# ringback channel (additive — Way 2)

A **second, optional** way to use ringback. It does **not** change the existing
`ringback-alert` / `ringback-voice` MCPs or the watchdog skill — those keep working
exactly as before. This adds a Claude Code [*channel*](https://code.claude.com/docs/en/channels):
an MCP server that pushes events **into a running interactive session** and lets
Claude reply back out.

## Why
The current way: the agent, *while it's actively running*, decides to call/alert you.

This way: you're **away** and the session goes **idle waiting on you** — the channel
lets your phoned-in answer land **back inside that same session**, which wakes (~5s)
and continues. Your phone becomes a two-way channel into the live session.

```
away → agent blocks/asks → call-driver phones you → you answer →
  POST /inject → channel event → idle session WAKES → continues on your answer
  (Claude can `say` back mid-task → routed to the call)
```

> Proven: an idle interactive Claude Code session **does** wake on an inbound channel
> event and act on it (~5–6s latency), verified against Claude Code 2.1.x.

## Thin prototype (what's here now)
Real phone audio comes later (it'll reuse `voice_agent.py`'s `CallSession`). For now the
call-driver is faked by `inject.sh` so we can prove the *session-wakes-and-continues* loop:

- `ringback_channel.mjs` — the channel MCP (zero-dep Node). Loopback HTTP `/inject`
  (answers in) + a `say` tool (Claude's words out, logged to `outbound.jsonl`).
- `inject.sh` — stand-in for the call-driver: type what you'd have said on the phone.
- `ringback.mcp.json` — registers the channel.

## Two requirements the loop needs (verified the hard way)
1. **Bypass posture (option A).** A woken away-session must be able to *act* on your
   answer without a permission prompt nobody is there to approve. So launch with
   `--dangerously-skip-permissions` (baked into `run_session.sh`). Without it, the
   session still *wakes* but *stalls* on the first tool approval.
2. **Block by ending the turn, not via a modal.** The channel can only reach the
   session when it's **idle at the prompt**. If the agent asks via the interactive
   `AskUserQuestion` menu (or any permission modal), it's parked *inside* that dialog —
   not idle — and the channel can't deliver. So the agent must ask as **plain text and
   end its turn**. The channel's `instructions` now steer it that way.

## Try it
**Terminal A** — start a session with the channel attached, in bypass posture:
```bash
./channel/run_session.sh
```
Accept the folder-trust + "loading development channels" prompts. Then give Claude a
task that makes it pause and wait on you — phrased so it **asks as plain text and stops**
(e.g. "ask me which option to pick *as a plain message and end your turn*, then wait").
Let it go idle.

**Terminal B** — answer "by phone" (no keystroke in Terminal A):
```bash
./channel/inject.sh "go with option B"
```
Terminal A should wake on its own and continue with your answer. When Claude uses `say`,
watch it here:
```bash
tail -f channel/outbound.jsonl
```

## Notes
- The HTTP endpoint binds **127.0.0.1 only**. Set `RINGBACK_CHANNEL_TOKEN` to require an
  `X-Ringback-Token` header on `/inject`. Override the port with `RINGBACK_CHANNEL_PORT`.
- A channel must be attached **at launch** (`--channels` / dev flag) — you can't bolt it
  onto an already-running session. Use a shell alias, or package as a plugin to launch
  with `--channels plugin:ringback@<marketplace>` and drop the dev flag/warning.
- One session per port (the channel runs inside that session's MCP subprocess).
