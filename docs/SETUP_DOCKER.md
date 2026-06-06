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

- **Linux** — `--network host` (used above) puts the container on the host network, so
  pjsua's RTP ports are directly reachable. This is the simplest and most reliable.
- **Windows / macOS (Docker Desktop)** — Docker Desktop runs the engine inside a Linux VM,
  where `--network host` does **not** expose host ports the same way. Pin the RTP port and
  publish it as UDP:

  ```bash
  docker run -i --rm \
    -e VOICE_RTP_PORT=4000 -p 4000:4000/udp -p 4001:4001/udp \
    --env-file voice.docker.env ringback
  ```

  Linphone is a hosted SIP service that does symmetric RTP, so outbound-initiated media
  returns over the same mapping. **Tested working on Docker Desktop for Mac** with exactly
  the port mapping above — a real call connected with clean **two-way** audio (Piper TTS
  out, whisper capture in). If you do hit **one-way or no audio** on your network, that's
  an RTP/NAT edge — prefer **WSL2** on Windows (host networking; see
  [SETUP_WINDOWS.md](SETUP_WINDOWS.md)) or the native Linux setup.

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
