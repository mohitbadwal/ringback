"""Voice call engine for ringback-voice: pjsua2 + say (TTS) + whisper.cpp (STT).

Provides CallSession: place a SIP call to the Linphone account, then dynamically
speak() arbitrary text and listen() to the caller (transcribed). The MCP wraps
this so a Claude session can drive a phone conversation turn by turn.

Run env (set by the launcher):
  PYTHONPATH   -> pjsua2 build lib dir
  DYLD_LIBRARY_PATH -> pjproject dylibs + openssl
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import time
import wave

import pjsua2 as pj

# ---- config (all via env; see voice.env.example) ------------------------------
# Your SIP identity/credentials come from the environment (the launcher sources
# voice.env). No personal account is baked into this file.
SIP_ID = os.environ.get("VOICE_SIP_ID", "sip:user@sip.linphone.org")
SIP_CALLEE = os.environ.get("VOICE_SIP_CALLEE", os.environ.get("VOICE_SIP_ID",
                                                               "sip:user@sip.linphone.org"))
SIP_USER = os.environ.get("VOICE_SIP_USER", "user")
SIP_PASS = os.environ.get("VOICE_SIP_PASS", "")
SIP_PROXY = os.environ.get("VOICE_SIP_PROXY", "sip:sip.linphone.org;transport=tls")
WHISPER_BIN = os.environ.get("WHISPER_BIN", "whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL",
                               os.path.expanduser("~/.whisper-models/ggml-small.en.bin"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")


def _tts_to_wav(text: str) -> str:
    """macOS `say` -> 16 kHz mono 16-bit WAV (pjsua2 resamples as needed)."""
    aiff = tempfile.mktemp(suffix=".aiff")
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", aiff,
                    "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le", wav], check=True)
    try:
        os.remove(aiff)
    except OSError:
        pass
    return wav


def _wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _transcribe(wav: str) -> str:
    out = subprocess.run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav, "-nt", "-t", "8"],
                         capture_output=True, text=True)
    text = " ".join(out.stdout.split()).strip()
    # drop whisper non-speech annotations like [BLANK_AUDIO], [beep], (silence)
    if not text or (text.startswith(("[", "(")) and text.endswith(("]", ")"))):
        return ""
    return text


_BEEP_WAV = "/tmp/voice_beep.wav"


def _ensure_beep() -> str:
    """Generate a short 'your turn' beep once (16 kHz mono, fade in/out)."""
    if os.path.exists(_BEEP_WAV):
        return _BEEP_WAV
    rate, dur, freq = 16000, 0.18, 880.0
    n = int(rate * dur)
    fade = int(rate * 0.02)
    with wave.open(_BEEP_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        buf = bytearray()
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            val = int(0.5 * env * 32767 * math.sin(2 * math.pi * freq * i / rate))
            buf += struct.pack("<h", val)
        w.writeframes(bytes(buf))
    return _BEEP_WAV


def _tail_rms(path: str, seconds: float = 0.25) -> float:
    """RMS of the last `seconds` of a (possibly still-growing) 16-bit mono WAV."""
    try:
        size = os.path.getsize(path)
        if size <= 44:
            return 0.0
        nbytes = int(16000 * 2 * seconds)
        with open(path, "rb") as f:
            f.seek(max(44, size - nbytes))
            raw = f.read()
        if len(raw) < 2:
            return 0.0
        n = len(raw) // 2
        vals = struct.unpack("<%dh" % n, raw[: n * 2])
        return math.sqrt(sum(v * v for v in vals) / n)
    except OSError:
        return 0.0


class _Call(pj.Call):
    def __init__(self, acc, session):
        super().__init__(acc)
        self.session = session

    def onCallState(self, prm):
        ci = self.getInfo()
        self.session.last_state = ci.state
        if ci.state == pj.PJSIP_INV_STATE_CONFIRMED:
            self.session.connected = True
        if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.session.connected = False
            self.session.disconnected = True

    def onCallMediaState(self, prm):
        ci = self.getInfo()
        for i, mi in enumerate(ci.media):
            if (mi.type == pj.PJMEDIA_TYPE_AUDIO
                    and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE):
                self.session.aud = self.getAudioMedia(i)


class CallSession:
    def __init__(self):
        self.ep = None
        self.acc = None
        self.call = None
        self.aud = None
        self.connected = False
        self.disconnected = False
        self.last_state = None
        self.log = []          # unified conversation timeline (claude + user turns)

    def _pump(self, seconds: float):
        # pjsua2 runs its own worker thread (threadCnt=1) that processes RTP and
        # events continuously, so we just wait — no manual event pumping needed.
        time.sleep(seconds)

    def _reg(self):
        # any thread calling into pjsua2 must be registered (MCP tool calls may
        # land on different threadpool threads).
        try:
            self.ep.libRegisterThread("mcp")
        except Exception:
            pass

    def start_lib(self):
        self.ep = pj.Endpoint()
        self.ep.libCreate()
        cfg = pj.EpConfig()
        cfg.uaConfig.threadCnt = 1          # worker thread keeps the call alive
        cfg.logConfig.level = 2
        cfg.logConfig.consoleLevel = 2
        self.ep.libInit(cfg)

        self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, pj.TransportConfig())
        tls = pj.TransportConfig()
        tls.tlsConfig.verifyServer = False
        tls.tlsConfig.method = pj.PJSIP_TLSV1_2_METHOD
        self.ep.transportCreate(pj.PJSIP_TRANSPORT_TLS, tls)
        self.ep.libStart()

        acfg = pj.AccountConfig()
        acfg.idUri = SIP_ID
        acfg.regConfig.registrarUri = ""    # do NOT register (avoids self-call fork)
        acfg.sipConfig.authCreds.append(
            pj.AuthCredInfo("digest", "*", SIP_USER, 0, SIP_PASS))
        acfg.sipConfig.proxies.append(SIP_PROXY)
        acfg.mediaConfig.srtpUse = pj.PJMEDIA_SRTP_MANDATORY
        acfg.mediaConfig.srtpSecureSignaling = 0
        self.acc = pj.Account()
        self.acc.create(acfg)

    def place_call(self, answer_timeout: float = 25.0) -> bool:
        import gc
        self._reg()
        self.connected = False
        self.disconnected = False
        self.aud = None
        if self.call is not None:        # clear any previous call object
            self.call = None
            gc.collect()
        self.call = _Call(self.acc, self)
        self.call.makeCall(SIP_CALLEE, pj.CallOpParam(True))
        end = time.time() + answer_timeout
        while time.time() < end:
            time.sleep(0.1)
            if self.connected and self.aud is not None:
                time.sleep(0.4)   # let media settle
                return True
            if self.disconnected:
                return False
        return False

    def speak(self, text: str):
        self._reg()
        if not (self.connected and self.aud):
            return "not connected"
        wav = _tts_to_wav(text)
        dur = _wav_duration(wav)
        player = pj.AudioMediaPlayer()
        player.createPlayer(wav, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(self.aud)
        self._pump(dur + 0.3)     # play to completion
        try:
            player.stopTransmit(self.aud)
        except Exception:
            pass
        del player
        try:
            os.remove(wav)
        except OSError:
            pass
        return "spoke"

    def listen(self, max_sec: float = 15.0, silence_sec: float = 1.4,
               rms_thresh: float = 300.0) -> str:
        self._reg()
        if not (self.connected and self.aud):
            return ""
        # play a short beep so the user knows it's their turn to talk
        try:
            beep = pj.AudioMediaPlayer()
            beep.createPlayer(_ensure_beep(), pj.PJMEDIA_FILE_NO_LOOP)
            beep.startTransmit(self.aud)
            time.sleep(0.22)
            beep.stopTransmit(self.aud)
            del beep
        except Exception:
            pass
        time.sleep(0.45)          # let the beep + its echo clear before recording
        rec_wav = tempfile.mktemp(suffix=".wav")
        rec = pj.AudioMediaRecorder()
        rec.createRecorder(rec_wav)
        self.aud.startTransmit(rec)
        start = time.time()
        heard = False
        silence_since = None
        over = 0                  # consecutive over-threshold polls (debounce)
        grace = 0.4               # ignore residual beep echo at the very start
        while time.time() - start < max_sec:
            time.sleep(0.1)
            if time.time() - start < grace:
                continue          # don't judge speech during the grace window
            lvl = _tail_rms(rec_wav)
            if lvl > rms_thresh:
                over += 1
                if over >= 2:     # need ~0.2s of real sound, not a blip/echo
                    heard = True
                silence_since = None
            else:
                over = 0
                if heard:         # only end the turn AFTER real speech began
                    if silence_since is None:
                        silence_since = time.time()
                    elif time.time() - silence_since > silence_sec:
                        break
            if self.disconnected:
                break
        try:
            self.aud.stopTransmit(rec)
        except Exception:
            pass
        del rec
        text = _transcribe(rec_wav) if heard else ""
        try:
            os.remove(rec_wav)
        except OSError:
            pass
        return text

    def speak_interruptible(self, text: str, listen_after: bool = True,
                            silence_sec: float = 1.0, max_wait: float = 15.0,
                            barge_rms: float = 500.0) -> dict:
        """Speak `text` while monitoring for the user talking over us (barge-in).

        If the user starts talking, we stop speaking immediately, note how far we
        got, and capture what they said. If we finish uninterrupted, we then
        listen for their reply (when listen_after). Returns a dict describing the
        turn and appends to self.log (the unified transcript).
        """
        self._reg()
        if not (self.connected and self.aud):
            return {"ok": False, "ended": self.disconnected, "user": "",
                    "interrupted": False, "spoken": "", "unsaid": ""}

        wav = _tts_to_wav(text)
        dur = _wav_duration(wav)

        # --- phase 1: speak while a throwaway recorder senses barge-in ---
        det_wav = tempfile.mktemp(suffix=".wav")
        det = pj.AudioMediaRecorder()
        det.createRecorder(det_wav)
        self.aud.startTransmit(det)
        player = pj.AudioMediaPlayer()
        player.createPlayer(wav, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(self.aud)
        start = time.time()
        over = 0
        interrupted_at = None
        while time.time() - start < dur + 0.2:
            time.sleep(0.08)
            if self.disconnected:
                break
            if _tail_rms(det_wav) > barge_rms:      # user talking over us
                over += 1
                if over >= 2:
                    interrupted_at = time.time() - start
                    break
            else:
                over = 0
        try:
            player.stopTransmit(self.aud)
        except Exception:
            pass
        del player
        try:
            self.aud.stopTransmit(det)
        except Exception:
            pass
        del det
        _rm(det_wav)

        # user hung up mid-speech -> stop instantly, no transcription
        if self.disconnected:
            _rm(wav)
            return {"ok": True, "ended": True, "interrupted": interrupted_at is not None,
                    "spoken": text, "unsaid": "", "user": ""}

        interrupted = interrupted_at is not None
        words = text.split()
        if interrupted:
            frac = min(1.0, interrupted_at / max(dur, 0.1))
            k = max(1, int(round(len(words) * frac)))
            spoken, unsaid = " ".join(words[:k]), " ".join(words[k:])
        else:
            spoken, unsaid = text, ""

        # --- phase 2: FRESH recorder captures only the user's words (clean clip) ---
        user_text = ""
        if interrupted or listen_after:
            cap_wav = tempfile.mktemp(suffix=".wav")
            cap = pj.AudioMediaRecorder()
            cap.createRecorder(cap_wav)
            self.aud.startTransmit(cap)
            heard = interrupted                     # if they cut in, already talking
            silence_since = None
            o2 = 0
            t0 = time.time()
            while time.time() - t0 < max_wait:
                time.sleep(0.08)
                if self.disconnected:
                    break
                if _tail_rms(cap_wav) > barge_rms:
                    o2 += 1
                    if o2 >= 2:
                        heard = True
                    silence_since = None
                else:
                    o2 = 0
                    if heard:
                        if silence_since is None:
                            silence_since = time.time()
                        elif time.time() - silence_since > silence_sec:
                            break
            try:
                self.aud.stopTransmit(cap)
            except Exception:
                pass
            del cap
            # skip transcription entirely if they hung up (instant return)
            if not self.disconnected and heard:
                user_text = _transcribe(cap_wav)
            _rm(cap_wav)

        _rm(wav)
        self.log.append({"who": "claude", "text": spoken,
                         "interrupted": interrupted, "unsaid": unsaid})
        if user_text:
            self.log.append({"who": "user", "text": user_text})
        return {"ok": True, "interrupted": interrupted, "spoken": spoken,
                "unsaid": unsaid, "user": user_text, "ended": self.disconnected}

    def hangup(self):
        self._reg()
        try:
            if self.call:
                self.call.hangup(pj.CallOpParam(True))
                time.sleep(0.5)
        except Exception:
            pass
        self.connected = False

    def shutdown(self):
        import gc
        try:
            self.hangup()
        except Exception:
            pass
        # destroy Call/Account C++ objects BEFORE libDestroy (else pjsua asserts)
        self.call = None
        self.aud = None
        self.acc = None
        gc.collect()
        try:
            self.ep.libDestroy()
        except Exception:
            pass
        self.ep = None


if __name__ == "__main__":
    # Standalone one-turn test: call, greet, listen, echo back, hang up.
    s = CallSession()
    s.start_lib()
    print("placing call...")
    if not s.place_call():
        print("not answered"); s.shutdown(); raise SystemExit
    print("connected. speaking greeting.")
    s.speak("Hi, this is your assistant. Say something after the beep, then pause.")
    print("listening...")
    said = s.listen()
    print("HEARD:", repr(said))
    s.speak("You said: " + (said or "nothing"))
    s.hangup()
    s.shutdown()
    print("done")
