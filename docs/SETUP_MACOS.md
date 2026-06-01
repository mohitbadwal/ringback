# Ringback-Voice MCP — macOS Setup & Troubleshooting Guide

A field-tested guide to getting the **ringback-voice** MCP server working on macOS
(Apple Silicon), contributed from a real end-to-end debugging session.

> **Note:** the current `setup.sh` already bakes in two of the fixes below — it builds
> the bindings with the correct `make`/`-std=c++11` (§5.2/5.3) and verifies the whisper
> model (§5.7). The **OpenSSL flat-namespace relink (§5.5) is NOT auto-run** — its link
> deps vary by machine, so it's a manual step: run `./fix_macos_twolevel.sh` if calls
> fail with `-32000` or a segfault. This doc is the authoritative root-cause reference.

> TL;DR for the impatient: run `./setup.sh`, then (if calls fail) `./fix_macos_twolevel.sh`,
> fill in `voice.env`, register the MCP with the **correct absolute path**, and test.
> The "why" for each step is in [§5](#5-issues-we-hit--root-causes--fixes).

---

## 1. What you're installing

```
Claude (the model)  ──MCP/stdio──►  voice_mcp.py
                                        │  (FastMCP server: call_start / converse / listen / speak / call_end)
                                        ▼
                                   voice_agent.py
                                   ┌─────────────┬───────────────┬──────────────────┐
                                   │  pjsua2      │  macOS `say`  │  whisper.cpp     │
                                   │  (SIP/VoIP)  │  (TTS, out)   │  (STT, in)       │
                                   └─────────────┴───────────────┴──────────────────┘
                                        │
                                        ▼  SIP over TLS + SRTP
                                   sip.linphone.org  ──►  your phone (Linphone app)
```

- **pjsua2** — Python bindings for **pjproject** (a C SIP/VoIP stack). *Not* available
  via pip or Homebrew; it must be **compiled from source** (the slow part of setup).
- **macOS `say` + ffmpeg** — text-to-speech (what Claude says to you).
- **whisper.cpp** (`whisper-cli`) + a `ggml-*.bin` model — speech-to-text (what you say back).
- A free **Linphone SIP account** (https://subscribe.linphone.org) is the phone line.

---

## 2. Prerequisites

- macOS (this guide is for **Apple Silicon / arm64**; Intel needs arch tweaks).
- **Homebrew**.
- A **Python 3.10+** that you'll run the server with. ⚠️ **Which Python matters** — see
  [§5.5](#55-the-big-one--openssl-flat-namespace-collision). The launcher uses
  `command -v python3`; whatever that resolves to is what you build the bindings for.
- A free Linphone account: username, password, and your SIP address
  `sip:<username>@sip.linphone.org`.
- ~30 minutes (pjproject compiles for 20–30 min) and ~500 MB disk for the whisper model.

---

## 3. Clean install (the corrected happy path)

These are the exact steps that produce a **working** install. They incorporate every
fix from [§5](#5-issues-we-hit--root-causes--fixes); follow them in order.

### Step 0 — Clone
```bash
git clone https://github.com/mohitbadwal/ringback && cd ringback
```

### Step 1 — Run setup.sh (it will likely stop early — that's expected)
```bash
./setup.sh
```
`setup.sh` installs the toolchain (swig, openssl@3, ffmpeg, whisper-cpp, …), fetches
and compiles pjproject, **then tries to build the Python bindings with `make python`**.

> ⚠️ **Known bug:** on pjproject 2.17 the bindings Makefile target is `make` (default
> `all`), **not `make python`**. So `setup.sh` dies here under `set -e`, and because it
> dies, the **two steps after it never run**: the whisper model is never downloaded and
> `voice.env` is never created. See [§5.2](#52-setupsh-dies-at-make-python).

If `setup.sh` finishes cleanly on your version, skip to Step 4. If it dies at
"Building pjsua2 Python bindings", continue with Steps 2–3 to finish the job by hand.

### Step 2 — Build the pjsua2 Python bindings (the manual version of step 4)
```bash
PJ="$HOME/build/pjproject-2.17"
OPENSSL_PREFIX="$(brew --prefix openssl@3)"
cd "$PJ/pjsip-apps/src/swig/python"

# -std=c++11 is REQUIRED: pjsua2 headers use rvalue refs / nullptr; without it the
# compile fails with "rvalue references are a C++11 extension" (see §5.3).
CFLAGS="-std=c++11 -I$OPENSSL_PREFIX/include -fPIC -O2" \
LDFLAGS="-L$OPENSSL_PREFIX/lib" \
PATH="$(dirname "$(command -v python3)"):$PATH" \
  make            # NOT `make python`

ls build/lib.*/_pjsua2*.so   # confirm the extension built
```

### Step 3 — Finish the steps setup.sh skipped (deps, whisper model, voice.env)
```bash
cd /path/to/ringback

# 3a. Python deps for the MCP server (use the SAME python the launcher will use)
python3 -m pip install "mcp>=1.2.0" httpx \
  || python3 -m pip install --break-system-packages "mcp>=1.2.0" httpx

# 3b. Whisper model (~481 MB). Without this, calls connect but every reply is
#     transcribed as "[SILENCE]" / "[unclear]" — see §5.7.
mkdir -p ~/.whisper-models
curl -fL --progress-bar \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin" \
  -o ~/.whisper-models/ggml-small.en.bin

# 3c. Create voice.env from the template
cp voice.env.example voice.env
```

### Step 4 — Fix the OpenSSL flat-namespace problem (the crucial macOS fix)
This is the step that makes calls actually work instead of crashing. Run the helper
shipped in this repo (it relinks 4 dylibs to a two-level namespace — full explanation
in [§5.5](#55-the-big-one--openssl-flat-namespace-collision)):
```bash
./fix_macos_twolevel.sh
```
Verify:
```bash
nm -m "$HOME/build/pjproject-2.17/pjlib/lib/libpj.dylib.2" | grep 'SSL_CTX_new'
# Expected: (undefined) external _SSL_CTX_new (from libssl)   ← bound, not flat
```

> **Building from scratch and want to avoid the relink entirely?** Edit
> `pjproject-2.17/build/rules.mak` **before** compiling and change line 18 from
> `SHLIB_OPT := -dynamiclib -undefined dynamic_lookup -flat_namespace`
> to `SHLIB_OPT := -dynamiclib -twolevel_namespace`. Then the normal build produces
> correct two-level libs and `fix_macos_twolevel.sh` is unnecessary. (We applied the
> relink because pjproject was already built; both routes give the same result.)

### Step 5 — Fill in your SIP credentials
Edit `voice.env` and set the three required vars:
```bash
export VOICE_SIP_ID="sip:<yourname>@sip.linphone.org"
export VOICE_SIP_USER="<yourname>"
export VOICE_SIP_PASS="<your-linphone-password>"
```
`voice.env` is gitignored — your password won't be committed. Verify with
`git check-ignore voice.env`.

### Step 6 — Register the MCP server (mind the path!)
```bash
claude mcp add ringback-voice --scope user -- "$PWD/run_voice_mcp.sh"
```
⚠️ Use the **absolute path to `run_voice_mcp.sh` inside the `ringback/` directory**.
Pointing at the wrong directory gives `ENOENT` / "failed to reconnect" — see
[§5.1](#51-mcp-enoent--wrong-launcher-path-manual-setup-error).

---

## 4. Verify it works

In order of increasing confidence:

```bash
# A. Server starts & handshakes (lazy init — no SIP/audio yet)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' \
  | ./run_voice_mcp.sh
# Expect a JSON result with "serverInfo":{"name":"ringback-voice",...}

# B. SRTP + pjsua2 init (the call that used to fail)
#    Run inside the launcher env; should print LIBINIT_OK with no SRTP error.

# C. whisper transcribes
say -o /tmp/k.aiff "this is a transcription test"
ffmpeg -y -loglevel error -i /tmp/k.aiff -ar 16000 -ac 1 -acodec pcm_s16le /tmp/k.wav
whisper-cli -m ~/.whisper-models/ggml-small.en.bin -f /tmp/k.wav -nt -t 8
# Expect: "this is a transcription test"
```

Then in Claude: *"use ringback-voice to call me and say hi."* You should get a real
call; speak a sentence and Claude should read your words back. **Use a handset or
headset, not speakerphone** — barge-in/capture is much more reliable that way.

---

## 5. Issues we hit → root causes → fixes

The order below is the order we encountered them; each was masking the next.

### 5.1 MCP `ENOENT` — wrong launcher path *(manual setup error)*
**Symptom:** `Failed to reconnect to ringback-voice: ENOENT`.
**Cause:** the server was registered pointing at
`…/Neo/run_voice_mcp.sh` — the **wrong directory** (the script lives in
`…/Neo/ringback/run_voice_mcp.sh`). `ENOENT` = the launcher file doesn't exist at that path.
**Fix:** re-register with the correct absolute path (Step 6). After fixing the path the
error changed to `-32000`, which exposed the next, real problem.
**Lesson:** always register with the full path *including the `ringback/` subdirectory*.

### 5.2 `setup.sh` dies at `make python`
**Symptom:** `setup.sh` aborts at "Building pjsua2 Python bindings" with
`make: *** No rule to make target 'python'`.
**Cause:** `setup.sh` runs `make python`, but pjproject 2.17's
`pjsip-apps/src/swig/python/Makefile` only has the default `all` target — you build it
with plain `make`. Because `setup.sh` uses `set -e`, this kills the script, and the
**subsequent steps never run**: the whisper model download (§5.7) and `voice.env`
creation are both skipped. This single bug is the upstream cause of several later
symptoms.
**Fix:** build the bindings with `make` (Step 2); manually do the skipped steps (Step 3).

### 5.3 pjsua2 bindings fail to compile (C++11)
**Symptom:** `error: rvalue references are a C++11 extension` /
`expected ';' at end of declaration list` while compiling `pjsua2_wrap.cpp`.
**Cause:** the SWIG-generated C++ was compiled without a C++ standard flag, defaulting to
an old dialect that rejects `&&` rvalue refs and `nullptr` in the pjsua2 headers.
**Fix:** pass `CFLAGS="-std=c++11 …"` when building the bindings (Step 2). Result:
`_pjsua2.cpython-311-darwin.so`.
**Note (harmless):** the extension builds **universal2** (arm64+x86_64) while pjproject is
arm64-only, so you'll see many `ld: warning: ignoring file … required architecture
'x86_64'`. These are benign — Python runs the arm64 slice, which links fine.

### 5.4 `mcp` / `httpx` missing, `voice.env` missing
**Symptom:** server fails to import `mcp`; no `voice.env`.
**Cause:** collateral damage from §5.2 — those are `setup.sh` steps 5–6 that never ran.
**Fix:** Step 3 (pip install + copy the template).

### 5.5 ⭐ THE BIG ONE — OpenSSL flat-namespace collision
This caused two distinct failures (`-32000` at init, then a hard **segfault** on the call)
with the **same root cause**.

**Symptom A — `-32000` at startup:**
```
pjsua_media.c  Error initializing SRTP library: couldn't initialize [status=259804]
```
`status 259804` decodes to libsrtp `srtp_err_status_init_fail`. `srtp_init()` worked fine
from a standalone C program but **failed inside the Python process**.

**Symptom B — segfault during the call:** after fixing A, `call_start` crashed the server
("Connection closed"). The macOS crash report showed:
```
libboringssl.dylib   SSL_CTX_new        ◄── crash
libpj.dylib.2        init_openssl
libpj.dylib.2        ssl_create         (pjlib's TLS socket for SIP-over-TLS)
```

**Root cause (both):** pjproject builds **every** `.dylib` with
`-undefined dynamic_lookup -flat_namespace` (`build/rules.mak:18`). With a flat namespace,
a library's undefined OpenSSL symbols resolve at runtime against *whatever crypto library
is first in the process's global symbol table* — they are **not** bound to a specific lib.
Inside the Python process there are **multiple** crypto libs loaded:
- Apple's system **LibreSSL/BoringSSL** (`/usr/lib/libcrypto.46.dylib`), pulled in by system frameworks,
- Python's own OpenSSL (e.g. python.org 3.11 bundles **OpenSSL 1.1.1q**),
- the Homebrew **OpenSSL 3** that pjproject was compiled against.

So pjproject's `EVP_*`, `SSL_CTX_new`, `RAND_bytes`, and libsrtp's HMAC self-test bound to
the **wrong** crypto (LibreSSL lacks the OpenSSL-3 `EVP_MAC_*` API; `SSL_CTX_new` returns a
struct of a different shape → `srtp_init` self-test fails, and `SSL_CTX_new` segfaults).
A plain C process has only one OpenSSL loaded, which is why it "worked" outside Python and
masked the problem.

**Four libraries were affected** (all flat, all calling OpenSSL):
| dylib | OpenSSL symbols | used for |
|-------|-----------------|----------|
| `libsrtp` | HMAC/EVP (via `srtp_init`) | SRTP media encryption |
| `libpj` | `SSL_CTX_new`, `SSL_*`, `EVP_*` (97 syms) | SIP-over-TLS socket |
| `libpjsip` | `EVP_Digest*` (6) | SIP **digest authentication** |
| `libpjmedia` | `RAND_bytes` (1) | SRTP key generation |

**Fix:** relink all four with a **two-level namespace** explicitly bound to Homebrew
OpenSSL 3, so the OpenSSL symbols pin to `libcrypto.3`/`libssl.3` and can never resolve to
the system LibreSSL. Run `./fix_macos_twolevel.sh` (Step 4). Confirm with
`nm -m … | grep SSL_CTX_new` → `(undefined) external _SSL_CTX_new (from libssl)`.
For from-scratch builds, patching `rules.mak` to `-twolevel_namespace` before compiling
achieves the same thing without a relink (see the box in Step 4).

### 5.6 Process aborts on interpreter exit while a call is live *(test artifact)*
**Symptom:** a bare test script printing `Fatal Python error: Aborted` after a call connected.
**Cause:** pjsua2's worker thread invoking director callbacks on a Python `_Call` object that
was being finalized during interpreter shutdown.
**Not a real-world issue:** the MCP server stays running and calls `call_end()` to hang up
cleanly, so it never hits the shutdown race. Don't chase this in normal operation.

### 5.7 Calls connect but every reply is `[SILENCE]` / `[unclear]`
**Symptom:** outbound audio works, barge-in fires, but the user's speech never transcribes.
**Cause:** the **whisper model file was missing** —
`~/.whisper-models/ggml-small.en.bin` was never downloaded (collateral from §5.2; it's
`setup.sh` step 5). whisper failed to initialize on every call → empty transcript →
`[SILENCE]`. The reason this *looked* like a mic/capture bug: **barge-in detection is pure
RMS level analysis, not whisper**, so it kept firing even with no model — making it seem
like audio was captured but "unclear."
**Fix:** download the model (Step 3b). After that, transcription worked first try
("Okay, I am testing cloud." / "My favorite color is blue."). No server restart needed —
`whisper-cli` reads the model fresh on each turn.

---

## 6. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ENOENT` / failed to reconnect | wrong launcher path | re-register with full `…/ringback/run_voice_mcp.sh` path (§5.1) |
| `-32000` / "Connection closed" at startup | SRTP init fail (flat-namespace OpenSSL) | `./fix_macos_twolevel.sh` (§5.5) |
| `pjsua2: build not found` from launcher | bindings never compiled | build with `make` + `-std=c++11` (§5.2/5.3) |
| `No rule to make target 'python'` | `setup.sh` bug | use `make`, not `make python` (§5.2) |
| `rvalue references are a C++11 extension` | missing C++ std flag | `CFLAGS="-std=c++11 …"` (§5.3) |
| `ModuleNotFoundError: mcp` | deps not installed | `pip install "mcp>=1.2.0" httpx` (§5.4) |
| call **segfaults** / "Connection closed" mid-call | `SSL_CTX_new` flat-bound to system LibreSSL | `./fix_macos_twolevel.sh` (§5.5) |
| call connects but replies are `[SILENCE]`/`[unclear]` | whisper model missing | download `ggml-small.en.bin` (§5.7) |
| poor transcription on a connected call | speakerphone / low mic | use a **headset/handset**; try `medium.en` model |

Useful diagnostics:
```bash
# Reproduce the SRTP failure in isolation (5 = init_fail, 0 = ok):
python3 -c "import ctypes; print(ctypes.CDLL('<PJ>/third_party/lib/libsrtp.dylib.2').srtp_init())"

# See whether a dylib is FLAT or TWOLEVEL:
otool -hv <PJ>/pjlib/lib/libpj.dylib.2 | sed -n '4p'    # look for TWOLEVEL

# Native backtrace of a Python segfault (lldb often can't attach; use the crash report):
ls -t ~/Library/Logs/DiagnosticReports/Python-*.ips | head -1
```

---

## 7. Files & key paths

| Path | What |
|------|------|
| `ringback/run_voice_mcp.sh` | launcher — sets `PYTHONPATH`/`DYLD_LIBRARY_PATH`, sources `voice.env`, runs `voice_mcp.py` |
| `ringback/voice.env` | your SIP creds + optional overrides (gitignored) |
| `ringback/voice_mcp.py` | the MCP server (FastMCP tools) |
| `ringback/voice_agent.py` | pjsua2 call engine + TTS + whisper glue |
| `ringback/fix_macos_twolevel.sh` | the OpenSSL two-level relink fix (§5.5) |
| `~/build/pjproject-2.17/` | compiled pjproject + pjsua2 bindings |
| `~/.whisper-models/ggml-small.en.bin` | whisper STT model (~481 MB) |
| `*.flatns-bkp` (next to each fixed dylib) | backups of the original flat-namespace libs |

---

## 8. Upstream improvements worth contributing

To spare the next person, these belong back in the repo's `setup.sh` / build:
1. Replace `make python` with `make` (with `-std=c++11`) for pjproject ≥ 2.13.
2. Patch `build/rules.mak` line 18 to `-twolevel_namespace` on macOS (or run the relink
   automatically) so OpenSSL binds correctly inside Python.
3. Verify `~/.whisper-models/<model>` exists at the end of setup and fail loudly if not.

---
*Authored from a real end-to-end debugging session on macOS 15 (Darwin 24) / Apple
Silicon, pjproject 2.17, Homebrew OpenSSL 3.6.2, python.org Python 3.11.*
