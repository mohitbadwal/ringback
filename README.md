# ringback

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#prerequisites)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io)

> **Your AI agent can call your phone — and actually talk to you.**

**ringback** gives an LLM (Claude, or any MCP client) tools to **reach you on your phone** — from a one-way "fierce" alert all the way to a **live, interruptible voice conversation** — using only free, self-hosted pieces. No paid telephony. No extra API key for the conversation: the model already driving the MCP *is* the voice on the line.

**Highlights**
- 📞 **Two-way voice calls** — the agent rings your phone, you talk, it transcribes you and replies in speech. **Barge-in**: talk over it and it stops.
- 🔔 **Tiered alerts** — a loud push (ntfy / Pushover) or a real SIP ring + chat message, fired only when *the LLM* judges it urgent.
- 🆓 **Free & self-hosted** — pjsua2 + whisper.cpp + macOS `say` + a free Linphone SIP account. No Twilio, no per-minute fees.
- 🧠 **No conversation API key** — the calling model is the brain; these tools are just its ears and mouth.

It ships two MCP servers, `phone-alert` and `phone-voice`:

> **Platform:** macOS only. The voice feature uses Apple's `say` (TTS) and CoreAudio via pjsua2.

| Server | Tools | What it does |
|---|---|---|
| **phone-alert** | `alert_me`, `alert_test`, `alert_status` | Fire-and-forget notification: a loud push (ntfy / Pushover) and/or a SIP ring + chat message. |
| **phone-voice** | `call_start`, `converse`, `get_conversation`, `call_end`, … | A real **two-way phone conversation**. Rings your phone; you talk, it transcribes you, the LLM replies in speech. Supports **barge-in** (talk over it and it stops). |

The criteria for *when* to contact you live in the tool descriptions — the calling LLM decides. These servers are just the mechanism.

### What a call looks like

```
agent → call_start("Your nightly deploy failed — want me to walk you through it?")
   📞 your phone rings; you pick up and hear the line
you   → "yeah, which step broke?"
agent → "The database migration. I can roll it back and retry — want that?"
you   → "yes, do it"            ← you can also just talk over the agent to interrupt
agent → call_end()
```

The LLM calls `call_start` once, then `converse(...)` for each turn. Plain alerts are even simpler: one `alert_me(...)` call.

---

## Quick start

```bash
git clone https://github.com/mohitbadwal/ringback && cd ringback
./setup.sh                         # installs EVERYTHING: toolchain, pjsua2, whisper model, deps
cp voice.env.example voice.env     # add your free Linphone SIP account
```

