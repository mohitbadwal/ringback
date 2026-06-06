#!/usr/bin/env python3
"""Reproduce the "said a full sentence, captured nothing" bug OFFLINE — no phone.

Feeds a real speech fixture through a GrowingWav (a stale-header recorder simulator)
and runs the REAL voice_agent._capture_turn + _wav_snapshot + _transcribe_stream path
(test_voice_listen.py mocked the transcriber, so it never saw this bug). Logs per-poll
transcript growth so we can SEE where capture drops, then asserts the sentence is caught.
"""
import importlib.util
import os
import re
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from wav_feeder import GrowingWav  # noqa: E402

# --- import voice_agent WITHOUT real pjsua2 (the functions under test don't use it) ---
pj = types.ModuleType("pjsua2")
pj.Call = object
sys.modules["pjsua2"] = pj
spec = importlib.util.spec_from_file_location("voice_agent", os.path.join(ROOT, "voice_agent.py"))
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

FIX = os.path.join(HERE, "fixtures")


def _words(s: str):
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w]


def _overlap(expected: str, got: str) -> float:
    exp, g = _words(expected), set(_words(got))
    if not exp:
        return 0.0
    return sum(1 for w in exp if w in g) / len(exp)


def _final_snapshot_transcribe(path: str) -> str:
    snap = va._wav_snapshot(path)
    out = va._transcribe_stream(snap) if snap else ""
    if snap:
        va._rm(snap)
    return out


def run_capture(fixture: str, rate: float, label: str) -> dict:
    """Mirror listen(): GrowingWav recorder -> _capture_turn -> final snapshot+transcribe."""
    src = os.path.join(FIX, fixture + ".wav")
    expected = open(os.path.join(FIX, fixture + ".txt")).read() if os.path.exists(
        os.path.join(FIX, fixture + ".txt")) else ""
    dst = tempfile.mktemp(suffix=".wav")
    feeder = GrowingWav(src, dst, rate=rate).start()

    # wrap the REAL transcriber to log every poll (input audio-sec + output text)
    polls = []
    t0 = time.time()
    real_ts = va._transcribe_stream

    def logged(wav):
        r = real_ts(wav)
        polls.append((round(time.time() - t0, 2), r))
        return r

    va._transcribe_stream = logged
    try:
        streamed = va._capture_turn(lambda: va._wav_snapshot(dst), lambda: False,
                                    max_sec=20.0)
        final = _final_snapshot_transcribe(dst)
    finally:
        va._transcribe_stream = real_ts
        feeder.stop()
        va._rm(dst)

    result = final or streamed or ""
    ov = _overlap(expected, result)
    print(f"\n=== {label} (rate={rate}, audio={feeder.audio_sec:.1f}s) ===")
    for el, t in polls:
        print(f"  t={el:5.2f}s  -> {t[:70]!r}")
    print(f"  STREAMED: {streamed!r}")
    print(f"  FINAL   : {final!r}")
    print(f"  EXPECTED: {expected!r}")
    print(f"  OVERLAP : {ov:.0%}")
    return {"label": label, "result": result, "overlap": ov, "expected": expected}


def main():
    if not os.path.exists(os.path.join(FIX, "long_sentence.wav")):
        print("fixtures missing — run: python3 tests/gen_fixtures.py")
        return 2
    cases = [
        ("long_sentence", 1.0, "long sentence, real-time recorder"),
        ("long_sentence", 0.6, "long sentence, LAGGING recorder (0.6x)"),
        ("short_reply", 1.0, "short reply, real-time"),
    ]
    results = [run_capture(f, r, lbl) for f, r, lbl in cases]
    print("\n" + "=" * 60)
    ok = True
    for r in results:
        passed = r["overlap"] >= 0.7
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {r['label']}: {r['overlap']:.0%} captured")
    print("=" * 60)
    print("RESULT:", "ALL PASS" if ok else "FAILURES (capture bug reproduced above)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
