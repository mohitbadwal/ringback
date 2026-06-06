#!/usr/bin/env python3
"""Synthesize a speakerphone near-end: the user's voice + a delayed, attenuated copy of
OUR TTS echoing off their speaker (+ optional noise). This reproduces the real failure
class — structured speech-like echo masking the user — that clean `say` fixtures don't,
and is the substrate for proving AEC recovers the user's words.

Pure stdlib (int16 PCM math). Returns paths and also exposes raw PCM for AEC feeding.
"""
import os
import random
import struct
import wave

SR = 16000


def _read(path: str):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        return list(struct.unpack("<%dh" % w.getnframes(), w.readframes(w.getnframes())))


def _write(path: str, samples) -> str:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % len(samples),
                                  *[max(-32767, min(32767, int(s))) for s in samples]))
    return path


def mix_echo(user_wav: str, tts_wav: str, dst: str, delay_ms: float = 150.0,
             atten: float = 0.6, noise_amp: int = 200, user_start_ms: float = 0.0):
    """near = user(delayed by user_start_ms) + atten*tts(delayed by delay_ms) + noise.

    Returns (dst, far_pcm_bytes) where far_pcm_bytes is the clean TTS reference (what we
    would feed the AEC as the far end). The near-end length covers both signals.
    """
    user = _read(user_wav)
    tts = _read(tts_wav)
    d = int(SR * delay_ms / 1000)
    us = int(SR * user_start_ms / 1000)
    n = max(len(tts) + d, len(user) + us) + SR // 2
    rng = random.Random(3)
    near = [0] * n
    for i, s in enumerate(tts):                       # echo: attenuated + delayed TTS
        near[i + d] += int(atten * s)
    for i, s in enumerate(user):                      # the user's actual voice
        near[i + us] += s
    if noise_amp:
        for i in range(n):
            near[i] += rng.randint(-noise_amp, noise_amp)
    _write(dst, near)
    far = struct.pack("<%dh" % len(tts), *[max(-32767, min(32767, s)) for s in tts])
    return dst, far


def far_pcm(tts_wav: str) -> bytes:
    s = _read(tts_wav)
    return struct.pack("<%dh" % len(s), *[max(-32767, min(32767, x)) for x in s])