Then register with your MCP client (see [Register](#register-with-your-mcp-client)). Run `./setup.sh` **before** registering — it's the one-shot installer.

---

## Honest caveats (read first)

- **macOS only.** `say` + CoreAudio. No Linux/Windows path today.
- **Not ChatGPT-realtime.** The voice loop is record → whisper STT → LLM → `say` TTS, so expect **~1–2 s per turn**. It's a reliable walkie-talkie with barge-in, not a streaming realtime voice.
- **The voice feature depends on GPL software** (pjproject/pjsua2). This repo is Apache-2.0, but redistributing a bundle that links pjsua2 carries GPL obligations — see [`NOTICE`](NOTICE). The phone-alert server is unaffected.
- **Your Mac must be awake and online**, and for a voice call a Claude session must be live (it's the brain) for the duration.
- **Barge-in assumes low acoustic echo** (handset or headset). On speakerphone, the TTS can echo into the mic and false-trigger "interruption." There's no echo cancellation in this path.
- **iOS push reality:** a self-hosted/free push can't truly pierce Focus/Silent on iPhone except via Pushover's Critical Alerts (paid) — see phone-alert notes below.

---

## Architecture

```
  LLM (Claude)  ──MCP tools──▶  phone-voice server (Python)
                                   │  call_start / converse / listen / speak
                                   ▼
                 pjsua2 (SIP+SRTP, built from source)  ──▶  Linphone SIP server
                   │  say → ffmpeg → WAV  (speak)              │ APNs VoIP push
                   │  record → whisper.cpp (listen)            ▼
                   └───────────────────────────────────▶  your iPhone rings
```

phone-alert is simpler: it shells out to `ntfy`/Pushover HTTP and/or `baresip` for a SIP ring + chat message.

---

## Prerequisites

- macOS (Apple Silicon or Intel) with [Homebrew](https://brew.sh)
- A free **Linphone** SIP account (`sip.linphone.org`) and the **Linphone iOS app** (for the ring/voice features)
- Python 3.10+ (the pjsua2 bindings are built against whichever `python3` you point at)

---

## Install

```bash
git clone https://github.com/mohitbadwal/ringback && cd ringback
./setup.sh
```

`setup.sh` installs the toolchain (swig, openssl@3, ffmpeg, whisper-cpp, baresip), **compiles pjproject + the pjsua2 Python bindings from source** (~20–30 min; there is no Homebrew formula for the bindings), downloads a whisper model, and installs the Python deps. It's safe to re-run.

> Override `PYTHON_BIN`, `PJPROJECT_DIR`, or `WHISPER_MODEL_NAME` as env vars if your layout differs.

---

## Configure

**phone-voice** — copy the template and fill in your SIP account:
```bash
cp voice.env.example voice.env      # gitignored; holds your SIP identity + password
```

**phone-alert** — this server takes its config from the **MCP client's `env` block** (it reads `os.environ` directly; there is no dotenv auto-loading). So you don't create a file — instead, copy the variables you need from `alert.env.example` into your `claude mcp add --env …` flags or the Desktop config `env` object (see the registration section below). Use a **long random** ntfy topic (anyone who knows it can read/publish).

---

## Register with your MCP client

**Claude Code** (user scope = available in every project):
```bash
# voice
claude mcp add phone-voice --scope user -- "$PWD/run_voice_mcp.sh"

# alert (env passed inline, or via your shell)
claude mcp add phone-alert --scope user \
  --env ALERT_CHANNEL=ntfy \
  --env NTFY_URL=https://ntfy.sh/your-random-topic \
  -- /opt/homebrew/bin/uv --directory "$PWD" run server.py
```

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "phone-voice": { "command": "/absolute/path/to/run_voice_mcp.sh" },
    "phone-alert": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/absolute/path/to/repo", "run", "server.py"],
      "env": { "ALERT_CHANNEL": "ntfy", "NTFY_URL": "https://ntfy.sh/your-random-topic" }
    }
  }
}
```
Use absolute paths (Desktop launches MCP servers with a minimal PATH). Restart the app.

---

## Using phone-voice (the conversation)

The LLM drives a simple loop:

```
reply = call_start("Hi, it's your assistant — your deploy failed. Want details?")
# rings the phone, speaks the line, returns the user's first words
reply = converse("It failed on the database migration step. Want me to retry it?")
# speaks AND listens in one interruptible turn
... repeat converse() each turn ...
call_end()   # when the user says "bye" / hangs up
```

- **`converse(text)`** speaks while listening. If you **talk over it**, it stops immediately and tells the LLM how far it got and what you said (barge-in).
- **`get_conversation()`** returns the full transcript so far — both sides, plus where it got interrupted.
- TTS reads text literally, so the tool descriptions instruct the model to speak **plain-language summaries**, never raw logs/codes — those go via `alert_me` as text instead.

Whisper model accuracy/speed trade-off (set `WHISPER_MODEL`): `base.en` (fast/rough) → `small.en` (balanced, default) → `medium.en` (most accurate/slow).

---

## Using phone-alert (notifications)

`alert_me(message, severity, title)` with `severity` = `info` | `warn` | `critical`. Channels via `ALERT_CHANNEL` (comma list of `ntfy`, `pushover`, `call`):

- **ntfy** — free push; loud but does not pierce iOS Focus/Silent unless whitelisted per-Focus.
- **Pushover** — $5 one-time; true iOS **Critical Alerts** (pierces Focus/Silent, repeats until acknowledged) at `critical`.
- **call** — free SIP ring + Linphone chat message via baresip; rings full-screen, only at `critical` by default.

A built-in rate-limit guard (default 5/60s, per-process) stops a misfiring caller from spamming you.

---

## Security

- **SIP credentials** live only in your local, gitignored `voice.env` (and the baresip `accounts` file for phone-alert) — never in the repo or the MCP client config when you can avoid it.
- The voice server only ever **calls the single SIP URI you configure** — it cannot dial arbitrary numbers.
- Treat ntfy topics as secrets (use a long random topic); don't put sensitive detail in alert bodies on public ntfy.sh.
- See [`NOTICE`](NOTICE) for the GPL/pjproject licensing caveat before redistributing.

---

## License

Apache-2.0 (see [`LICENSE`](LICENSE)), with an important GPL caveat for the voice
component's pjproject dependency — see [`NOTICE`](NOTICE).

## Credits

Built on [pjproject/pjsua2](https://www.pjsip.org), [whisper.cpp](https://github.com/ggerganov/whisper.cpp),
[baresip](https://github.com/baresip/baresip), [ntfy](https://ntfy.sh), and
[Linphone](https://www.linphone.org).
