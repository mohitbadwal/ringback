#!/usr/bin/env bash
# setup-linux.sh — one-shot installer for ringback-alert / ringback-voice on Linux
# (and on Windows via WSL2, which is just Linux).
#
# Installs the toolchain, compiles pjproject + the pjsua2 Python bindings from source,
# builds whisper.cpp, installs Piper (neural TTS) + a voice, downloads a whisper model,
# and installs the Python deps. Safe to re-run; it skips steps already done.
#
# Linux does NOT need the macOS OpenSSL flat-namespace relink (fix_macos_twolevel.sh):
# the system OpenSSL resolves cleanly, so pjproject builds and imports as-is.
#
# Supported package managers: apt (Debian/Ubuntu) and dnf (Fedora/RHEL). Other distros:
# install the equivalent of the apt list below by hand, then re-run to do the build steps.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PJ_VER="${PJ_VER:-2.17}"
PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-$PJ_VER}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
WHISPER_DIR="${WHISPER_DIR:-$HOME/build/whisper.cpp}"
WHISPER_MODEL_DIR="${WHISPER_MODEL_DIR:-$HOME/.whisper-models}"
PIPER_DIR="${PIPER_DIR:-$HOME/.piper-voices}"
PIPER_VOICE="${PIPER_VOICE:-en_US-lessac-medium}"
LOCAL_BIN="${LOCAL_BIN:-$HOME/.local/bin}"

say_step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

[ "$(uname)" = "Linux" ] || { echo "This setup targets Linux (use setup.sh on macOS)."; exit 1; }
[ -n "$PYTHON_BIN" ] || { echo "python3 not found on PATH"; exit 1; }
mkdir -p "$LOCAL_BIN" "$WHISPER_MODEL_DIR" "$PIPER_DIR"

# --- 1/7 system packages ------------------------------------------------------
say_step "1/7 Installing system packages"
if command -v apt-get >/dev/null; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y --no-install-recommends \
    build-essential swig python3-dev python3-pip python3-venv python3-setuptools python3-wheel \
    libssl-dev libopus-dev libsdl2-dev libasound2-dev \
    ffmpeg git curl ca-certificates cmake xz-utils xprintidle
elif command -v dnf >/dev/null; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO dnf install -y \
    gcc gcc-c++ make swig python3-devel python3-pip python3-setuptools python3-wheel \
    openssl-devel opus-devel SDL2-devel alsa-lib-devel \
    ffmpeg git curl cmake xz xprintidle
else
  echo "No apt-get or dnf found. Install the Debian package equivalents listed in this"
  echo "script's apt block, then re-run."; exit 1
fi

# --- 2/7 pjproject ------------------------------------------------------------
say_step "2/7 Fetching + building pjproject $PJ_VER -> $PJPROJECT_DIR"
mkdir -p "$(dirname "$PJPROJECT_DIR")"
if [ ! -d "$PJPROJECT_DIR" ]; then
  curl -fsSL "https://github.com/pjsip/pjproject/archive/refs/tags/$PJ_VER.tar.gz" \
    -o "/tmp/pjproject-$PJ_VER.tar.gz"
  tar xzf "/tmp/pjproject-$PJ_VER.tar.gz" -C "$(dirname "$PJPROJECT_DIR")"
fi
cd "$PJPROJECT_DIR"
export CFLAGS="-fPIC -O2"
if [ ! -f "pjlib/lib/libpj.so" ] && ! ls pjlib/lib/libpj-*.so >/dev/null 2>&1; then
  ./configure --enable-shared --with-ssl
  make dep
  make
fi

# --- 3/7 pjsua2 python bindings ----------------------------------------------
say_step "3/7 Building pjsua2 Python bindings (for $("$PYTHON_BIN" --version))"
# setup.py needs setuptools (+ distutils shim) — Python 3.12 dropped distutils from stdlib.
"$PYTHON_BIN" -m pip install --quiet --upgrade setuptools wheel 2>/dev/null \
  || "$PYTHON_BIN" -m pip install --quiet --break-system-packages --upgrade setuptools wheel 2>/dev/null || true
