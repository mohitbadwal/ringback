#!/usr/bin/env bash
# ringback-voice MCP launcher (Docker route).
#
# Claude Code spawns this over stdio; it execs the ringback image, which speaks the MCP
# protocol on stdin/stdout. SIP credentials arrive as environment variables (the plugin's
# .mcp.json maps each one from ${user_config.*}) and are forwarded to the container with
# bare `-e NAME` so the values never appear on the command line, and so blank optionals
# fall back to the image's own defaults instead of overriding them with an empty string.
set -euo pipefail

IMG="${RINGBACK_IMAGE:-ghcr.io/mohitbadwal/ringback:latest}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[ringback] Docker is required but 'docker' was not found on PATH." >&2
  echo "[ringback] Install Docker Desktop (macOS/Windows) or docker engine (Linux), then restart your MCP client." >&2
  exit 1
fi

ENVARGS=()

# Forward a configured var to the container. Skips a blank value AND any un-substituted
# ${user_config.*} placeholder. Always returns 0 so it is safe under `set -e` (a bare
# `[ -n x ] && ...` would return 1 on the empty case and abort the whole script).
add_env() {
  local k="$1"
  local v="${!k:-}"   # separate line: ${!k} must see k already assigned
  case "$v" in
    "" | '${'*) return 0 ;;
  esac
  ENVARGS+=(-e "$k")
}

# A required value must be present and actually substituted (not a literal placeholder).
require() {
  local k="$1"
  local v="${!k:-}"
  case "$v" in
    "" | '${'*)
      echo "[ringback] Missing $k — set it in the plugin configuration." >&2
      exit 1 ;;
  esac
}

require VOICE_SIP_ID
require VOICE_SIP_USER
require VOICE_SIP_PASS

for v in VOICE_SIP_ID VOICE_SIP_USER VOICE_SIP_PASS VOICE_SIP_CALLEE VOICE_DISPLAY_NAME \
         VOICE_SIP_PROXY VOICE_STUN VOICE_HALF_DUPLEX VOICE_DEBUG; do
  add_env "$v"
done

# RTP media networking (see docs/SETUP_DOCKER.md):
#   - Linux / WSL2 -> host networking; pjsua's RTP ports are directly reachable.
#   - Docker Desktop (macOS / Windows) -> the engine runs in a NAT'd Linux VM where neither
#     host networking nor port-publishing delivers the return RTP. Plain bridge + STUN is what
#     works: STUN (VOICE_STUN, set by the plugin) makes pjsua advertise its PUBLIC address so
#     the remote media server can route audio back. Verified two-way + barge-in on Apple Silicon.
NETARGS=()
case "$(uname -s)" in
  Linux) NETARGS=(--network host) ;;
  *)     NETARGS=() ;;   # plain bridge; STUN handles NAT traversal
esac

# ${arr[@]+...} guards against an empty array under `set -u` on bash 3.2 (macOS default).
exec docker run --rm -i ${NETARGS[@]+"${NETARGS[@]}"} ${ENVARGS[@]+"${ENVARGS[@]}"} "$IMG"
