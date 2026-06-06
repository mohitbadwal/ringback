#!/usr/bin/env node
// ringback channel — an ADDITIVE second way to use ringback.
//
// It is a Claude Code "channel": an MCP server that pushes events INTO a running
// interactive session (and lets Claude reply back out). It does NOT replace or
// touch the existing ringback-alert / ringback-voice MCPs — it sits alongside.
//
// Flow (live-session, hands-free):
//   away → agent gets blocked → call-driver phones you → your spoken answer is
//   POSTed to this channel's /inject endpoint → channel emits a channel event →
//   the IDLE session wakes (~5s) and continues on your answer. If Claude wants to
//   say something back mid-task, it calls the `say` tool → routed back to the call.
//
// In the thin prototype the "call-driver" is faked by ./inject.sh (you type the
// answer). Real phone audio (reusing voice_agent.py's CallSession) comes later.
//
// Zero-dependency: plain Node ESM + built-in http. Speaks MCP over stdio
// (newline-delimited JSON-RPC) and runs a loopback HTTP listener for inject/say.
//
// Verified protocol bits (proven against Claude Code 2.1.x):
//   * capabilities.experimental['claude/channel'] = {}  → registers as a channel
//   * server->client notification  notifications/claude/channel {content, meta}
//     surfaces to Claude as  <channel source="ringback" ...>content</channel>
//   * `meta` keys become tag attributes; pass call_id so replies route back.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(HERE, '..');
// Where the call-driver lives. Defaults to this dir (repo-local). If this channel
// is installed as a COPIED plugin elsewhere, set RINGBACK_REPO to your ringback
// checkout so the call-driver (which needs voice_agent.py + the pjsua2 build) is found.
const CALL_HOME = process.env.RINGBACK_REPO
  ? path.join(path.resolve(process.env.RINGBACK_REPO), 'channel')
  : HERE;
const PORT = parseInt(process.env.RINGBACK_CHANNEL_PORT || '8790', 10);
const HOST = '127.0.0.1'; // loopback ONLY — never bind a public interface
const TOKEN = (process.env.RINGBACK_CHANNEL_TOKEN || '').trim(); // optional shared secret
const OUTBOUND = path.join(HERE, 'outbound.jsonl'); // what Claude `say`s back (the "call")
const DEBUG = path.join(HERE, 'ringback_channel.log');
const CALL_DRIVER_LOG = path.join(HERE, 'call_driver.log');
const LOCK = path.join(HERE, '.call_active'); // cross-process "a call is in flight"
let activeCall = null; // the spawned call-driver child, while a call is in flight

function callInFlight() {
  if (activeCall && activeCall.exitCode === null) return true;
  try { return (Date.now() - fs.statSync(LOCK).mtimeMs) < 900000; } catch { return false; }
}

const log = (m) => { try { fs.appendFileSync(DEBUG, `[${new Date().toISOString()}] ${m}\n`); } catch {} };

// ----- MCP stdio (server -> client notifications + request handling) --------- #
function send(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

// Emit a channel event into the live session. `content` is the body Claude reads;
// `meta` becomes <channel> tag attributes (call_id is the reply-routing handle).
function emitChannelEvent(content, meta = {}) {
  send({ jsonrpc: '2.0', method: 'notifications/claude/channel', params: { content, meta } });
  log(`EMIT channel event meta=${JSON.stringify(meta)} content=${JSON.stringify(content).slice(0, 200)}`);
}

let buf = '';
process.stdin.on('data', (d) => {
  buf += d.toString();
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { log('PARSE ERR ' + line); continue; }
    handleRpc(msg);
  }
});

