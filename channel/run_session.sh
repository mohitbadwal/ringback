#!/usr/bin/env bash
# Launch an interactive Claude Code session wired to the ringback channel (Way 2),
# in the bypass-permissions posture so that when the session is woken by a phoned-in
# answer while you're away, it can actually ACT on it (call `say`, run the tools it
# needs) instead of stalling on a permission prompt nobody is there to approve.
#
# Trade-off: bypass = full trust for this session. Use it for sessions you intend to
# leave running and reach by phone. For an attended session, drop --dangerously-skip-
# permissions (or use an allowlist) — but then a woken session may pause for approval.
#
# Usage:  ./channel/run_session.sh [extra claude args...]
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root, so the relative --mcp-config path resolves

exec claude \
  --dangerously-skip-permissions \
  --mcp-config channel/ringback.mcp.json --strict-mcp-config \
  --dangerously-load-development-channels server:ringback \
  "$@"
