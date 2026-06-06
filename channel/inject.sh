#!/usr/bin/env bash
# Prototype stand-in for the call-driver: push an "answer" into the live session,
# as if you had just said it on the phone. The real call-driver will POST the same
# /inject endpoint with your transcribed speech instead.
#
# Usage:  ./channel/inject.sh "yes, go with option B"
#         ./channel/inject.sh --call-id abc123 "approved, ship it"
set -euo pipefail

PORT="${RINGBACK_CHANNEL_PORT:-8790}"
CALL_ID="local"
if [[ "${1:-}" == "--call-id" ]]; then CALL_ID="$2"; shift 2; fi
TEXT="$*"
[[ -n "$TEXT" ]] || { echo "usage: $0 [--call-id ID] \"your spoken answer\"" >&2; exit 1; }

BODY=$(python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1], "call_id": sys.argv[2]}))' "$TEXT" "$CALL_ID")
URL="http://127.0.0.1:${PORT}/inject"

# (Avoid bash-3.2 empty-array pitfalls — branch instead of expanding an array.)
if [[ -n "${RINGBACK_CHANNEL_TOKEN:-}" ]]; then
  curl -fsS -X POST "$URL" -H 'Content-Type: application/json' \
    -H "X-Ringback-Token: ${RINGBACK_CHANNEL_TOKEN}" -d "$BODY"
else
  curl -fsS -X POST "$URL" -H 'Content-Type: application/json' -d "$BODY"
fi
echo "  → injected into live session (call_id=$CALL_ID)"
