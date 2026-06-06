#!/usr/bin/env python3
"""Prove the echo failure class AND that AEC fixes it — fully offline.

  near = user's voice + delayed/attenuated copy of OUR TTS (speaker echo) + noise.
  1. transcribe the RAW near-end  -> expect contamination / dropped user words (the bug),
  2. run it through aec.AecProcessor (feed_far = our TTS, process_near = near),
  3. transcribe the CLEANED near-end -> expect the user's words recovered.

Run under the python that has livekit:  /opt/anaconda3/bin/python3 tests/test_aec.py
"""
import importlib.util
import os
import re
import struct
import sys
import tempfile
import types
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from mix_echo import mix_echo, SR  # noqa: E402
import aec  # noqa: E402

pj = types.ModuleType("pjsua2")
pj.Call = object
sys.modules["pjsua2"] = pj
spec = importlib.util.spec_from_file_location("voice_agent", os.path.join(ROOT, "voice_agent.py"))
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

FIX = os.path.join(HERE, "fixtures")


def _words(s):
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w]


def _overlap(expected, got):
    exp, g = _words(expected), set(_words(got))
    return sum(1 for w in exp if w in g) / len(exp) if exp else 0.0


def _transcribe_pcm(pcm: bytes) -> str:
    dst = tempfile.mktemp(suffix=".wav")
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    out = va._transcribe_stream(dst)
    va._rm(dst)
    return out


def _aec_clean(near_wav: str, far_pcm: bytes, delay_ms: float) -> bytes:
    """Feed far (our TTS) + near in lockstep 20 ms chunks; return cleaned PCM."""
    with wave.open(near_wav, "rb") as w:
        near = w.readframes(w.getnframes())
    proc = aec.AecProcessor(delay_ms=delay_ms)
    CH = 640  # 20 ms @ 16 kHz int16 (pjsua-style); wrapper re-chunks to 10 ms
    cleaned = bytearray()
    for i in range(0, max(len(near), len(far_pcm)), CH):
        fchunk = far_pcm[i:i + CH]
        if fchunk:
            proc.feed_far(fchunk)
        nchunk = near[i:i + CH]
        if nchunk:
            cleaned += proc.process_near(nchunk)
    return bytes(cleaned)


def main():
    user_wav = os.path.join(FIX, "long_sentence.wav")
    expected = open(os.path.join(FIX, "long_sentence.txt")).read()
    # our spoken line that echoes back (different words, so contamination is visible)
    tts_wav = va._tts_to_wav("Okay, here is the current status of your deployment pipeline.")
    delay_ms = 150.0
    near_wav, far = mix_echo(user_wav, tts_wav, tempfile.mktemp(suffix=".wav"),
                             delay_ms=delay_ms, atten=0.6, noise_amp=250, user_start_ms=0.0)
    with wave.open(near_wav, "rb") as w:
        near_pcm = w.readframes(w.getnframes())

    raw = _transcribe_pcm(near_pcm)
    cleaned_pcm = _aec_clean(near_wav, far, delay_ms)
    cleaned = _transcribe_pcm(cleaned_pcm)

    raw_ov, clean_ov = _overlap(expected, raw), _overlap(expected, cleaned)
    print(f"USER (expected): {expected!r}")
    print(f"OUR ECHO       : 'Okay, here is the current status of your deployment pipeline.'")
    print(f"\nRAW near-end   : {raw!r}\n  user-word overlap: {raw_ov:.0%}")
    print(f"\nAEC cleaned    : {cleaned!r}\n  user-word overlap: {clean_ov:.0%}")
    improved = clean_ov - raw_ov
    print(f"\nAEC improved user-word recovery by {improved:+.0%}")
    va._rm(near_wav)
    va._rm(tts_wav)
    ok = clean_ov >= 0.7 and clean_ov > raw_ov
    print("RESULT:", "PASS — AEC recovers the user under echo" if ok else "INVESTIGATE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
