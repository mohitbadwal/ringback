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

## Components
- `ringback_channel.mjs` — the channel MCP (zero-dep Node). Loopback HTTP `/inject`
  (answers in) + tools `ask_user_by_phone` (start a call to ask the away user) and
  `say` (speak back on the live call, logged to `outbound.jsonl`).
- `call_driver.py` — the **real** call-driver: dials you (imports `voice_agent.CallSession`
  unchanged), speaks the question, transcribes your reply, POSTs it to `/inject`, then
  relays the session's `say` replies back onto the call. Run via `run_call_driver.sh`.
- `run_session.sh` — launches a session with the channel attached, in bypass posture.
- `inject.sh` — no-phone stand-in for the call-driver: type what you'd have said, to test
  the channel half without dialing.
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

## Real phone flow (autonomous)
With the call-driver wired, the agent reaches you **by phone on its own**:

```
session (run_session.sh) → agent needs a decision, you're away →
  agent calls ask_user_by_phone("staging or production?") → channel spawns call_driver.py →
  📞 your phone rings → you answer + speak → transcribed → POST /inject →
  idle session WAKES → decides → say(...) → call_driver speaks it back on the call
```

Verified end-to-end with a real call: the agent dialed unprompted, the spoken answer woke
the session, and it confirmed the choice out loud — no manual orchestration.

**Requirement:** the call-driver needs your SIP password to place the call. Put
`VOICE_SIP_PASS` in `voice.env` (gitignored) so `run_call_driver.sh` picks it up, **or**
export it in the environment you launch `run_session.sh` from (the channel passes its env
to the spawned call-driver). Same SIP/Linphone account `ringback-voice` uses.

## Fully automatic (Stop hook)
The agent reaching you via `ask_user_by_phone` is the precise path. As a **backstop**,
`stop_hook.py` (wired in the repo `.claude/settings.json`) fires on every turn end and
phones you **even if the agent didn't call the tool** — but only when ALL of these hold:

1. ringback is configured (SIP creds present) — else it's completely inert,
2. you're **away** (macOS HID idle > `RINGBACK_AWAY_IDLE_SEC`, default 300s),
3. no call is already active (a `channel/.call_active` lockfile coordinates with `ask_user_by_phone`),
4. the agent's last message is a question, and
5. it hasn't already handled this turn.

It reads the question from the transcript and dials via the same call-driver. To disable,
remove the `Stop` hook from `.claude/settings.json`. Test without dialing:
`RINGBACK_HOOK_DRYRUN=1 RINGBACK_AWAY_IDLE_SEC=0` (logs the decision to `channel/stop_hook.log`).

## Install as a plugin (scaffold — see caveat)
A plugin scaffold is included (`channel/.claude-plugin/plugin.json`, `channel/.mcp.json`,
and a local `/.claude-plugin/marketplace.json`) so ringback can be distributed and launched
as `--channels plugin:ringback@<marketplace>`:
```bash
# from a session, interactively:
/plugin marketplace add /Users/mohitbadwal/PycharmProjects/phone-alert-mcp
/plugin install ringback@ringback-local
```
**Caveat (verified on the current research-preview build):** custom channels are gated by an
*approved-channels allowlist*, so `--channels plugin:…` still shows the "development channels"
warning (or refuses) unless the channel is allowlisted. Until then, **`run_session.sh` (dev flag)
is the working launch.** Also, a marketplace install *copies* the plugin — set `RINGBACK_REPO`
to your ringback checkout so the copied channel can still find the call-driver
(`voice_agent.py` + the pjsua2 build, which don't travel with the copy).

## Notes
- The HTTP endpoint binds **127.0.0.1 only**. Set `RINGBACK_CHANNEL_TOKEN` to require an
  `X-Ringback-Token` header on `/inject`. Override the port with `RINGBACK_CHANNEL_PORT`.
- A channel must be attached **at launch** (`--channels` / dev flag) — you can't bolt it
  onto an already-running session. Use a shell alias, or package as a plugin to launch
  with `--channels plugin:ringback@<marketplace>` and drop the dev flag/warning.
- One session per port (the channel runs inside that session's MCP subprocess).
