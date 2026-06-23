#!/usr/bin/env bash
# SessionStart hook: warm the Docker image cache in the BACKGROUND.
#
# The engine image is large (~2 GB: compiled pjsua2 + whisper.cpp + baked-in models). On a
# cold machine the first `docker run` would pull it inline, which can exceed the MCP server
# connect timeout. Pulling here, proactively and detached, means the image is usually ready
# by the time the agent places a call. Combined with a generous startupTimeout in .mcp.json,
# the first connect no longer races the download.
#
# Never blocks session start; no-op if Docker is absent or the image is already cached.
set -u
IMG="${RINGBACK_IMAGE:-ghcr.io/mohitbadwal/ringback:latest}"
command -v docker >/dev/null 2>&1 || exit 0
docker image inspect "$IMG" >/dev/null 2>&1 && exit 0
( docker pull "$IMG" >/dev/null 2>&1 & ) >/dev/null 2>&1   # detached; do not wait
exit 0
