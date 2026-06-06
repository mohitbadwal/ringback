#!/usr/bin/env python3
"""Offline proof of the turn-logic fixes (no phone):
  1. late-starting user (pause before answering) is NO LONGER dropped — the bug,
  2. the turn ends ~END_SILENCE (1.5s) after the user's last word (responsive),
  3. whisper silence-hallucinations ("you"/"thank you"/...) never count as a word,
  4. a true-silence turn ends by START_TIMEOUT with "",
  5. a mid-turn hang-up bails at once (returns None).
"""
import importlib.util
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from wav_feeder import GrowingWav  # noqa: E402

pj = types.ModuleType("pjsua2")
pj.Call = object
sys.modules["pjsua2"] = pj
spec = importlib.util.spec_from_file_location("voice_agent", os.path.join(ROOT, "voice_agent.py"))
va = importlib.util.module_from_spec(spec)
spec.loader.exec_module(va)

FIX = os.path.join(HERE, "fixtures")
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")


def capture(fixture, rate=1.0, start_delay=0.0, disconnect_after=None):
    dst = tempfile.mktemp(suffix=".wav")
    # trailing silence so end-of-turn energy actually drops (the live recorder keeps going)
    f = GrowingWav(os.path.join(FIX, fixture + ".wav"), dst, rate=rate,
                   start_delay=start_delay, trailing_silence=2.5).start()
    flag = {"d": False}
    if disconnect_after is not None:
        import threading
        threading.Thread(target=lambda: (time.sleep(disconnect_after), flag.__setitem__("d", True)),
                         daemon=True).start()
    t0 = time.time()
    streamed = va._capture_turn(lambda: va._wav_snapshot(dst), lambda: flag["d"], max_sec=22.0,
                                energy_fn=lambda: va._tail_rms(dst, 0.3))
    dt = time.time() - t0
    final = ""
    if streamed is not None:
        snap = va._wav_snapshot(dst)
        if snap:
            final = va._transcribe_stream(snap)
            va._rm(snap)
    f.stop()
    va._rm(dst)
    return streamed, (final or (streamed or "")), dt


def main():
    if not os.path.exists(os.path.join(FIX, "long_sentence.wav")):
        print("fixtures missing — run: python3 tests/gen_fixtures.py")
        return 2
    print(f"START_TIMEOUT={va.START_TIMEOUT}s  END_SILENCE={va.END_SILENCE}s\n")

    # 1. THE BUG: user pauses 2.5s before speaking — must NOT be dropped now.
    _, text, _ = capture("long_sentence", start_delay=2.5)
    check("late-starting user (2.5s pause) is captured", len(text.split()) >= 6, repr(text[:50]))

    # 2. endpoint: after the user stops, the turn ends ~END_SILENCE later (not max_sec).
    _, text2, dt2 = capture("short_reply", start_delay=0.0)
    # short_reply ~1.7s audio; turn should end ~1.7 + END_SILENCE, well under max_sec
    check("turn ends shortly after last word (responsive)", dt2 < 5.0, f"{dt2:.1f}s")
    check("short reply captured", "production" in text2.lower(), repr(text2))

    # 3. hallucination filter: bare silence-fillers are dropped, real short answers kept.
    check("'you' (hallucination) -> dropped", va._clean_text("you") == "")
    check("'Thank you.' (hallucination) -> dropped", va._clean_text("Thank you.") == "")
    check("'[BLANK_AUDIO]' -> dropped", va._clean_text("[BLANK_AUDIO]") == "")
    check("'Yes, go with production.' (real) -> kept",
          va._clean_text("Yes, go with production.") == "Yes, go with production.")
    check("'Okay thanks for the help' (real) -> kept",
          va._clean_text("Okay thanks for the help") == "Okay thanks for the help")

    # 4. true silence -> ends by START_TIMEOUT with "".
    _, text4, dt4 = capture("silence")
    check("pure silence -> empty by START_TIMEOUT", text4 == "" and dt4 <= va.START_TIMEOUT + 1.5,
          f"{dt4:.1f}s, {text4!r}")

    # 5. hang-up mid-turn -> None at once.
    streamed5, _, dt5 = capture("long_sentence", disconnect_after=1.0)
    check("hang-up mid-turn bails at once (None)", streamed5 is None and dt5 < 2.0, f"{dt5:.1f}s")

    print("\nRESULT:", "ALL PASS" if all(results) else "FAILURES ABOVE")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
