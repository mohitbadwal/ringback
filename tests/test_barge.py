#!/usr/bin/env python3
"""Validate barge-in against the REAL harvested speakerphone audio (no AEC needed — the
phone cancels echo). Feeds the recorded near-end RMS through the SAME voice_agent._BargeState
the live call uses, with the SAME during-TX-floor threshold logic:

  echo_near  (me speaking, user silent)      -> must NOT detect a barge,
  barge_near (user talking over me)          -> MUST detect a barge.

This is the real-data check the earlier synthetic AEC work couldn't be: it runs only if the
harvest fixtures exist (./tests/run_harvest.sh). Skips cleanly otherwise.
"""
import importlib.util
import json
import math
import os
import struct
import sys
import types
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REAL = os.path.join(HERE, "fixtures", "real")

pj = types.ModuleType("pjsua2")
pj.Call = object
sys.modules["pjsua2"] = pj
spec = importlib.util.spec_from_file_location("voice_agent", os.path.join(ROOT, "voice_agent.py"))
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

POLL, WIN = 0.08, 0.25     # live poll cadence + _tail_rms window


def rms_window(samples, sr, t):
    a = max(0, int((t - WIN) * sr))
    b = min(len(samples), int(t * sr))
    if b <= a:
        return 0.0
    seg = samples[a:b]
    return math.sqrt(sum(v * v for v in seg) / len(seg))


def scan(path, pre_roll):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        samples = struct.unpack("<%dh" % n, w.readframes(n))
    dur = n / sr
    # during-TX floor: near-end just after our TTS starts (skip the harvest pre-roll),
    # exactly as live: measured at ~0.1-0.4s into our speech.
    floor_samps = [rms_window(samples, sr, pre_roll + x) for x in (0.15, 0.25, 0.35)]
    tx_floor = sum(floor_samps) / len(floor_samps)
    thresh = min(max(va.BARGE_RMS_MIN, tx_floor * 2.0), va.RMS_CAP)
    state = va._BargeState(thresh)
    verdict, at = None, None
    t = pre_roll
    while t < dur:
        live_t = t - pre_roll                       # time since our speech began
        v = state.feed(live_t, rms_window(samples, sr, t))
        if v == "barge":
            verdict, at = "barge", live_t
            break
        if v == "echo":
            verdict = "echo"                         # keep scanning (live goes half-duplex)
        t += POLL
    return {"thresh": thresh, "tx_floor": tx_floor, "verdict": verdict, "at": at}


def main():
    if not os.path.exists(os.path.join(REAL, "barge_near.wav")):
        print("no harvested audio — run ./tests/run_harvest.sh first. SKIP.")
        return 0
    pre = json.load(open(os.path.join(REAL, "meta.json"))).get("pre_roll_sec", 0.5)
    results = []

    e = scan(os.path.join(REAL, "echo_near.wav"), pre)
    no_barge = e["verdict"] != "barge"
    results.append(no_barge)
    print(f"echo_near  (silent user): thresh={e['thresh']:.0f} txfloor={e['tx_floor']:.0f} "
          f"verdict={e['verdict']}  -> {'PASS (no false barge)' if no_barge else 'FAIL'}")

    b = scan(os.path.join(REAL, "barge_near.wav"), pre)
    got_barge = b["verdict"] == "barge"
    results.append(got_barge)
    print(f"barge_near (talk-over) : thresh={b['thresh']:.0f} txfloor={b['tx_floor']:.0f} "
          f"verdict={b['verdict']} at={b['at']}  -> {'PASS (barge detected)' if got_barge else 'FAIL'}")

    print("\nRESULT:", "ALL PASS — barge-in works on real audio, no AEC"
          if all(results) else "FAILURES ABOVE")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
