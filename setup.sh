#!/bin/bash
# setup.sh — one-shot installer for phone-alert / phone-voice on macOS.
#
# Installs the toolchain, compiles pjproject + the pjsua2 Python bindings from
# source (no Homebrew formula provides them), downloads a whisper model, and
# installs the Python deps. Safe to re-run; it skips steps already done.
#
# macOS only: the voice feature uses Apple `say` (TTS) and CoreAudio.
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

say_step "1/6 Installing Homebrew packages (swig, openssl@3, ffmpeg, whisper-cpp, baresip, uv)"
# uv runs the phone-alert server (uv run server.py); the rest are voice deps.
brew install swig openssl@3 ffmpeg whisper-cpp baresip uv

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
  make python
fi

say_step "5/6 Downloading whisper model ($WHISPER_MODEL_NAME)"
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/$WHISPER_MODEL_NAME" ]; then
  echo "  (~470 MB for small.en — this can take a minute)"
  curl -fL --progress-bar "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL_NAME" \
    -o "$MODEL_DIR/$WHISPER_MODEL_NAME"
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

Next steps:
  1. Edit voice.env (already created for you) and fill in your 3 required SIP
     vars: VOICE_SIP_ID, VOICE_SIP_USER, VOICE_SIP_PASS
     (get a free account at https://subscribe.linphone.org).
  2. Register phone-voice with your MCP client:

     claude mcp add phone-voice --scope user -- "$APP/run_voice_mcp.sh"

  3. Test: in a fresh Claude session, say "use phone-voice to call me and say hi".

  (phone-alert is optional — copy vars from alert.env.example into your MCP
   client's env block; it runs via 'uv run server.py', no file to source.)

If your python differs from the one above, set PYTHON_BIN and re-run.
The whisper model is at: $MODEL_DIR/$WHISPER_MODEL_NAME
EOF
