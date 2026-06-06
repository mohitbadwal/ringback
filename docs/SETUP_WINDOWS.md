# Ringback — Windows Setup

The voice engine depends on **pjsua2** (pjproject's Python bindings). Building those
natively on Windows means Visual Studio + SWIG + hand-managed OpenSSL — fragile and
unsupported here. Instead, run the **Linux build** on Windows. Two supported routes:

## Recommended: WSL2

WSL2 is a real Linux kernel on Windows; everything in [SETUP_LINUX.md](SETUP_LINUX.md)
works inside it, and Linux host networking makes SIP/RTP "just work".

```powershell
# 1. Install WSL2 (PowerShell as admin), then reboot if prompted:
wsl --install -d Ubuntu
```

```bash
# 2. Inside the Ubuntu (WSL) shell:
git clone <your-fork-or-this-repo> ringback && cd ringback
./setup-linux.sh
$EDITOR voice.env        # VOICE_SIP_ID, VOICE_SIP_USER, VOICE_SIP_PASS

# 3. Register the MCP server using the WSL python + launcher.
#    If your MCP client runs on the Windows side, point it at the WSL interpreter:
#      wsl python3 /home/<you>/ringback/run_voice_mcp.py
#    If the client runs inside WSL, just:
claude mcp add ringback-voice --scope user -- python3 "$PWD/run_voice_mcp.py"
```

Microphone/speaker are irrelevant (the engine is headless — all audio is WAV ↔ SIP/RTP),
so WSL2's limited audio support is a non-issue.

## Alternative: Docker Desktop

Use the prebuilt container — see [SETUP_DOCKER.md](SETUP_DOCKER.md). One caveat: Docker
Desktop runs in a VM, so RTP media needs an explicit published UDP port:

```powershell
docker build -t ringback .
docker run -i --rm -e VOICE_RTP_PORT=4000 -p 4000:4000/udp -p 4001:4001/udp `
  --env-file voice.docker.env ringback
```

(`voice.docker.env` is the plain `KEY=val` form of your creds — see the conversion step in
[SETUP_DOCKER.md](SETUP_DOCKER.md), since `docker --env-file` can't read `voice.env`'s
`export` syntax.) This port mapping tested working with two-way audio on Docker Desktop for
Mac; if a connected call has no audio on your network, prefer WSL2, where host networking
avoids RTP/NAT entirely.

## Not supported: native Windows build

A native MSVC/SWIG pjsua2 build is intentionally not documented — it's brittle and offers
no benefit here, since the engine is headless and runs identically under WSL2/Docker. The
pure-Python pieces (`platform_compat.py`, the turn/capture logic) do include Windows
support (`GetLastInputInfo` idle detection, detached-process flags, SAPI TTS) so that a
future native port is possible, but pjsua2 remains the blocker we route around.
