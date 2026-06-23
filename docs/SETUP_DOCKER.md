# Ringback — Docker Setup (universal: Linux, Windows, macOS)

The `Dockerfile` builds the **complete voice stack** (pjproject + pjsua2 bindings,
whisper.cpp, Piper TTS + a voice, ffmpeg, baresip) into one Linux image that runs the
same everywhere. It's the zero-build option and the recommended way to run on Windows
(via Docker Desktop). The engine is **headless** — no sound card needed.

> Why this works: the voice engine never uses a local mic/speaker — all audio is
> WAV ↔ SIP/RTP and pjsua2 runs on a NULL audio device (`VOICE_NULL_AUDIO=1` in the
> image). So a container with no audio hardware is a perfectly good runtime.

## Build

```bash
docker build -t ringback .
```

First build compiles pjproject + whisper.cpp from source and bakes in a whisper model
(`base.en`) and a Piper voice (`en_US-lessac-medium`), so it's a few minutes and a
largish image — but then it just works offline.

## Run + register as an MCP server

ringback-voice speaks MCP over stdio, so the container runs with `-i` and your MCP client
launches it:

First convert your creds to a Docker env-file. `voice.env` uses shell `export KEY="val"`
syntax, which `docker --env-file` does **not** parse — it wants plain `KEY=val` lines:

```bash
sed -E 's/^[[:space:]]*export //; s/^([A-Z_]+)="?([^"]*)"?$/\1=\2/' voice.env \
  | grep -E '^[A-Z_]+=' > voice.docker.env      # gitignored; plain KEY=val for Docker
```

Then register the server (your MCP client launches the container on stdio):

```bash
claude mcp add ringback-voice --scope user -- \
  docker run -i --rm --network host --env-file voice.docker.env ringback
```

`voice.docker.env` holds your SIP creds — passed at **runtime** via `--env-file`, never
baked into the image. (One-off `docker run` from a shell can instead source `voice.env`
and pass `-e VOICE_SIP_ID -e VOICE_SIP_USER -e VOICE_SIP_PASS …` to keep values off the
command line.)

To run the **alert** server instead (ntfy/Pushover/SIP), override the command:

```bash
docker run -i --rm --network host --env-file alert.docker.env ringback python server.py
```

## Networking (the one thing to get right): RTP

SIP signaling is outbound TLS to Linphone and always works. The **media (RTP/SRTP)** must
be able to flow back to the container:

- **Linux / WSL2** — `--network host` (used above) puts the container on the host network, so
  pjsua's RTP ports are directly reachable. Simplest and most reliable.
- **Windows / macOS (Docker Desktop)** — the engine runs inside a NAT'd Linux VM where
  **neither `--network host` nor `-p` port-publishing delivers the return RTP** — you get
  one-way audio (you hear the agent; your reply never arrives). The fix is **STUN**: set
  `VOICE_STUN` so pjsua discovers and advertises its **public** address, and use plain bridge
  networking:

  ```bash
  docker run -i --rm \
    -e VOICE_STUN=stun.l.google.com:19302 \
    --env-file voice.docker.env ringback
  ```

  Verified two-way audio + barge-in on Docker Desktop for Mac (Apple Silicon) this way. STUN
  only discovers your public address (one tiny handshake at call setup); no call audio passes
  through it. Use `stun.linphone.org` if you'd rather not use Google's. The **Claude Code
  plugin sets `VOICE_STUN` automatically**, so this is only needed for a manual `docker run`.

## Slimming / overriding models

The image bakes in models for convenience. To keep your own (or shrink the image), mount
them and point the env vars at the mount:

```bash
docker run -i --rm --network host \
  -v ~/.whisper-models:/models/whisper -v ~/.piper-voices:/models/piper \
  -e WHISPER_SERVER_MODEL=/models/whisper/ggml-base.en.bin \
  -e VOICE_PIPER_MODEL=/models/piper/en_US-lessac-medium.onnx \
  --env-file voice.docker.env ringback
```

## Verify the build (offline, no phone)

```bash
# pjsua2 imports inside the image:
docker run --rm ringback python -c "import pjsua2; print('pjsua2 OK')"

# whisper + piper present:
docker run --rm ringback bash -c "whisper-server --help >/dev/null && echo whisper OK; piper --help >/dev/null 2>&1 && echo piper OK"

# full offline suite inside the container (generates fixtures via Piper, runs all tests):
docker run --rm ringback python tests/run_all.py
```
