#!/bin/bash
# setup.sh — one-shot installer for ringback-alert / ringback-voice on macOS.
#
# Installs the toolchain, compiles pjproject + the pjsua2 Python bindings from
# source (no Homebrew formula provides them), downloads a whisper model, and
# installs the Python deps. Safe to re-run; it skips steps already done.
#
# macOS only: the voice feature uses Apple `say` (TTS) and CoreAudio.
#
# Thanks to the teammate who debugged the macOS sharp edges end-to-end (the
# make-target, -std=c++11, OpenSSL flat-namespace, and missing-model issues) —
# see docs/SETUP_MACOS.md for the full root-cause writeup + symptom→fix table.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PJ_VER="${PJ_VER:-2.17}"
PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-$PJ_VER}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
WHISPER_MODEL_NAME="${WHISPER_MODEL_NAME:-ggml-small.en.bin}"
MODEL_DIR="$HOME/.whisper-models"

say_step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

[ "$(uname)" = "Darwin" ] || { echo "This setup targets macOS only."; exit 1; }
command -v brew >/dev/null || { echo "Homebrew required: https://brew.sh"; exit 1; }
[ -n "$PYTHON_BIN" ] || { echo "python3 not found on PATH"; exit 1; }

say_step "1/6 Installing Homebrew packages (swig, openssl@3, opus, sdl2, ffmpeg, whisper-cpp, baresip, uv)"
# opus + sdl2 are pjproject media deps also needed if you run fix_macos_twolevel.sh.
# uv runs the ringback-alert server (uv run server.py); the rest are voice deps.
brew install swig openssl@3 opus sdl2 ffmpeg whisper-cpp baresip uv

OPENSSL_PREFIX="$(brew --prefix openssl@3)"

say_step "2/6 Fetching pjproject $PJ_VER source -> $PJPROJECT_DIR"
mkdir -p "$(dirname "$PJPROJECT_DIR")"
if [ ! -d "$PJPROJECT_DIR" ]; then
  curl -fsSL "https://github.com/pjsip/pjproject/archive/refs/tags/$PJ_VER.tar.gz" \
    -o "/tmp/pjproject-$PJ_VER.tar.gz"
  tar xzf "/tmp/pjproject-$PJ_VER.tar.gz" -C "$(dirname "$PJPROJECT_DIR")"
fi

say_step "3/6 Configuring + compiling pjproject (with OpenSSL for TLS/SRTP)"
cd "$PJPROJECT_DIR"
export CFLAGS="-I$OPENSSL_PREFIX/include -fPIC -O2"
export LDFLAGS="-L$OPENSSL_PREFIX/lib"
if [ ! -f "pjlib/lib/libpj.dylib" ]; then
  ./configure --enable-shared --with-ssl="$OPENSSL_PREFIX"
  make dep
  make
fi

say_step "4/6 Building pjsua2 Python bindings (for $("$PYTHON_BIN" --version))"
cd "$PJPROJECT_DIR/pjsip-apps/src/swig/python"
if ! ls build/lib.*/_pjsua2*.so >/dev/null 2>&1; then
  # pjproject 2.17's bindings build via the default 'all' target — NOT `make python`
  # (there is no `python` target; `make python` aborts the whole script under set -e,
  # which then skips the whisper download + voice.env creation below).
  # -std=c++11 is REQUIRED or the SWIG-generated C++ fails on rvalue refs / nullptr.
  CFLAGS="-std=c++11 -I$OPENSSL_PREFIX/include -fPIC -O2" \
  CXXFLAGS="-std=c++11 -I$OPENSSL_PREFIX/include -fPIC -O2" \
  PATH="$(dirname "$PYTHON_BIN"):$PATH" \
    make
fi

say_step "5/6 Downloading whisper model ($WHISPER_MODEL_NAME)"
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/$WHISPER_MODEL_NAME" ]; then
  echo "  (~470 MB for small.en — this can take a minute)"
  curl -fL --progress-bar "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL_NAME" \
    -o "$MODEL_DIR/$WHISPER_MODEL_NAME"