function handleRpc(msg) {
  const { id, method, params } = msg;
  if (method === 'initialize') {
    send({ jsonrpc: '2.0', id, result: {
      protocolVersion: params?.protocolVersion || '2025-06-18',
      capabilities: { experimental: { 'claude/channel': {} }, tools: { listChanged: false } },
      serverInfo: { name: 'ringback', version: '0.1.0' },
      instructions:
        'The "ringback" channel lets you reach the user by PHONE when they are away from ' +
        'the keyboard. When you need a decision or answer from an away user, call the ' +
        '`ask_user_by_phone` tool with your question — it calls them and asks it out loud — ' +
        'then END YOUR TURN. Do NOT use interactive question menus or modal prompts, and do ' +
        'NOT just wait; the channel can only reach you once your turn has ended and you are ' +
        'idle. Their spoken answer then arrives as <channel source="ringback" call_id="...">' +
        'their answer</channel> — treat it as a direct reply and continue. While that call is ' +
        'still up you can speak back to them with the `say` tool (pass the same call_id from ' +
        'the event tag) to confirm, report progress, or ask a follow-up. Keep spoken lines ' +
        'short and plain.',
    }});
    return;
  }
  if (method === 'notifications/initialized') { return; }
  if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: [{
      name: 'ask_user_by_phone',
      description:
        'Phone the user and ask them a question out loud, when they are away from the ' +
        'keyboard and you need their decision or input to continue. This places a real call ' +
        'and asks your question; you should then END YOUR TURN. The user\'s spoken answer ' +
        'arrives back through this channel and wakes you to continue. Use this instead of ' +
        'an interactive menu/modal when the user may not be at the screen.',
      inputSchema: { type: 'object', properties: {
        question: { type: 'string', description: 'The question to ask the user out loud on the call. One clear sentence.' },
      }, required: ['question'] },
    }, {
      name: 'say',
      description:
        'Speak a short message back to the user on the active ringback phone call. ' +
        'Use this when you need to tell them something or ask a follow-up while they are ' +
        'on the line. Pass the call_id from the channel event you are responding to.',
      inputSchema: { type: 'object', properties: {
        text: { type: 'string', description: 'Short, plain-spoken message to say on the call.' },
        call_id: { type: 'string', description: 'The call_id from the channel event tag.' },
      }, required: ['text'] },
    }]}});
    return;
  }
  if (method === 'tools/call') {
    if (params?.name === 'ask_user_by_phone') {
      const args = params.arguments || {};
      const question = (args.question || '').toString().trim();
      if (!question) {
        send({ jsonrpc: '2.0', id, error: { code: -32602, message: 'question is required' } });
        return;
      }
      if (callInFlight()) {
        send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text',
          text: 'A phone call is already in progress — not starting another. Wait for the answer.' }] } });
        return;
      }
      try {
        // claim the lock BEFORE spawning so the Stop hook can't also dial (race-free)
        try { fs.writeFileSync(LOCK, JSON.stringify({ by: 'ask_user_by_phone', ts: Date.now() })); } catch {}
        const out = fs.openSync(CALL_DRIVER_LOG, 'a');
        // detached: the call outlives this tool call; the answer returns async via /inject.
        const child = spawn('bash',
          [path.join(CALL_HOME, 'run_call_driver.sh'),
           '--question', question, '--call-id', 'phone', '--say-wait', '45'],
          { cwd: path.join(CALL_HOME, '..'), env: { ...process.env, RINGBACK_CHANNEL_PORT: String(PORT) },
            detached: true, stdio: ['ignore', out, out] });
        child.on('error', (e) => log(`call-driver spawn error: ${e.message}`));
        child.unref();
        activeCall = child;
        log(`ask_user_by_phone: spawned call-driver pid=${child.pid} q=${JSON.stringify(question).slice(0, 200)}`);
        send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text:
          'Calling the user now and asking your question by phone. END YOUR TURN — do not wait ' +
          'or do anything else. Their spoken answer will arrive through this channel and wake ' +
          'you; then use the `say` tool to speak back on the call.' }] } });
      } catch (e) {
        send({ jsonrpc: '2.0', id, error: { code: -32000, message: 'failed to start call: ' + e.message } });
      }
      return;
    }
    if (params?.name === 'say') {
      const args = params.arguments || {};
      const rec = { ts: new Date().toISOString(), call_id: args.call_id || null, text: args.text || '' };
      try { fs.appendFileSync(OUTBOUND, JSON.stringify(rec) + '\n'); } catch {}
      // In the prototype this just logs; the real call-driver tails OUTBOUND (or
      // gets an HTTP callback) and speaks it on the live call.
      log(`SAY → ${JSON.stringify(rec)}`);
      process.stderr.write(`[ringback say] ${rec.text}\n`);
      send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: 'spoken on call' }] } });
      return;
    }
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: `unknown tool: ${params?.name}` } });
    return;
  }
  if (method === 'ping') { send({ jsonrpc: '2.0', id, result: {} }); return; }
  if (id !== undefined && id !== null) { send({ jsonrpc: '2.0', id, result: {} }); }
}

// ----- loopback HTTP: the call-driver (or ./inject.sh) posts answers here ----- #
function readBody(req) {
  return new Promise((resolve) => {
    let b = '';
    req.on('data', (c) => { b += c; if (b.length > 1e6) req.destroy(); });
    req.on('end', () => resolve(b));
  });
}

const server = http.createServer(async (req, res) => {
  const json = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
  if (req.method === 'GET' && req.url === '/health') return json(200, { ok: true, port: PORT });
  if (req.method === 'POST' && req.url === '/inject') {
    if (TOKEN && req.headers['x-ringback-token'] !== TOKEN) return json(401, { error: 'bad token' });
    let body;
    try { body = JSON.parse(await readBody(req) || '{}'); } catch { return json(400, { error: 'bad json' }); }
    const content = (body.content || '').toString();
    if (!content) return json(400, { error: 'content required' });
    const meta = { call_id: (body.call_id || 'local').toString() };
    if (body.severity) meta.severity = body.severity.toString();
    emitChannelEvent(content, meta);
    return json(200, { ok: true, injected: content.length });
  }
  json(404, { error: 'not found' });
});

server.on('error', (e) => { log(`HTTP ERR ${e.message}`); process.stderr.write(`[ringback channel] HTTP error: ${e.message}\n`); });
server.listen(PORT, HOST, () => log(`channel up: stdio MCP + http://${HOST}:${PORT} (inject/health)`));
log(`channel started pid=${process.pid}`);
