#!/bin/bash
# Launcher for the ONE harvest+verify call (tests/harvest_call.py). Mirrors
# run_voice_mcp.sh: sources the gitignored voice.env (SIP creds) and sets the pjsua2
# env, then runs under the Python pjsua2 was built against.
set -e
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -f "$APP/voice.env" ]; then
  set -a; . "$APP/voice.env"; set +a
fi

PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-2.17}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3 2>/dev/null || echo /usr/local)}"
SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* 2>/dev/null | head -1)"

if [ -z "$SWIG_LIB" ] || [ ! -d "$PJPROJECT_DIR/pjlib/lib" ]; then
  echo "harvest: pjsua2 build not found under PJPROJECT_DIR=$PJPROJECT_DIR" >&2
  exit 1
fi

export PYTHONPATH="$SWIG_LIB:$APP"
export DYLD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib:$OPENSSL_PREFIX/lib"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

exec "$PYTHON_BIN" "$APP/tests/harvest_call.py" "$@"
