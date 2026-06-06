#!/usr/bin/env python3
"""GrowingWav — simulate a live pjsua AudioMediaRecorder for offline tests.

pjsua's recorder writes a WAV header with a STALE (zero) data-size, then appends PCM
over wall-clock time, only fixing the header on close. That is the exact condition
voice_agent._wav_snapshot() exists to handle. This reproduces it from a fixture WAV so
the capture loop can be tested with no phone call.

`rate` scales write speed vs. real time: 1.0 = real time, <1.0 = a recorder that lags
behind wall-clock (writes slower than audio plays) — the suspected capture-bug trigger.
"""
import os
import struct
import threading
import time
import wave


class GrowingWav:
    def __init__(self, src_wav: str, dst: str, rate: float = 1.0,
                 start_delay: float = 0.0, chunk_sec: float = 0.1,
                 trailing_silence: float = 0.0):
        with wave.open(src_wav, "rb") as w:
            self.sr = w.getframerate()
            self.ch = w.getnchannels()
            self.sw = w.getsampwidth()
            self.pcm = w.readframes(w.getnframes())
        self.dst = dst
        self.rate = rate
        self.start_delay = start_delay
        self.chunk_sec = chunk_sec
        self.trailing_silence = trailing_silence   # seconds of silence after the clip (the
        self._stop = False                         # live recorder keeps capturing after they
        self._thread = None                        # stop — needed to test end-of-turn detect)
        self.done = False

    def _write_stale_header(self) -> None:
        """44-byte canonical PCM header with data-size = 0 (an open recorder's header)."""
        byte_rate = self.sr * self.ch * self.sw
        with open(self.dst, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", 0) + b"WAVE")
            f.write(b"fmt " + struct.pack("<I", 16) + struct.pack("<H", 1))
            f.write(struct.pack("<H", self.ch) + struct.pack("<I", self.sr))
            f.write(struct.pack("<I", byte_rate) + struct.pack("<H", self.ch * self.sw))
            f.write(struct.pack("<H", self.sw * 8))
            f.write(b"data" + struct.pack("<I", 0))   # STALE size — never updated here

    def _run(self) -> None:
        if self.start_delay:
            time.sleep(self.start_delay)
        bps = self.sr * self.ch * self.sw
        chunk = max(2, int(bps * self.chunk_sec))
        i = 0
        with open(self.dst, "ab") as f:
            while i < len(self.pcm) and not self._stop:
                f.write(self.pcm[i:i + chunk])
                f.flush()
                os.fsync(f.fileno())
                i += chunk
                time.sleep(self.chunk_sec / self.rate)
            # trailing silence — the live recorder keeps writing after the user goes quiet
            sil = b"\x00" * chunk
            n_sil = int(self.trailing_silence / self.chunk_sec)
            for _ in range(n_sil):
                if self._stop:
                    break
                f.write(sil)
                f.flush()
                os.fsync(f.fileno())
                time.sleep(self.chunk_sec / self.rate)
        self.done = True

    def start(self) -> "GrowingWav":
        self._write_stale_header()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop = True

    @property
    def audio_sec(self) -> float:
        return len(self.pcm) / (self.sr * self.ch * self.sw)