cd "$PJPROJECT_DIR/pjsip-apps/src/swig/python"
if ! ls build/lib.*/_pjsua2*.so >/dev/null 2>&1; then
  # pjproject 2.17 builds the bindings via the default target; -std=c++11 is required.
  CFLAGS="-std=c++11 -fPIC -O2" CXXFLAGS="-std=c++11 -fPIC -O2" \
    PATH="$(dirname "$PYTHON_BIN"):$PATH" make
fi

# --- 4/7 whisper.cpp ----------------------------------------------------------
say_step "4/7 Building whisper.cpp (whisper-cli + whisper-server)"
if ! command -v whisper-cli >/dev/null && [ ! -x "$LOCAL_BIN/whisper-cli" ]; then
  [ -d "$WHISPER_DIR" ] || git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
  cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$WHISPER_DIR/build" -j --target whisper-cli whisper-server
  cp "$WHISPER_DIR/build/bin/whisper-cli" "$WHISPER_DIR/build/bin/whisper-server" "$LOCAL_BIN/"
fi

# --- 5/7 whisper models -------------------------------------------------------
say_step "5/7 Downloading whisper models (base.en = streaming server, small.en = cli fallback)"
for m in ggml-base.en.bin ggml-small.en.bin; do
  if [ ! -f "$WHISPER_MODEL_DIR/$m" ]; then
    echo "  fetching $m ..."
    curl -fL --progress-bar \
      "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$m" \
      -o "$WHISPER_MODEL_DIR/$m"
  fi
done

# --- 6/7 Piper TTS + a voice --------------------------------------------------
say_step "6/7 Installing Piper (neural TTS) + voice '$PIPER_VOICE'"
"$PYTHON_BIN" -m pip install --quiet --user piper-tts \
  || "$PYTHON_BIN" -m pip install --quiet --break-system-packages piper-tts
# voice model + its required .onnx.json config (rhasspy/piper-voices on HuggingFace)
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
for f in "$PIPER_VOICE.onnx" "$PIPER_VOICE.onnx.json"; do
  if [ ! -f "$PIPER_DIR/$f" ]; then
    echo "  fetching $f ..."
    curl -fL --progress-bar "$PIPER_BASE/$f" -o "$PIPER_DIR/$f"
  fi
done

# --- 7/7 python deps + smoke tests -------------------------------------------
say_step "7/7 Installing Python deps (mcp, httpx) + verifying pjsua2 import"
"$PYTHON_BIN" -m pip install --quiet "mcp>=1.2.0" httpx \
  || "$PYTHON_BIN" -m pip install --quiet --break-system-packages "mcp>=1.2.0" httpx

SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* | head -1)"
PYTHONPATH="$SWIG_LIB" \
LD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib" \
  "$PYTHON_BIN" -c "import pjsua2; print('pjsua2 import OK')"

if [ ! -f "$APP/voice.env" ]; then
  cp "$APP/voice.env.example" "$APP/voice.env"
  echo "Created $APP/voice.env — edit it with your SIP account."
fi

cat <<EOF

Setup complete (Linux).

Next steps:
  1. Edit voice.env and fill in your SIP vars: VOICE_SIP_ID, VOICE_SIP_USER, VOICE_SIP_PASS
     (free account: https://subscribe.linphone.org).
  2. Register ringback-voice with your MCP client (uses the cross-platform launcher):

       claude mcp add ringback-voice --scope user -- "$PYTHON_BIN" "$APP/run_voice_mcp.py"

  3. Test: in a fresh session, say "use ringback-voice to call me and say hi".

TTS: Piper is the default (voice at $PIPER_DIR/$PIPER_VOICE.onnx). Override the voice with
     VOICE_PIPER_MODEL, switch engine with VOICE_TTS=espeak, or plug a custom one with
     VOICE_TTS_CMD. whisper models: $WHISPER_MODEL_DIR. Tools installed to: $LOCAL_BIN
     (ensure that's on PATH).
EOF
