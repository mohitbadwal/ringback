#!/usr/bin/env python3
"""Generate deterministic audio fixtures for the offline voice harness.

Synthesizes 16 kHz mono 16-bit WAVs (the format the live recorder produces) via the
cross-platform TTS seam (platform_compat.synthesize_to_wav — Piper by default, else the
OS-native voice), so the capture/turn-logic/AEC tests run with NO phone call on macOS,
Linux, or in the Docker image. Each speech fixture also writes a `.txt` with its expected
transcript (the tests assert on word overlap, so the exact voice doesn't matter).

Run:  python3 tests/gen_fixtures.py
Output: tests/fixtures/*.wav (+ *.txt)
"""
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from platform_compat import synthesize_to_wav  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
SR = 16000

# (name, text) — distinctive words so the asserts are unambiguous.
SPEECH = [
    ("long_sentence",
     "Let's go with the production database migration tonight, "
     "and roll back immediately if the error rate climbs above five percent."),
    ("short_reply", "Yes, go with production."),
    ("mid_sentence",
     "I think we should wait until the weekend before deploying this change."),
]


def _synth_wav(text: str, dst: str) -> None:
    synthesize_to_wav(text, dst)   # any engine -> 16 kHz mono 16-bit WAV


def _silence_wav(dst: str, seconds: float) -> None:
    n = int(SR * seconds)
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"\x00\x00" * n)


def _noisy_speech(src_wav: str, dst: str, noise_amp: int = 600) -> None:
    """Mix low-level white noise into a speech clip (room/line noise)."""
    with wave.open(src_wav, "rb") as w:
        n = w.getnframes()
        pcm = w.readframes(n)
    import random
    rng = random.Random(7)
    vals = list(struct.unpack("<%dh" % (len(pcm) // 2), pcm))
    out = bytearray()
    for v in vals:
        s = max(-32767, min(32767, v + rng.randint(-noise_amp, noise_amp)))
        out += struct.pack("<h", s)
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(out))


def main() -> None:
    os.makedirs(FIX, exist_ok=True)
    for name, text in SPEECH:
        wav = os.path.join(FIX, name + ".wav")
        _synth_wav(text, wav)
        with open(os.path.join(FIX, name + ".txt"), "w") as f:
            f.write(text)
        dur = wave.open(wav, "rb").getnframes() / SR
        print(f"  {name}.wav  {dur:.1f}s  \"{text[:48]}...\"")
    _silence_wav(os.path.join(FIX, "silence.wav"), 3.0)
    print("  silence.wav  3.0s")
    _noisy_speech(os.path.join(FIX, "long_sentence.wav"),
                  os.path.join(FIX, "noisy_speech.wav"))
    print("  noisy_speech.wav  (long_sentence + white noise)")
    print(f"fixtures written to {FIX}")


if __name__ == "__main__":
    main()
