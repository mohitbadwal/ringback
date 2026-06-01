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

It ships two MCP servers, `ringback-alert` and `ringback-voice`:

> **Platform:** macOS only. The voice feature uses Apple's `say` (TTS) and CoreAudio via pjsua2.

| Server | Tools | What it does |
|---|---|---|
| **ringback-alert** | `alert_me`, `alert_test`, `alert_status` | Fire-and-forget notification: a loud push (ntfy / Pushover) and/or a SIP ring + chat message. |
| **ringback-voice** | `call_start`, `converse`, `get_conversation`, `call_end`, … | A real **two-way phone conversation**. Rings your phone; you talk, it transcribes you, the LLM replies in speech. Supports **barge-in** (talk over it and it stops). |

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
./setup.sh        # installs EVERYTHING (toolchain, pjsua2, whisper model, deps) + creates voice.env
# edit voice.env → add your 3 SIP vars (free account: https://subscribe.linphone.org), then:
claude mcp add ringback-voice --scope user -- "$PWD/run_voice_mcp.sh"
```

Full walkthrough + env-var reference: **[Set up ringback-voice](#set-up-ringback-voice--4-steps)** below.

---

## Honest caveats (read first)

- **macOS only.** `say` + CoreAudio. No Linux/Windows path today.
- **Not ChatGPT-realtime.** The voice loop is record → whisper STT → LLM → `say` TTS, so expect **~1–2 s per turn**. It's a reliable walkie-talkie with barge-in, not a streaming realtime voice.
- **The voice feature depends on GPL software** (pjproject/pjsua2). This repo is Apache-2.0, but redistributing a bundle that links pjsua2 carries GPL obligations — see [`NOTICE`](NOTICE). The ringback-alert server is unaffected.
- **Your Mac must be awake and online**, and for a voice call a Claude session must be live (it's the brain) for the duration.
- **Barge-in assumes low acoustic echo** (handset or headset). On speakerphone, the TTS can echo into the mic and false-trigger "interruption." There's no echo cancellation in this path.
- **iOS push reality:** a self-hosted/free push can't truly pierce Focus/Silent on iPhone except via Pushover's Critical Alerts (paid) — see ringback-alert notes below.

---

## Architecture

```
  LLM (Claude)  ──MCP tools──▶  ringback-voice server (Python)
                                   │  call_start / converse / listen / speak
                                   ▼
                 pjsua2 (SIP+SRTP, built from source)  ──▶  Linphone SIP server
                   │  say → ffmpeg → WAV  (speak)              │ APNs VoIP push
                   │  record → whisper.cpp (listen)            ▼
                   └───────────────────────────────────▶  your iPhone rings
