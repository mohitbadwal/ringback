# ringback — Claude Code plugin

One-command install of the **ringback** MCP servers (`ringback-voice` + `ringback-alert`)
plus the **watchdog** skill. Your AI agent can call your phone for a two-way voice
conversation and send tiered alerts, over a free self-hosted SIP line.

This plugin runs the engine in **Docker**, so there is no local pjsua2/whisper build. You
need Docker installed and a free [Linphone](https://subscribe.linphone.org) SIP account.

## Install

```text
/plugin marketplace add mohitbadwal/ringback
/plugin install ringback@ringback
```

Claude Code then prompts for your config (SIP address, username, password; optional
caller-ID name and alert backends). The password and tokens are stored in your OS keychain,
never in a file. On first call the image is pulled automatically (one-time download).

After installing, start a fresh session and tell the agent to call you to confirm it works.

## What you get

| Server / skill | Purpose |
|---|---|
| `ringback-voice` | A real two-way phone conversation: it rings your phone, speaks, transcribes your reply, and answers in speech. Supports barge-in. |
| `ringback-alert` | Fire-and-forget tiered alerts via ntfy / Pushover push. |
| `watchdog` skill | Presence-aware escalation: only reaches you when you are actually away from the keyboard. |

## Requirements

- **Docker** — Docker Desktop on macOS/Windows, or docker engine on Linux.
- **A Linphone SIP account** — free, from https://subscribe.linphone.org, with the Linphone
  app installed on your phone (that is the line the agent calls).

## Networking note (RTP)

The launcher picks the right Docker networking for your OS automatically: host networking on
Linux/WSL2, and a pinned, published UDP port on Docker Desktop (macOS/Windows). If you hit
one-way audio on Docker Desktop, prefer WSL2 or the native install. See
[docs/SETUP_DOCKER.md](../docs/SETUP_DOCKER.md) for details.

## No Docker?

Use the native install instead (compiles the engine locally): see the repository
[README](../README.md) and the per-OS setup guides under [docs/](../docs).

---

The `skills/watchdog/` directory here mirrors the canonical skill at the repository root
(`skills/watchdog/`); keep them in sync when the skill changes.
