#!/usr/bin/env python3
"""One command to verify the voice engine OFFLINE — no phone call.

    python3 tests/run_all.py

Runs every offline test with the right interpreter (the AEC test needs the Python that
has livekit) and prints a single pass/fail summary. This is the "test it with a script"
entry point: capture, turn-logic, hallucination filter, frame-port de-risk, and the AEC
core all checked without dialing anyone. (Live confirmation + real-echo AEC tuning is the
separate one-call step: ./tests/run_harvest.sh then tests/tune_aec.py.)
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SYS = sys.executable
# the AEC test imports livekit — prefer a python that has it
ANACONDA = "/opt/anaconda3/bin/python3"
LIVEKIT_PY = ANACONDA if os.path.exists(ANACONDA) else SYS

SUITE = [
    ("capture (full-sentence, lagging recorder)", "test_capture.py", SYS),
    ("turn-logic (late-start fix, 1.5s endpoint, hallucination filter)", "test_turnlogic.py", SYS),
    ("frame-port de-risk (records the NO-GO finding)", "test_frameports.py", SYS),
    ("AEC core (echo cancellation on synthetic)", "test_aec.py", LIVEKIT_PY),
    ("barge-in on REAL harvested audio (skips if no harvest)", "test_barge.py", SYS),
]


def gen_fixtures_if_needed():
    if not os.path.exists(os.path.join(HERE, "fixtures", "long_sentence.wav")):
        print("generating fixtures...")
        subprocess.run([SYS, os.path.join(HERE, "gen_fixtures.py")], check=True)


def main():
    if not shutil.which("whisper-cli") and not shutil.which("whisper-server"):
        print("warning: whisper not found in PATH; transcription tests will be weak")
    gen_fixtures_if_needed()
    results = []
    for name, script, py in SUITE:
        print(f"\n{'=' * 70}\n# {name}\n{'=' * 70}")
        rc = subprocess.run([py, "-u", os.path.join(HERE, script)]).returncode
        results.append((name, rc == 0))
    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    allok = all(ok for _, ok in results)
    print("\nOFFLINE RESULT:", "ALL PASS" if allok else "FAILURES ABOVE")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
