#!/usr/bin/env python3
"""Generate deterministic audio fixtures for the offline voice harness.

Uses macOS `say` + ffmpeg to synthesize 16 kHz mono 16-bit WAVs (the format the
live recorder produces), so the capture/turn-logic/AEC tests run with NO phone
call. Each speech fixture also writes a `.txt` with its expected transcript.

Run:  python3 tests/gen_fixtures.py
Output: tests/fixtures/*.wav (+ *.txt)
"""
import os
import struct
import subprocess
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
SR = 16000

# (name, voice, text) — distinctive words so asserts are unambiguous.
SPEECH = [
    ("long_sentence", "Samantha",
     "Let's go with the production database migration tonight, "
     "and roll back immediately if the error rate climbs above five percent."),
    ("short_reply", "Samantha", "Yes, go with production."),
    ("mid_sentence", "Daniel",
     "I think we should wait until the weekend before deploying this change."),
]


def _say_wav(text: str, voice: str, dst: str) -> None:
    aiff = dst + ".aiff"
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff,
                    "-ar", str(SR), "-ac", "1", "-acodec", "pcm_s16le", dst], check=True)
    os.remove(aiff)


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
    for name, voice, text in SPEECH:
        wav = os.path.join(FIX, name + ".wav")
        _say_wav(text, voice, wav)
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
