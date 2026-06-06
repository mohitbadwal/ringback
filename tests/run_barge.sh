#!/bin/bash
# Launcher for the live barge-in confirmation call (tests/barge_call.py). Same env as the
# harvest; uses VOICE_RTP_PORT=5000 so it coexists with a running MCP voice server.
set -e
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$APP/voice.env" ] && { set -a; . "$APP/voice.env"; set +a; }
PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-2.17}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3 2>/dev/null || echo /usr/local)}"
SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* 2>/dev/null | head -1)"
export PYTHONPATH="$SWIG_LIB:$APP"
export DYLD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib:$OPENSSL_PREFIX/lib"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
export VOICE_RTP_PORT="${VOICE_RTP_PORT:-5000}"
exec "$PYTHON_BIN" "$APP/tests/barge_call.py" "$@"
