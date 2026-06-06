#!/usr/bin/env python3
"""Verify the AEC core does the job barge-in actually needs: kill our ECHO ENERGY while
preserving the user's voice — so the RMS barge detector fires on the user, not on our
echo. (Echo masking whisper TRANSCRIPTION turned out NOT to be the issue — whisper is
robust to linear echo; AEC's payoff is the RMS separation. See plan / test_capture.py.)

Synthetic here (proves the math + chunking wrapper). The REAL speakerphone-echo tuning is
tests/tune_aec.py against harvested audio. Run under a livekit python:
    /opt/anaconda3/bin/python3 tests/test_aec.py
"""
import importlib.util
import os
import struct
import sys
import tempfile
import types
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from mix_echo import SR, _read  # noqa: E402
import aec  # noqa: E402

pj = types.ModuleType("pjsua2")
pj.Call = object
sys.modules["pjsua2"] = pj
spec = importlib.util.spec_from_file_location("voice_agent", os.path.join(ROOT, "voice_agent.py"))
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

FIX = os.path.join(HERE, "fixtures")
import math


def rms(pcm, a, b):
    xs = struct.unpack("<%dh" % (len(pcm) // 2), pcm)[a:b]
    return math.sqrt(sum(v * v for v in xs) / len(xs)) if xs else 0.0


def main():
    user = _read(os.path.join(FIX, "long_sentence.wav"))
    tts_wav = va._tts_to_wav(
        "Okay, here is the current status of your deployment pipeline running right now.")
    tts = _read(tts_wav)
    delay = 150
    d = int(SR * delay / 1000)
    # near = our echo (delayed/attenuated TTS) everywhere + the user ONLY in the 2nd half,
    # so we have a clean echo-only window (barge must NOT fire) and a user window (must fire)
    user_start = int(3.0 * SR)
    n = max(len(tts) + d, user_start + len(user)) + SR
    near = [0] * n
    for i, s in enumerate(tts):
        near[i + d] += int(0.7 * s)
    for i, s in enumerate(user):
        if user_start + i < n:
            near[user_start + i] += s
    near_pcm = struct.pack("<%dh" % n, *[max(-32767, min(32767, x)) for x in near])
    far = struct.pack("<%dh" % len(tts), *[max(-32767, min(32767, s)) for s in tts])

    proc = aec.AecProcessor(delay_ms=delay)
    CH = 640
    cleaned = bytearray()
    for i in range(0, max(len(near_pcm), len(far)), CH):
        if far[i:i + CH]:
            proc.feed_far(far[i:i + CH])
        if near_pcm[i:i + CH]:
            cleaned += proc.process_near(near_pcm[i:i + CH])
    cleaned = bytes(cleaned)

    eo = (int(0.6 * SR), int(2.6 * SR))      # echo-only window
    uw = (int(3.5 * SR), int(5.5 * SR))      # user-present window
    raw_echo, cl_echo = rms(near_pcm, *eo), rms(cleaned, *eo)
    raw_user, cl_user = rms(near_pcm, *uw), rms(cleaned, *uw)
    red = 20 * math.log10((raw_echo + 1) / (cl_echo + 1))
    user_keep = cl_user / (raw_user + 1)
    va._rm(tts_wav)

    print(f"ECHO-ONLY  raw {raw_echo:6.0f} -> cleaned {cl_echo:6.0f}   reduction {red:5.1f} dB")
    print(f"USER       raw {raw_user:6.0f} -> cleaned {cl_user:6.0f}   preserved {user_keep:.0%}")
    print(f"gating: cleaned echo floor {cl_echo:.0f}  vs  user {cl_user:.0f}  "
          f"-> {'SEPARABLE' if cl_user > cl_echo * 3 else 'too close'}")

    ok = red >= 15 and user_keep >= 0.6 and cl_user > cl_echo * 3
    print("RESULT:", "PASS — echo energy killed, user preserved, barge gateable" if ok
          else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
