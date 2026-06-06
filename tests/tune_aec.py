#!/usr/bin/env python3
"""Tune + validate barge-in AEC against the REAL recordings harvested by harvest_call.py
(NOT synthetic echo). Run AFTER one harvest call; needs livekit:

    /opt/anaconda3/bin/python3 tests/tune_aec.py

What it does:
  1. echo pair (echo_near = our echo only): sweep VOICE_AEC_DELAY_MS, pick the delay that
     cancels the most real echo energy. That delay goes into voice.env.
  2. barge pair (barge_near = echo + user talking over us): with the best delay, check the
     gating bar that actually matters — cleaned echo RMS must sit BELOW the user's RMS, so
     barge-in fires on the user and not on our echo. (Bar is RMS separation, not 36 dB.)
"""
import json
import math
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import aec  # noqa: E402

REAL = os.path.join(HERE, "fixtures", "real")


def read_pcm(path):
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.readframes(w.getnframes())


def rms(pcm, a=0, b=None):
    n = len(pcm) // 2
    b = n if b is None else min(b, n)
    if b <= a:
        return 0.0
    xs = struct.unpack("<%dh" % (b - a), pcm[a * 2:b * 2])
    return math.sqrt(sum(v * v for v in xs) / len(xs))


def clean(near, far, delay_ms, pre_roll, sr):
    """Align far->near by skipping the pre-roll, feed both in lockstep, return cleaned near."""
    skip = int(pre_roll * sr) * 2
    near = near[skip:]
    proc = aec.AecProcessor(delay_ms=delay_ms)
    CH = 640
    out = bytearray()
    for i in range(0, max(len(near), len(far)), CH):
        if far[i:i + CH]:
            proc.feed_far(far[i:i + CH])
        if near[i:i + CH]:
            out += proc.process_near(near[i:i + CH])
    return bytes(out)


def main():
    if not os.path.exists(os.path.join(REAL, "echo_near.wav")):
        print("no harvested audio yet — run ./tests/run_harvest.sh first (one call).")
        return 2
    meta = json.load(open(os.path.join(REAL, "meta.json")))
    sr, pre = meta["sample_rate"], meta["pre_roll_sec"]

    # 1. sweep delay on the echo-only recording
    _, en = read_pcm(os.path.join(REAL, "echo_near.wav"))
    _, ef = read_pcm(os.path.join(REAL, "echo_far.wav"))
    raw = rms(en, int(pre * sr))
    print(f"echo-only raw near RMS = {raw:.0f}\nsweep VOICE_AEC_DELAY_MS:")
    best = (None, -1)
    for d in (40, 70, 100, 130, 160, 200, 250, 300):
        c = clean(en, ef, d, pre, sr)
        r = rms(c)
        red = 20 * math.log10((raw + 1) / (r + 1))
        print(f"  delay={d:3d}ms  cleaned RMS {r:6.0f}  reduction {red:5.1f} dB")
        if red > best[1]:
            best = (d, red)
    print(f"-> best delay = {best[0]}ms ({best[1]:.1f} dB).  Set VOICE_AEC_DELAY_MS={best[0]}")

    # 2. gating check on the barge recording (echo + user over us)
    _, bn = read_pcm(os.path.join(REAL, "barge_near.wav"))
    _, bf = read_pcm(os.path.join(REAL, "barge_far.wav"))
    cleaned = clean(bn, bf, best[0], pre, sr)
    # crude split: first second after pre-roll ~ echo establishing; whole clip RMS as ref.
    # The real test is whether ANY sustained region clears: print a moving-RMS summary.
    win = int(0.3 * sr)
    mins, maxs = 1e9, 0.0
    for i in range(0, len(cleaned) // 2 - win, win):
        r = rms(cleaned, i, i + win)
        mins, maxs = min(mins, r), max(maxs, r)
    print(f"\nbarge (echo+user) cleaned moving-RMS: min={mins:.0f} (echo-only floor) "
          f"max={maxs:.0f} (user talking)")
    margin = maxs / (mins + 1)
    print(f"gating margin (user / echo-floor) = {margin:.1f}x  "
          f"({'GOOD — barge gateable' if margin > 3 else 'TIGHT — needs a higher threshold or more delay tuning'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
