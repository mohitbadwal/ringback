#!/usr/bin/env python3
"""GO/NO-GO de-risk (durable record of the finding): can a Python pjsua2 AudioMediaPort
be used to intercept audio FRAMES for live AEC?

Runs _frameport_probe.py in isolated subprocesses (a crash is the signal, so it must be
isolated) and reads exit codes. Finding on this pjsua2 build (macOS, cpython-3.13):

  count  -> survives        (the onFrameReceived callback itself fires fine from Python)
  copy   -> SEGFAULT (139)  (reading audio out via frame.buf.copy_to_bytearray crashes
                             under concurrent media — i.e. exactly during a live call)
  source -> survives alone, but two Python ports / data access under load are unstable

CONCLUSION: the Python AudioMediaPort path CANNOT be used to pull near-end audio for AEC
on this build. AEC must instead be fed from the C++ AudioMediaRecorder's growing file via
a normal Python thread (no media-thread Python callback). This test exists so the result
stays reproducible — re-run it if pjsua2 is ever rebuilt.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pj_env import pj_env  # noqa: E402

WORKER = os.path.join(HERE, "_frameport_probe.py")


def run(mode):
    p = subprocess.run([sys.executable, "-u", WORKER, mode], env=pj_env(),
                       capture_output=True, text=True, timeout=30)
    line = next((l for l in p.stdout.splitlines() if l.startswith(mode + ":")), "")
    return p.returncode, line


def main():
    if not os.path.exists(os.path.join(HERE, "fixtures", "long_sentence.wav")):
        print("fixtures missing — run: python3 tests/gen_fixtures.py")
        return 2
    print("Probing pjsua2 Python AudioMediaPort frame access (each in its own process):\n")
    results = {}
    for mode in ("count", "copy", "source"):
        rc, line = run(mode)
        results[mode] = rc
        tag = "survived" if rc == 0 else f"CRASHED (exit {rc})"
        print(f"  {mode:7s}: {tag:20s} {line}")

    # The de-risk verdict: callbacks fire (count ok) but frame-DATA access crashes (copy).
    finding_holds = results.get("count") == 0 and results.get("copy") != 0
    print()
    if finding_holds:
        print("VERDICT: NO-GO for Python AudioMediaPort AEC — callbacks fire but reading frame")
        print("         audio crashes under load. Feed AEC from the AudioMediaRecorder file")
        print("         via a Python thread instead (no media-thread Python callback).")
    else:
        print("VERDICT: behavior CHANGED — copy no longer crashes. The Python AudioMediaPort")
        print("         AEC path may now be viable; re-evaluate (pjsua2 likely rebuilt).")
    # This test PASSES when it successfully establishes the finding (either direction is
    # informative); it fails only if the worker couldn't run at all.
    return 0 if results.get("count") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
