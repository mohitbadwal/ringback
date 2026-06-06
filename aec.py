"""Real-time acoustic echo cancellation + noise suppression for the voice engine.

Wraps the WebRTC Audio Processing Module (AEC3 + noise suppression + high-pass),
via livekit.rtc.apm, to cancel our OWN text-to-speech echoing back off the caller's
speakerphone — we have the reference signal (the TTS we play), so we feed it as the
far-end and subtract its echo from the incoming audio, keeping the caller's voice and
filtering background noise. This is what lets barge-in work on speaker WITHOUT the
half-duplex workaround.

Operates on 16 kHz mono int16 PCM. APM requires exactly 10 ms (160-sample) frames, so
this buffers arbitrary input sizes (pjsua delivers ~20 ms) into 10 ms chunks.

The genuine open risk is DELAY: our echo returns over RTP (caller speaker -> mic ->
network), so the reference leads the echo by ~100-250 ms and it drifts. set_stream_delay
tells AEC3 the offset; tune VOICE_AEC_DELAY_MS on a live call.
"""
from __future__ import annotations
import os

SR = 16000
FRAME = 160            # 10 ms @ 16 kHz (APM hard requirement)
FBYTES = FRAME * 2     # int16 bytes per frame


def available() -> bool:
    try:
        import livekit.rtc.apm  # noqa: F401
        return True
    except Exception:
        return False


class AecProcessor:
    """Feed the far-end (our TTS) via feed_far(); clean the near-end (incoming) via
    process_near() -> returns echo/noise-suppressed PCM, in whole 10 ms frames."""

    def __init__(self, delay_ms: float = 150.0):
        from livekit.rtc.apm import AudioProcessingModule
        from livekit.rtc import AudioFrame
        self._AF = AudioFrame
        self.apm = AudioProcessingModule(
            echo_cancellation=True, noise_suppression=True, high_pass_filter=True)
        self.delay_ms = float(os.environ.get("VOICE_AEC_DELAY_MS", delay_ms))
        self._far_buf = bytearray()
        self._near_buf = bytearray()

    def set_delay(self, ms: float) -> None:
        self.delay_ms = float(ms)

    def feed_far(self, pcm: bytes) -> None:
        """Reference = the audio we are playing out (our TTS)."""
        self._far_buf += pcm
        while len(self._far_buf) >= FBYTES:
            chunk = bytes(self._far_buf[:FBYTES]); del self._far_buf[:FBYTES]
            self.apm.process_reverse_stream(self._AF(chunk, SR, 1, FRAME))

    def process_near(self, pcm: bytes) -> bytes:
        """Near end = incoming caller audio (+ our echo). Returns cleaned PCM (whole
        10 ms frames; trailing partial frame is buffered for next call)."""
        self._near_buf += pcm
        out = bytearray()
        while len(self._near_buf) >= FBYTES:
            chunk = bytes(self._near_buf[:FBYTES]); del self._near_buf[:FBYTES]
            self.apm.set_stream_delay_ms(int(self.delay_ms))
            f = self._AF(chunk, SR, 1, FRAME)
            self.apm.process_stream(f)
            out += bytes(f.data)
        return bytes(out)
