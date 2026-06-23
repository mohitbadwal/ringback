# Dockerfile — full ringback-VOICE image (and ringback-alert).
#
# This builds the complete voice stack in a Linux container: pjproject + the pjsua2
# Python bindings, whisper.cpp (STT), Piper (neural TTS) + a voice, and ffmpeg. The
# engine runs HEADLESS — it never opens a local mic/speaker (all media is WAV<->SIP/RTP),
# so no sound card or X server is needed in the container. This is the universal runtime:
# the same image runs on Linux, on Windows (Docker Desktop / WSL2), and on macOS.
#
# (Supersedes the old alert-only note: ringback-voice IS containerizable — that's this.)
#
# Build:  docker build -t ringback .
# Run  :  docker run -i --rm --network host --env-file voice.env ringback
# See docs/SETUP_DOCKER.md for networking (RTP) details.

FROM python:3.12-slim AS base

ARG PJ_VER=2.17
ENV DEBIAN_FRONTEND=noninteractive \
    PJDIR=/opt/pjproject-${PJ_VER} \
    WHISPER_MODEL_DIR=/root/.whisper-models \
    PIPER_DIR=/root/.piper-voices

# --- system toolchain + runtime libs ------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential swig cmake git curl ca-certificates xz-utils \
        libssl-dev libopus-dev libsdl2-dev libasound2-dev \
        ffmpeg espeak-ng baresip \
    && rm -rf /var/lib/apt/lists/*

# setuptools/wheel are needed by the pjsua2 bindings' setup.py — Python 3.12 dropped
# distutils from the stdlib and slim images ship no setuptools. (setuptools provides the
# distutils shim too.) Must come BEFORE the bindings build below.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# --- pjproject + pjsua2 python bindings (Linux builds clean; no namespace relink) ----
RUN curl -fsSL "https://github.com/pjsip/pjproject/archive/refs/tags/${PJ_VER}.tar.gz" \
        -o /tmp/pj.tgz \
    && tar xzf /tmp/pj.tgz -C /opt && rm /tmp/pj.tgz \
    && cd ${PJDIR} \
    && CFLAGS="-fPIC -O2" ./configure --enable-shared --with-ssl \
    && make dep && make \
    && cd ${PJDIR}/pjsip-apps/src/swig/python \
    && CFLAGS="-std=c++11 -fPIC -O2" CXXFLAGS="-std=c++11 -fPIC -O2" make \
    && mkdir -p /opt/pjsua2 \
    && cp -r $(ls -d build/lib.*/ | head -1)/* /opt/pjsua2/ \
    # make the pjproject shared libs globally resolvable
    && for d in pjlib pjlib-util pjnath pjmedia pjsip third_party; do \
         find ${PJDIR}/$d/lib -name '*.so*' -exec cp -a {} /usr/local/lib/ \; ; done \
    && ldconfig

# --- whisper.cpp (whisper-cli + whisper-server) -------------------------------
# GGML_NATIVE=OFF is REQUIRED: the default (ON) bakes in -march=native CPU instructions from
# the BUILD machine (e.g. a GitHub Ampere ARM runner). Those crash with SIGILL on a different
# CPU at runtime — e.g. Apple Silicon under Docker Desktop — so whisper died on every call.
# OFF builds a portable baseline binary that runs on any amd64 / arm64.
RUN git clone --depth 1 https://github.com/ggerganov/whisper.cpp /opt/whisper.cpp \
    && cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build \
        -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DGGML_NATIVE=OFF >/dev/null \
    && cmake --build /opt/whisper.cpp/build -j --target whisper-cli whisper-server \
    && cp /opt/whisper.cpp/build/bin/whisper-cli /opt/whisper.cpp/build/bin/whisper-server \
          /usr/local/bin/ \
    && find /opt/whisper.cpp/build -name '*.so*' -exec cp -a {} /usr/local/lib/ \; \
    && ldconfig \
    && rm -rf /opt/whisper.cpp/build/CMakeFiles

# --- Python deps + Piper neural TTS -------------------------------------------
RUN pip install --no-cache-dir "mcp>=1.2.0" httpx piper-tts

# --- models (baked in so the image works out of the box; override via volume) ---
# whisper streaming server model (base.en) — the hot path.
RUN mkdir -p ${WHISPER_MODEL_DIR} \
    && curl -fL "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" \
        -o ${WHISPER_MODEL_DIR}/ggml-base.en.bin
# Piper voice (en_US-lessac-medium) + its required .onnx.json config.
RUN mkdir -p ${PIPER_DIR} \
    && PV=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium \
    && curl -fL "$PV/en_US-lessac-medium.onnx"      -o ${PIPER_DIR}/en_US-lessac-medium.onnx \
    && curl -fL "$PV/en_US-lessac-medium.onnx.json" -o ${PIPER_DIR}/en_US-lessac-medium.onnx.json

# --- runtime env --------------------------------------------------------------
ENV PYTHONPATH=/opt/pjsua2:/app \
    LD_LIBRARY_PATH=/usr/local/lib \
    WHISPER_SERVER_MODEL=/root/.whisper-models/ggml-base.en.bin \
    WHISPER_MODEL=/root/.whisper-models/ggml-base.en.bin \
    VOICE_PIPER_MODEL=/root/.piper-voices/en_US-lessac-medium.onnx \
    VOICE_TTS=piper \
    VOICE_NULL_AUDIO=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY voice_mcp.py voice_agent.py platform_compat.py aec.py server.py \
     voice.env.example alert.env.example ./
COPY tests/ ./tests/

# stdio MCP server — speaks the MCP protocol over stdin/stdout.
CMD ["python", "voice_mcp.py"]
