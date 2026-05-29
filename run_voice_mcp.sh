#!/bin/bash
# Launcher for the ringback-voice MCP. Sets up the environment so `import pjsua2`
# (built from source by setup.sh) resolves, then runs the server under the
# Python that pjsua2 was compiled against.
#
# Configuration & secrets live in a local, gitignored `voice.env` (sourced
# below) — see voice.env.example. Paths auto-detect but can be overridden by
# exporting PJPROJECT_DIR / PYTHON_BIN / OPENSSL_PREFIX before launch.

set -e
APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- local config & secrets (VOICE_SIP_ID / VOICE_SIP_USER / VOICE_SIP_PASS …)
if [ -f "$APP/voice.env" ]; then
  set -a; . "$APP/voice.env"; set +a
fi

# --- toolchain locations (override via env if your layout differs) ---
PJPROJECT_DIR="${PJPROJECT_DIR:-$HOME/build/pjproject-2.17}"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
OPENSSL_PREFIX="${OPENSSL_PREFIX:-$(brew --prefix openssl@3 2>/dev/null || echo /usr/local)}"

# pjsua2 SWIG build dir has a Python/arch-versioned name — glob for it.
SWIG_LIB="$(ls -d "$PJPROJECT_DIR"/pjsip-apps/src/swig/python/build/lib.* 2>/dev/null | head -1)"

if [ -z "$SWIG_LIB" ] || [ ! -d "$PJPROJECT_DIR/pjlib/lib" ]; then
  echo "ringback-voice: pjsua2 build not found under PJPROJECT_DIR=$PJPROJECT_DIR" >&2
  echo "Run ./setup.sh first (it compiles pjproject + the Python bindings)." >&2
  exit 1
fi

export PYTHONPATH="$SWIG_LIB:$APP"
export DYLD_LIBRARY_PATH="$PJPROJECT_DIR/pjlib/lib:$PJPROJECT_DIR/pjlib-util/lib:$PJPROJECT_DIR/pjnath/lib:$PJPROJECT_DIR/pjmedia/lib:$PJPROJECT_DIR/pjsip/lib:$PJPROJECT_DIR/third_party/lib:$OPENSSL_PREFIX/lib"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

exec "$PYTHON_BIN" "$APP/voice_mcp.py"