```

ringback-alert is simpler: it shells out to `ntfy`/Pushover HTTP and/or `baresip` for a SIP ring + chat message.

---

## Prerequisites

- macOS (Apple Silicon or Intel) with [Homebrew](https://brew.sh)
- A free **Linphone** SIP account (`sip.linphone.org`) and the **Linphone iOS app** (for the ring/voice features)
- Python 3.10+ (the pjsua2 bindings are built against whichever `python3` you point at)

---

## Set up ringback-voice — 4 steps

**1. Clone + install everything:**
```bash
git clone https://github.com/mohitbadwal/ringback && cd ringback
./setup.sh
```
`setup.sh` installs the toolchain, **compiles pjsua2 from source** (~20–30 min — no Homebrew formula exists for the bindings), relinks the pjproject dylibs to a two-level OpenSSL namespace (the macOS fix that makes SIP/SRTP work), downloads the whisper model, installs deps, **and creates `voice.env` for you**. Safe to re-run. (Override `PYTHON_BIN` / `PJPROJECT_DIR` / `WHISPER_MODEL_NAME` if your layout differs.)

> **Hit a snag on macOS?** [`docs/SETUP_MACOS.md`](docs/SETUP_MACOS.md) is a field-tested root-cause + troubleshooting guide (build target, the OpenSSL flat-namespace fix, whisper model, symptom→fix table).

**2. Get a free SIP account** (this is the phone that rings):
- Sign up at **<https://subscribe.linphone.org>** (or tap *Create account* in the Linphone app). You get a **username** and **password**; your address is `sip:<username>@sip.linphone.org`.
- Install the **Linphone app on your iPhone**, sign in, and confirm it shows **Connected**.

**3. Fill in `voice.env`** (already created by setup.sh — just edit it). Only **three** vars are required:
```bash
export VOICE_SIP_ID="sip:yourname@sip.linphone.org"
export VOICE_SIP_USER="yourname"
export VOICE_SIP_PASS="your-password"
```

Full variable reference:

| Variable | Required | Default | What it is |
|---|:---:|---|---|
| `VOICE_SIP_ID` | ✅ | — | Your SIP address, e.g. `sip:you@sip.linphone.org` |
| `VOICE_SIP_USER` | ✅ | — | SIP username (the part before `@`) |
| `VOICE_SIP_PASS` | ✅ | — | Your SIP password |
| `VOICE_SIP_CALLEE` | — | = `VOICE_SIP_ID` | Address to call (normally yourself) |
| `VOICE_SIP_PROXY` | — | `sip:sip.linphone.org;transport=tls` | SIP registrar/proxy |
| `WHISPER_MODEL` | — | `~/.whisper-models/ggml-small.en.bin` | STT model: `base.en` (fast) · `small.en` (default) · `medium.en` (accurate) |
| `PJPROJECT_DIR` | — | `~/build/pjproject-2.17` | pjsua2 build dir (auto-detected) |
| `PYTHON_BIN` | — | `$(command -v python3)` | Python that has pjsua2 (auto-detected) |
| `OPENSSL_PREFIX` | — | `$(brew --prefix openssl@3)` | OpenSSL libs (auto-detected) |

**4. Register + test:**
```bash
claude mcp add ringback-voice --scope user -- "$PWD/run_voice_mcp.sh"
```
Then in a **fresh** Claude session say: *"Use ringback-voice to call me and say hello."* Your phone should ring.

> **Claude Desktop** instead of Code? Add this to `~/Library/Application Support/Claude/claude_desktop_config.json` (absolute path required; restart the app):
> ```json
> { "mcpServers": { "ringback-voice": { "command": "/absolute/path/to/ringback/run_voice_mcp.sh" } } }
> ```

---

## Set up ringback-alert (optional)

ringback-alert reads its config from the **MCP client's `env` block** (no file to source). Register it with the channels you want:

```bash
# Claude Code
claude mcp add ringback-alert --scope user \
  --env ALERT_CHANNEL=ntfy \
  --env NTFY_URL=https://ntfy.sh/your-long-random-topic \
  -- /opt/homebrew/bin/uv --directory "$PWD" run server.py
```
See `alert.env.example` for all variables (ntfy / Pushover / SIP ring). Use a **long random** ntfy topic — anyone who knows it can read/publish.

---

## Using ringback-voice (the conversation)

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

## Using ringback-alert (notifications)

`alert_me(message, severity, title)` with `severity` = `info` | `warn` | `critical`. Channels via `ALERT_CHANNEL` (comma list of `ntfy`, `pushover`, `call`):

- **ntfy** — free push; loud but does not pierce iOS Focus/Silent unless whitelisted per-Focus.
- **Pushover** — $5 one-time; true iOS **Critical Alerts** (pierces Focus/Silent, repeats until acknowledged) at `critical`.
- **call** — free SIP ring + Linphone chat message via baresip; rings full-screen, only at `critical` by default.

A built-in rate-limit guard (default 5/60s, per-process) stops a misfiring caller from spamming you.

---

## Bundled skill: watchdog

[`skills/watchdog/`](skills/watchdog/SKILL.md) is a ready-to-use Claude skill built on these servers. It watches a condition you give it (a CI run, a deploy, a pod, a file) and **escalates only when you're actually away** from the laptop — chat status → chat warning → `ringback-alert` push → `ringback-voice` call — judged by macOS HID idle time. It never interrupts you while you're typing, and de-escalates the moment you touch the keyboard.

```bash
cp -r skills/watchdog ~/.claude/skills/watchdog   # install for Claude Code
```
Then: `/watchdog <what to watch> | priority=<low|medium|critical>` — `low` = chat only, `medium` = may send a phone alert, `critical` = may place a call. Full design in [`skills/watchdog/SKILL.md`](skills/watchdog/SKILL.md).

---

## Security

- **SIP credentials** live only in your local, gitignored `voice.env` (and the baresip `accounts` file for ringback-alert) — never in the repo or the MCP client config when you can avoid it.
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