fi
# fail loudly if the model isn't there — without it, calls connect but every reply
# transcribes as [SILENCE]/[unclear].
[ -f "$MODEL_DIR/$WHISPER_MODEL_NAME" ] \
  || { echo "ERROR: whisper model missing at $MODEL_DIR/$WHISPER_MODEL_NAME"; exit 1; }

# --- Piper neural TTS (the cross-platform default voice) — BEST EFFORT ----------
# Piper is the default TTS everywhere. On macOS, if it can't install (e.g. no onnxruntime
# wheel for your Python), that's fine: the engine auto-falls back to `say`, so this step
# never blocks setup and your existing macOS behavior is preserved.
say_step "5b/6 Installing Piper TTS + voice (best-effort; falls back to macOS 'say')"
PIPER_DIR="$HOME/.piper-voices"; PIPER_VOICE="en_US-lessac-medium"
mkdir -p "$PIPER_DIR"
if "$PYTHON_BIN" -m pip install --quiet piper-tts 2>/dev/null \
   || "$PYTHON_BIN" -m pip install --quiet --break-system-packages piper-tts 2>/dev/null; then
  PV="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
  for f in "$PIPER_VOICE.onnx" "$PIPER_VOICE.onnx.json"; do
    [ -f "$PIPER_DIR/$f" ] || curl -fL --progress-bar "$PV/$f" -o "$PIPER_DIR/$f" || true
  done
  echo "  Piper installed (voice: $PIPER_DIR/$PIPER_VOICE.onnx). Force the macOS voice with VOICE_TTS=say."
else
  echo "  Piper not installed — using macOS 'say' (totally fine). Re-run later to add it."
fi

say_step "6/6 Installing Python deps (mcp, httpx) into $PYTHON_BIN"
# (--break-system-packages fallback for Homebrew/PEP-668 "externally-managed" pythons)
"$PYTHON_BIN" -m pip install --quiet "mcp>=1.2.0" httpx \
  || "$PYTHON_BIN" -m pip install --quiet --break-system-packages "mcp>=1.2.0" httpx

# quick sanity import of pjsua2 under the resolved env
SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* | head -1)"
PYTHONPATH="$SWIG_LIB" \
DYLD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib:$OPENSSL_PREFIX/lib" \
  "$PYTHON_BIN" -c "import pjsua2; print('pjsua2 import OK')"

# create voice.env from the template if it doesn't exist yet (you fill in creds)
if [ ! -f "$APP/voice.env" ]; then
  cp "$APP/voice.env.example" "$APP/voice.env"
  echo "Created $APP/voice.env — edit it with your SIP account."
fi

cat <<EOF

Setup complete.

⚠️  macOS OpenSSL fix (often REQUIRED — especially with python.org Python):
    pjproject builds its dylibs with a flat namespace, so inside Python its
    OpenSSL calls can bind to macOS LibreSSL instead of openssl@3 → calls fail
    with MCP -32000 (srtp_init) or a segfault (SSL_CTX_new) on connect. If that
    happens, run the relink helper:

        PJPROJECT_DIR="$PJPROJECT_DIR" "$APP/fix_macos_twolevel.sh"

    (Anaconda Python often avoids the collision and won't need it.) Full
    root-cause writeup + symptom→fix table: docs/SETUP_MACOS.md

Next steps:
  1. Edit voice.env (already created for you) and fill in your 3 required SIP
     vars: VOICE_SIP_ID, VOICE_SIP_USER, VOICE_SIP_PASS
     (get a free account at https://subscribe.linphone.org).
  2. Register ringback-voice with your MCP client:

     claude mcp add ringback-voice --scope user -- "$APP/run_voice_mcp.sh"

  3. Test: in a fresh Claude session, say "use ringback-voice to call me and say hi".

  (ringback-alert is optional — copy vars from alert.env.example into your MCP
   client's env block; it runs via 'uv run server.py', no file to source.)

If your python differs from the one above, set PYTHON_BIN and re-run.
The whisper model is at: $MODEL_DIR/$WHISPER_MODEL_NAME
EOF
