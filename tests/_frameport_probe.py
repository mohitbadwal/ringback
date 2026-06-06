#!/usr/bin/env python3
"""Worker for test_frameports.py — run ONE mode in a pjsua2 process, then os._exit(0).
A segfault/abort here is the POINT (the parent reads the exit code), so it must be an
isolated subprocess. Modes:
  count  - onFrameReceived only increments a counter (no frame-buffer access)
  copy   - onFrameReceived reads the audio out via frame.buf.copy_to_bytearray()
  source - onFrameRequested writes audio via frame.buf.assign_from_bytes()
Each runs a Python AudioMediaPort WHILE a concurrent C++ player->recorder pumps media
(what a live call does). argv[1] = mode.
"""
import os
import sys
import tempfile
import time

import pjsua2 as pj
import voice_agent as va

SR = 16000
MODE = sys.argv[1] if len(sys.argv) > 1 else "count"
HERE = os.path.dirname(os.path.abspath(__file__))


def make_fmt():
    f = pj.MediaFormatAudio()
    f.type = pj.PJMEDIA_TYPE_AUDIO
    f.clockRate = SR
    f.channelCount = 1
    f.bitsPerSample = 16
    f.frameTimeUsec = 20000
    f.avgBps = SR * 16
    f.maxBps = SR * 16
    return f


class Port(pj.AudioMediaPort):
    def __init__(self):
        super().__init__()
        self.frames = 0

    def onFrameReceived(self, frame):
        self.frames += 1
        if MODE == "copy":
            b = bytearray()
            frame.buf.copy_to_bytearray(b)        # <-- reads audio out; crashes under load

    def onFrameRequested(self, frame):
        self.frames += 1
        size = frame.size if getattr(frame, "size", 0) else 320
        frame.type = pj.PJMEDIA_FRAME_TYPE_AUDIO
        if MODE == "source":
            frame.buf.assign_from_bytes(b"\x00" * size)
        frame.size = size


def main():
    wav = os.path.join(HERE, "fixtures", "long_sentence.wav")
    s = va.CallSession()
    s.start_lib()
    fmt = make_fmt()
    port = Port()
    port.createPort("p", fmt)
    if MODE == "source":
        rec = pj.AudioMediaRecorder()
        rec.createRecorder(tempfile.mktemp(suffix=".wav"))
        port.startTransmit(rec)
    else:
        player = pj.AudioMediaPlayer()
        player.createPlayer(wav, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(port)
    # concurrent C++ media (a 2nd player -> recorder), like TTS playing during a call
    tts = pj.AudioMediaPlayer()
    tts.createPlayer(os.path.join(HERE, "fixtures", "mid_sentence.wav"), pj.PJMEDIA_FILE_NO_LOOP)
    rec2 = pj.AudioMediaRecorder()
    rec2.createRecorder(tempfile.mktemp(suffix=".wav"))
    tts.startTransmit(rec2)
    time.sleep(1.5)
    print(f"{MODE}:frames={port.frames}", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()
