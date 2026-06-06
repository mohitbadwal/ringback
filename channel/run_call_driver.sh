#!/bin/bash
# Launcher for the ringback call-driver. Mirrors run_voice_mcp.sh's environment
# setup (so `import pjsua2` and `import voice_agent` resolve under the Python the
# bindings were built against), then runs channel/call_driver.py.
#
# Config & secrets come from the gitignored voice.env (VOICE_SIP_ID / VOICE_SIP_USER
# / VOICE_SIP_PASS / VOICE_SIP_CALLEE …) — same file ringback-voice uses. If your
# SIP password lives in your MCP client config instead of voice.env, export
# VOICE_SIP_PASS before calling this.
#
# Usage:  ./channel/run_call_driver.sh --dry-run
#         ./channel/run_call_driver.sh --question "Which option, A or B?"
set -e
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root (channel/..)

if [ -f "$APP/voice.env" ]; then
  set -a; . "$APP/voice.env"; set +a
fi

PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-2.17}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3 2>/dev/null || echo /usr/local)}"

SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* 2>/dev/null | head -1)"

if [ -z "$SWIG_LIB" ] || [ ! -d "$PJPROJECT_DIR/pjlib/lib" ]; then
  echo "call-driver: pjsua2 build not found under PJPROJECT_DIR=$PJPROJECT_DIR" >&2
  echo "Run ./setup.sh first (it compiles pjproject + the Python bindings)." >&2
  exit 1
fi

export PYTHONPATH="$SWIG_LIB:$APP"
export DYLD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib:$OPENSSL_PREFIX/lib"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

exec "$PYTHON_BIN" "$APP/channel/call_driver.py" "$@"
