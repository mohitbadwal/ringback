#!/usr/bin/env bash
# ringback-alert MCP launcher (Docker route).
#
# The alert backends (ntfy / Pushover push) need only outbound HTTPS, so no special
# networking. Config arrives as environment variables (the plugin's .mcp.json maps each
# from ${user_config.*}) and is forwarded with bare `-e NAME` so values stay off the
# command line and blank optionals fall back to the image defaults.
set -euo pipefail

IMG="${RINGBACK_IMAGE:-ghcr.io/mohitbadwal/ringback:latest}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[ringback] Docker is required but 'docker' was not found on PATH." >&2
  echo "[ringback] Install Docker Desktop (macOS/Windows) or docker engine (Linux), then restart your MCP client." >&2
  exit 1
fi

ENVARGS=()

# Forward a configured var to the container. Skips a blank value AND any un-substituted
# ${user_config.*} placeholder. Always returns 0 so it is safe under `set -e`.
add_env() {
  local k="$1"
  local v="${!k:-}"   # separate line: ${!k} must see k already assigned
  case "$v" in
    "" | '${'*) return 0 ;;
  esac
  ENVARGS+=(-e "$k")
}

for v in ALERT_CHANNEL NTFY_URL NTFY_TOKEN PUSHOVER_TOKEN PUSHOVER_USER \
         ALERT_MAX_PER_WINDOW ALERT_WINDOW_SEC; do
  add_env "$v"
done

# ${arr[@]+...} guards against an empty array under `set -u` on bash 3.2 (macOS default).
exec docker run --rm -i ${ENVARGS[@]+"${ENVARGS[@]}"} "$IMG" python server.py
