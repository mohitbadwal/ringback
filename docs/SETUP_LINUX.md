# Ringback-Voice MCP — Linux Setup & Troubleshooting Guide

This is the Linux counterpart to [SETUP_MACOS.md](SETUP_MACOS.md). It also covers
**Windows via WSL2** (WSL2 is just Linux — follow this guide inside your WSL distro;
see [SETUP_WINDOWS.md](SETUP_WINDOWS.md)). For a zero-build option, use
[SETUP_DOCKER.md](SETUP_DOCKER.md).

> **Good news for Linux:** the macOS OpenSSL flat-namespace relink (the big macOS
> headache, §5.5 of the macOS guide) is **not needed** on Linux. The system loader
> resolves OpenSSL by full path, so pjproject builds and imports cleanly as-is.

---

## 1. What you're installing

- **pjproject 2.17 + pjsua2 Python bindings** (built from source — SIP + media).
- **whisper.cpp** (`whisper-cli` + `whisper-server`) — speech-to-text.
- **Piper** — neural text-to-speech (the cross-platform default voice) + one voice model.
- **ffmpeg** — audio format conversion.
- **Python deps** — `mcp`, `httpx`.

The engine runs **headless** — it never opens a local microphone or speaker (all audio is
WAV ↔ SIP/RTP), so no sound card or X server is required.

## 2. Prerequisites

- A Debian/Ubuntu (`apt`) or Fedora/RHEL (`dnf`) system, or another distro where you can
  install the equivalent packages.
- Python 3.10+.
- A free SIP account (e.g. https://subscribe.linphone.org).

## 3. Install (the happy path)

```bash
git clone <your-fork-or-this-repo> ringback && cd ringback
./setup-linux.sh
```

`setup-linux.sh` is idempotent and does all 7 steps: system packages → pjproject →
pjsua2 bindings → whisper.cpp → whisper models → Piper + voice → Python deps + a
`pjsua2 import OK` smoke-test. It creates `voice.env` from the template at the end.

If you prefer to do it by hand, the script is the canonical reference — read it top to
bottom; each `==>` step is self-contained.

## 4. Configure & register

```bash
# 1. Fill in your SIP creds (gitignored):
$EDITOR voice.env      # VOICE_SIP_ID, VOICE_SIP_USER, VOICE_SIP_PASS

# 2. Register the MCP server with the cross-platform launcher:
claude mcp add ringback-voice --scope user -- python3 "$PWD/run_voice_mcp.py"

# 3. Fresh session: "use ringback-voice to call me and say hi"
```

`run_voice_mcp.py` sets `LD_LIBRARY_PATH` + `PYTHONPATH` to the pjproject build and
re-execs `voice_mcp.py` — the Linux equivalent of `run_voice_mcp.sh`.

## 5. Verify

```bash
# pjsua2 import (under the launcher env):
PJPROJECT_DIR=~/build/pjproject-2.17 \
SWIG=$(ls -d ~/build/pjproject-2.17/pjsip-apps/src/swig/python/build/lib.* | head -1) \
PYTHONPATH=$SWIG \
LD_LIBRARY_PATH=~/build/pjproject-2.17/pjlib/lib:~/build/pjproject-2.17/pjmedia/lib:~/build/pjproject-2.17/pjsip/lib:~/build/pjproject-2.17/pjlib-util/lib:~/build/pjproject-2.17/pjnath/lib \
  python3 -c "import pjsua2; print('pjsua2 OK')"

# TTS (Piper) renders 16 kHz mono WAV:
python3 -c "import platform_compat as p; print(p.tts_engine()); p.synthesize_to_wav('hello from linux','/tmp/t.wav')" && file /tmp/t.wav

# Full offline suite (no phone):
python3 tests/run_all.py
```

## 6. Issues → fixes

### 6.1 `import pjsua2` → `ImportError: libpj… .so: cannot open shared object file`
`LD_LIBRARY_PATH` isn't pointing at the pjproject lib dirs. Use `run_voice_mcp.py` (it
sets this for you), or export `LD_LIBRARY_PATH` as in §5.

### 6.2 `make` (bindings) fails with C++11 errors
Same root cause as macOS §5.3 — pjsua2 headers need `-std=c++11`. The script already
exports `CFLAGS/CXXFLAGS="-std=c++11 …"`; if building by hand, do the same.

### 6.3 Calls connect but every reply is `[SILENCE]`/`[unclear]`
The whisper model is missing or `WHISPER_SERVER_BIN` isn't found. Confirm
`~/.whisper-models/ggml-base.en.bin` exists and `whisper-server` is on PATH
(`setup-linux.sh` copies it to `~/.local/bin`).

### 6.4 No TTS / `piper: command not found`
Piper is a `pip --user` install → ensure `~/.local/bin` is on PATH. Or set
`VOICE_TTS=espeak` to use `espeak-ng` (install `espeak-ng`), or point `VOICE_TTS_CMD`
at any tool that writes a WAV.

### 6.5 Watchdog never escalates (presence detection)
Idle is read via `xprintidle` (X11) or GNOME's Mutter over D-Bus. On Wayland there is no
standard idle source — set `RINGBACK_PRESENCE=absent` to force "away" (headless servers),
or `present` to disable auto-escalation. See `platform_compat.hid_idle_seconds()`.

### 6.6 No audio device errors
There shouldn't be any — the engine forces a NULL audio device on Linux
(`VOICE_NULL_AUDIO=auto`). If you see pjsua trying to open ALSA, set `VOICE_NULL_AUDIO=1`.

## 7. Key paths

| What | Default |
|---|---|
| pjproject build | `~/build/pjproject-2.17` |
| pjsua2 bindings | `~/build/pjproject-2.17/pjsip-apps/src/swig/python/build/lib.*` |
| whisper.cpp build | `~/build/whisper.cpp` |
| whisper models | `~/.whisper-models/` |
| Piper voice | `~/.piper-voices/en_US-lessac-medium.onnx` |
| tools (whisper-*, piper) | `~/.local/bin/` |
| SIP creds | `voice.env` (gitignored) |
