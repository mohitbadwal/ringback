# Dockerfile — lets the ringback-alert MCP server start and respond to tool
# introspection inside a Linux container. This exists so automated MCP directory
# checks (e.g. Glama) can verify the server boots and lists its tools.
#
# NOTE: this image runs ONLY ringback-alert (server.py: ntfy / Pushover / SIP
# notifications), which needs just `mcp` + `httpx`. The ringback-voice server is
# macOS-only (CoreAudio + pjsua2 built from source + Apple `say`) and is not
# containerizable — see README/NOTICE. The SIP "call" backend of ringback-alert
# shells out to baresip only at send-time, so tool listing works without it.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt server.py ./
RUN pip install --no-cache-dir -r requirements.txt

# stdio MCP server — responds to initialize / tools/list over stdin/stdout.
CMD ["python", "server.py"]
