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
import urllib.error
import urllib.request
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
# Caller-ID display name shown on the phone (the SIP From header display name).
SIP_DISPLAY_NAME = os.environ.get("VOICE_DISPLAY_NAME", "").strip()
WHISPER_BIN = os.environ.get("WHISPER_BIN", "whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL",
                               os.path.expanduser("~/.whisper-models/ggml-small.en.bin"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

# Voice-activity thresholds. Background noise was falsely triggering barge-in, so
# these are now (a) env-tunable and (b) raised at call time by a measured noise floor
# (CallSession.place_call -> _measure_rms). Real speech must clear the effective
# threshold = clamp(max(base, noise_floor * NOISE_FACTOR), .., RMS_CAP).
BARGE_RMS_BASE = float(os.environ.get("VOICE_BARGE_RMS", "550"))
LISTEN_RMS_BASE = float(os.environ.get("VOICE_LISTEN_RMS", "320"))
NOISE_FACTOR = float(os.environ.get("VOICE_NOISE_FACTOR", "2.5"))
RMS_CAP = float(os.environ.get("VOICE_RMS_CAP", "3000"))  # never raise threshold above this
# Barge-in threshold floor. The barge detector must key off the near-end level DURING our
# TX (when the user is silent), NOT the pre-call ambient noise floor: a real harvested call
# measured ambient ~2441 but the during-TX floor ~1-800 and the user's barge ~2000-3972 —
# so a noise-floor-derived threshold (capped at 3000) MISSED the user. We measure the live
# during-TX floor each utterance and clamp it to at least this, well under a real barge.
BARGE_RMS_MIN = float(os.environ.get("VOICE_BARGE_RMS_MIN", "1300"))
# Listen (capturing the user) uses a GENTLER factor than barge: barge must avoid
# false-triggering on noise/echo (high bar), but listen must still hear the user in a
# noisy room (lower bar) — whisper discards any non-speech that slips through.
LISTEN_NOISE_FACTOR = float(os.environ.get("VOICE_LISTEN_NOISE_FACTOR", "1.5"))
# Debounce: consecutive over-threshold polls required to count as real speech (not a
# transient). Higher = more noise-robust, slightly less instant barge-in.
BARGE_DEBOUNCE = int(os.environ.get("VOICE_BARGE_DEBOUNCE", "5"))    # ~0.40s at 0.08s/poll
LISTEN_DEBOUNCE = int(os.environ.get("VOICE_LISTEN_DEBOUNCE", "3"))  # ~0.30s at 0.10s/poll

# Barge-in is ON by default. A harvested speakerphone call (user 100% on speaker) showed
# essentially NO echo in the near-end — modern phones run their own acoustic echo
# cancellation, so our TTS does not loop back. Barge-in is therefore safe without AEC.
# The early-barge guard below stays as a FALLBACK: if a device ever does echo our voice
# back, a sustained "barge" in the first EARLY_BARGE_SEC flips that call to half-duplex.
# Set VOICE_HALF_DUPLEX=1 to force half-duplex (a known echoey device / no phone AEC).
HALF_DUPLEX = os.environ.get("VOICE_HALF_DUPLEX", "0").strip().lower() in ("1", "true", "yes")
# A sustained "barge" within the first EARLY_BARGE_SEC of our speech is treated as echo
# (a real person rarely cuts in this early) and flips the call to half-duplex as a safety.
# Shorter now (0.8s): with phone-side AEC there's no echo to catch, and a real early barge
# should register — so only the very start is guarded.
EARLY_BARGE_SEC = float(os.environ.get("VOICE_EARLY_BARGE_SEC", "0.8"))
POST_SPEAK_DRAIN = float(os.environ.get("VOICE_POST_SPEAK_DRAIN", "0.35"))  # let echo tail clear
# Turn-taking — TWO phases, because one tight timeout dropped real speech:
#   START_TIMEOUT: how long to wait for the user's FIRST word before giving up. Must be
#     generous — a 2.0s window guillotined anyone who paused to think before answering
#     (reproduced offline in tests/test_capture.py: user starts at 2.5s -> captured "").
#   END_SILENCE: once they HAVE spoken, end the turn this long after their last word —
#     this is the responsive "stop after ~1.5s of no new words" endpoint.
START_TIMEOUT = float(os.environ.get("VOICE_START_TIMEOUT", "4.0"))
END_SILENCE = float(os.environ.get("VOICE_END_SILENCE", "1.5"))
# legacy alias (older callers): treat NO_SPEECH_SEC as the start timeout
NO_SPEECH_SEC = float(os.environ.get("VOICE_NO_SPEECH_SEC", str(START_TIMEOUT)))

# Persistent whisper.cpp HTTP server: loads the model ONCE so each transcription is fast
# inference (~0.1s) instead of a fresh whisper-cli model reload (~0.5-1.5s) — this is what
# makes streaming capture (transcribe a window ~3x/sec) fast enough. Lazy: started on the
# first transcription of a call; reaped after WHISPER_SERVER_IDLE_SEC idle to free memory.
# Falls back to whisper-cli if it can't start.
WHISPER_SERVER_BIN = os.environ.get("WHISPER_SERVER_BIN", "whisper-server")
WHISPER_SERVER_MODEL = os.environ.get("WHISPER_SERVER_MODEL",
                                      os.path.expanduser("~/.whisper-models/ggml-base.en.bin"))
WHISPER_SERVER_HOST = os.environ.get("WHISPER_SERVER_HOST", "127.0.0.1")
WHISPER_SERVER_PORT = int(os.environ.get("WHISPER_SERVER_PORT", "8642"))
WHISPER_SERVER_IDLE_SEC = float(os.environ.get("WHISPER_SERVER_IDLE_SEC", "300"))  # GC after 5 min idle


def _eff_threshold(base: float, noise_floor: float, factor: float = NOISE_FACTOR) -> float:
    """Speech threshold = base, raised to clear measured ambient noise, then capped.

    `factor` is how far above the noise floor speech must be — high for barge-in
    (don't false-trigger), gentler for listen (still hear the user in a noisy room).
    """
    return min(max(base, noise_floor * factor), RMS_CAP)


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


def _wav_snapshot(src: str) -> str:
    """Rewrite a still-being-recorded WAV with a CORRECT length header so a transcriber
    sees ALL audio captured so far. An in-progress pjsua recorder leaves the data-size
    field stale (set at close), so reading it directly yields only a fragment. Returns a
    temp path (caller removes it) or "" if there's no audio yet."""
    try:
        with open(src, "rb") as f:
            head = f.read(44)
            if len(head) < 44 or head[:4] != b"RIFF":
                return ""
            ch = struct.unpack_from("<H", head, 22)[0] or 1
            sr = struct.unpack_from("<I", head, 24)[0]
            bits = struct.unpack_from("<H", head, 34)[0] or 16
            pcm = f.read()                       # everything written so far, past the header
    except OSError:
        return ""
    if not pcm or sr == 0:
        return ""
    dst = tempfile.mktemp(suffix=".wav")
    with wave.open(dst, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(bits // 8)
        w.setframerate(sr)
        w.writeframes(pcm)
    return dst


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# Whisper emits these bare phrases as HALLUCINATIONS on silence / non-speech audio. If they
# slip through they look like the user spoke — flipping the turn into "they answered" and
# then ending it 1.5s later, swallowing the real reply that follows. Matched only as the
# WHOLE transcript (so "okay thanks for the help" is kept; a lone "Thanks for watching." is
# dropped). Deliberately excludes real short answers like yes/no/okay/sure.
_HALLUCINATIONS = {
    "you", "thank you", "thanks", "thanks for watching", "thank you for watching",
    "thank you.", "thanks for watching.", "bye", "bye.", "you're welcome",
    "please subscribe", "subscribe", ".", "so", "uh", "um",
}


def _clean_text(text: str) -> str:
    """Normalize whitespace and drop whisper non-speech artifacts (bracketed annotations
    like [BLANK_AUDIO]/(silence), and bare silence-hallucinations). Returns "" if nothing
    real was said."""
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if text.startswith(("[", "(")) and text.endswith(("]", ")")):
        return ""
    if text.strip(" .,!?-").lower() in _HALLUCINATIONS:
        return ""
    return text


def _transcribe(wav: str) -> str:
    out = subprocess.run([WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav, "-nt", "-t", "8"],
                         capture_output=True, text=True)
    return _clean_text(out.stdout)


# --- persistent whisper-server: load model once (lazy), reap after idle ---------------
import threading

_WSRV_URL = f"http://{WHISPER_SERVER_HOST}:{WHISPER_SERVER_PORT}"
_wsrv_proc = None
_wsrv_last_use = 0.0
_wsrv_lock = threading.Lock()
_wsrv_reaper = None


def _wsrv_health() -> bool:
    try:
        urllib.request.urlopen(_WSRV_URL + "/", timeout=1)
        return True
    except urllib.error.HTTPError:
        return True       # server answered (e.g. 404) -> it's up
    except Exception:
        return False      # connection refused / timeout -> down


def _wsrv_reaper_loop():
    global _wsrv_proc
    while True:
        time.sleep(20)
        with _wsrv_lock:
            if _wsrv_proc is None:
                return
            if time.time() - _wsrv_last_use > WHISPER_SERVER_IDLE_SEC:
                try:
                    _wsrv_proc.terminate()
                except Exception:
                    pass
                _wsrv_proc = None
                return    # idle too long -> freed; a future call re-spawns + re-arms


def _wsrv_spawn():
    """Start the server if it isn't up (non-blocking) and arm the idle reaper."""
    global _wsrv_proc, _wsrv_reaper, _wsrv_last_use
    with _wsrv_lock:
        if _wsrv_health() or (_wsrv_proc is not None and _wsrv_proc.poll() is None):
            return
        try:
            _wsrv_proc = subprocess.Popen(
                [WHISPER_SERVER_BIN, "-m", WHISPER_SERVER_MODEL,
                 "--host", WHISPER_SERVER_HOST, "--port", str(WHISPER_SERVER_PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            _wsrv_last_use = time.time()
        except FileNotFoundError:
            _wsrv_proc = None
            return
        if _wsrv_reaper is None or not _wsrv_reaper.is_alive():
            _wsrv_reaper = threading.Thread(target=_wsrv_reaper_loop, daemon=True)
            _wsrv_reaper.start()


def _wsrv_warm():
    """Kick off the model load now (e.g. when a call connects) without blocking."""
    if not _wsrv_health():
        _wsrv_spawn()


def _wsrv_ready(wait_sec: float = 12.0) -> bool:
    if _wsrv_health():
        return True
    _wsrv_spawn()
    end = time.time() + wait_sec
    while time.time() < end:
        if _wsrv_health():
            return True
        time.sleep(0.2)
    return False


def _transcribe_stream(wav: str) -> str:
    """Fast transcription via the persistent whisper-server; whisper-cli fallback."""
    global _wsrv_last_use
    if not _wsrv_ready():
        return _transcribe(wav)
    try:
        with open(wav, "rb") as f:
            audio = f.read()
        b = "----rbkboundary"
        body = (
            (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             f'Content-Type: audio/wav\r\n\r\n').encode() + audio +
            (f'\r\n--{b}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\n'
             f'text\r\n--{b}--\r\n').encode())
        req = urllib.request.Request(
            _WSRV_URL + "/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=6) as r:
            text = r.read().decode("utf-8", "ignore")
        _wsrv_last_use = time.time()
        return _clean_text(text)
    except Exception:
        return _transcribe(wav)


def _capture_turn(snapshot_fn, is_disconnected, max_sec: float = 15.0,
                  start_timeout: float = START_TIMEOUT, end_silence: float = END_SILENCE) -> str:
    """The streaming capture turn-loop, with NO pjsua2/SIP dependency so it can be
    unit-tested with a file-fed audio source (see tests/test_capture.py).

      snapshot_fn() -> path to a valid-header WAV of audio captured so far, or "".
      is_disconnected() -> True if the call dropped (bail at once; returns None).

    Transcribes the growing clip ~3x/sec via the persistent whisper-server, in TWO phases:
      - BEFORE the first real word: wait up to `start_timeout` (generous — a thinking
        pause must not end the turn). If still nothing -> "".
      - AFTER the first word: end `end_silence` after the last new word (the responsive
        endpoint). Returns the streamed transcript; the final re-transcribe is the caller's.
    is_disconnected() -> returns None so the caller can surface [CALL ENDED].
    """
    start = time.time()
    text = ""
    last_word = None          # elapsed time when the transcript last gained new words
    last_check = 0.0
    while time.time() - start < max_sec:
        time.sleep(0.1)
        if is_disconnected():
            return None
        el = time.time() - start
        if el >= 0.5 and (el - last_check) >= 0.3:
            last_check = el
            snap = snapshot_fn()
            t = _transcribe_stream(snap) if snap else ""   # already hallucination-filtered
            if snap:
                _rm(snap)
            if t and t != text:
                last_word = el
                text = t
            if not text:
                if el >= start_timeout:
                    break                     # user never started speaking -> ""
            elif last_word is not None and (el - last_word) >= end_silence:
                break                         # they spoke, then went quiet -> end turn
    return text


class _BargeState:
    """Decide if/when the user barged in, from a stream of near-end RMS samples while we
    speak. Pure + tiny so the REAL harvested audio (tests/test_barge.py) validates the same
    decision the live call makes. feed(t, rms) -> 'barge' (cut in -> stop talking), 'echo'
    (suspiciously early sustained energy -> treat as device echo, go half-duplex), or None.
    """

    def __init__(self, thresh: float, debounce: int = BARGE_DEBOUNCE,
                 early_sec: float = EARLY_BARGE_SEC):
        self.thresh = thresh
        self.debounce = debounce
        self.early_sec = early_sec
        self.over = 0

    def feed(self, t: float, rms: float):
        if rms > self.thresh:
            self.over += 1
            if self.over >= self.debounce:        # sustained, not a transient
                self.over = 0
                return "echo" if t < self.early_sec else "barge"
        else:
            self.over = 0
        return None


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
        self.noise_floor = 0.0  # ambient RMS measured at call connect (see place_call)
        self.half_duplex = HALF_DUPLEX  # flips on if speaker echo is detected mid-call
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

    def _measure_rms(self, duration: float = 0.6) -> float:
        """Sample ambient audio (right after answer, before anyone speaks) to learn
        the background noise floor, so thresholds can be raised to clear it."""
        self._reg()
        if not (self.connected and self.aud):
            return 0.0
        wav = tempfile.mktemp(suffix=".wav")
        try:
            rec = pj.AudioMediaRecorder()
            rec.createRecorder(wav)
            self.aud.startTransmit(rec)
            time.sleep(duration)
            try:
                self.aud.stopTransmit(rec)
            except Exception:
                pass
            del rec
            return _tail_rms(wav, seconds=duration)
        except Exception:
            return 0.0
        finally:
            _rm(wav)

    def start_lib(self):
        self.ep = pj.Endpoint()
        self.ep.libCreate()
        cfg = pj.EpConfig()
        cfg.uaConfig.threadCnt = 1          # worker thread keeps the call alive
        # quiet by default; raise VOICE_CONSOLE_LEVEL (e.g. 5) to see SIP signaling, or set
        # VOICE_LOG_FILE to capture full pjsua logs to a file for debugging
        cfg.logConfig.level = int(os.environ.get("VOICE_LOG_LEVEL", "1"))
        cfg.logConfig.consoleLevel = int(os.environ.get("VOICE_CONSOLE_LEVEL", "0"))
        _logfile = os.environ.get("VOICE_LOG_FILE", "")
        if _logfile:
            cfg.logConfig.filename = _logfile
        self.ep.libInit(cfg)

        self.ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, pj.TransportConfig())
        tls = pj.TransportConfig()
        tls.tlsConfig.verifyServer = False
        tls.tlsConfig.method = pj.PJSIP_TLSV1_2_METHOD
        self.ep.transportCreate(pj.PJSIP_TRANSPORT_TLS, tls)
        self.ep.libStart()

        acfg = pj.AccountConfig()
        # add the caller-ID display name to the From header if configured
        acfg.idUri = f'"{SIP_DISPLAY_NAME}" <{SIP_ID}>' if SIP_DISPLAY_NAME else SIP_ID
        acfg.regConfig.registrarUri = ""    # do NOT register (avoids self-call fork)
        acfg.sipConfig.authCreds.append(
            pj.AuthCredInfo("digest", "*", SIP_USER, 0, SIP_PASS))
        acfg.sipConfig.proxies.append(SIP_PROXY)
        acfg.mediaConfig.srtpUse = pj.PJMEDIA_SRTP_MANDATORY
        acfg.mediaConfig.srtpSecureSignaling = 0
        # RTP media port base. Default (0) keeps pjsua's 4000; override (VOICE_RTP_PORT) when
        # a second instance must coexist with the running MCP server (which holds 4000/4002).
        rtp_port = int(os.environ.get("VOICE_RTP_PORT", "0"))
        if rtp_port:
            acfg.mediaConfig.transportConfig.port = rtp_port
        self.acc = pj.Account()
        self.acc.create(acfg)

    def place_call(self, answer_timeout: float = 25.0) -> bool:
        import gc
        self._reg()
        self.connected = False
        self.disconnected = False
        self.half_duplex = HALF_DUPLEX   # re-evaluate echo per call (unless forced on)
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
                self.noise_floor = self._measure_rms(0.6)   # calibrate to ambient noise
                _wsrv_warm()   # lazily start the whisper-server now so it's ready to capture
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
        end = time.time() + dur + 0.3
        while time.time() < end:        # poll so a hang-up stops us AT ONCE, not after the whole line
            time.sleep(0.05)
            if self.disconnected:
                break
        try:
            player.stopTransmit(self.aud)
        except Exception:
            pass
        del player
        _rm(wav)
        return "ended" if self.disconnected else "spoke"

    def listen(self, max_sec: float = 15.0, end_silence: float = END_SILENCE,
               start_timeout: float = START_TIMEOUT) -> str:
        """Capture one user turn, STREAMED through the persistent whisper-server. Records
        and transcribes the growing clip ~3x/sec; the turn-end logic lives in the pure,
        file-testable _capture_turn (two-phase: wait `start_timeout` for the first word,
        then end `end_silence` after the last). No "your turn" beep — they just hear us
        finish and reply, like a real call. Returns the user's words, or "" on silence /
        hang-up (caller surfaces [SILENCE] / [CALL ENDED])."""
        self._reg()
        if not (self.connected and self.aud):
            return ""
        rec_wav = tempfile.mktemp(suffix=".wav")
        rec = pj.AudioMediaRecorder()
        rec.createRecorder(rec_wav)
        self.aud.startTransmit(rec)
        # stream-capture via the pure, file-fed turn-loop (same code the tests exercise)
        text = _capture_turn(lambda: _wav_snapshot(rec_wav), lambda: self.disconnected,
                             max_sec=max_sec, start_timeout=start_timeout, end_silence=end_silence)
        try:
            self.aud.stopTransmit(rec)
        except Exception:
            pass
        del rec
        if text is None or self.disconnected:    # hang-up mid-turn
            _rm(rec_wav)
            return ""
        snap = _wav_snapshot(rec_wav)            # full audio with a correct header
        final = _transcribe_stream(snap) if snap else ""
        if snap:
            _rm(snap)
        _rm(rec_wav)
        return final or text

    def speak_interruptible(self, text: str, listen_after: bool = True,
                            silence_sec: float = 1.0, max_wait: float = 15.0,
                            barge_rms: float = BARGE_RMS_BASE) -> dict:
        """Speak `text` while monitoring for the user talking over us (barge-in).

        If the user starts talking, we stop speaking immediately, note how far we
        got, and capture what they said. If we finish uninterrupted, we then
        listen for their reply (when listen_after). Returns a dict describing the
        turn and appends to self.log (the unified transcript).

        If the call is in half-duplex (speaker echo detected, or VOICE_HALF_DUPLEX),
        barge-in is skipped: we speak fully, drain the echo tail, then listen.
        """
        self._reg()
        _t0 = time.time()
        _tlog = ((lambda m: print("[timing] +%5.2fs %s" % (time.time() - _t0, m), flush=True))
                 if os.environ.get("VOICE_TIMING") else (lambda m: None))
        if not (self.connected and self.aud):
            return {"ok": False, "ended": self.disconnected, "user": "",
                    "interrupted": False, "spoken": "", "unsaid": ""}

        if self.half_duplex:
            # echo-safe: don't listen for barge while speaking (our own voice coming
            # back off the user's speaker would trigger it). Speak fully, then listen.
            self.speak(text)
            if self.disconnected:
                self.log.append({"who": "claude", "text": text, "interrupted": False, "unsaid": ""})
                return {"ok": True, "ended": True, "interrupted": False,
                        "spoken": text, "unsaid": "", "user": ""}
            user_text = ""
            if listen_after:
                time.sleep(POST_SPEAK_DRAIN)   # let the echo of our last words die down
                user_text = self.listen(max_sec=max_wait)
            self.log.append({"who": "claude", "text": text, "interrupted": False, "unsaid": ""})
            if user_text:
                self.log.append({"who": "user", "text": user_text})
            return {"ok": True, "interrupted": False, "spoken": text, "unsaid": "",
                    "user": user_text, "ended": self.disconnected}

        wav = _tts_to_wav(text)
        dur = _wav_duration(wav)
        _tlog("TTS generated (%.1fs of audio to speak)" % dur)

        # --- phase 1: speak while a throwaway recorder senses barge-in ---
        det_wav = tempfile.mktemp(suffix=".wav")
        det = pj.AudioMediaRecorder()
        det.createRecorder(det_wav)
        self.aud.startTransmit(det)
        player = pj.AudioMediaPlayer()
        player.createPlayer(wav, pj.PJMEDIA_FILE_NO_LOOP)
        player.startTransmit(self.aud)
        start = time.time()
        interrupted_at = None
        echo_mode = False
        # Barge threshold from the DURING-TX floor, not the pre-call noise floor: let our
        # TTS establish briefly, measure the near-end level while the user is still
        # listening, and key off that. Phone-side AEC keeps it low (~hundreds) so a real
        # barge (thousands) clears it; an echoey device pushes it up and trips the echo guard.
        time.sleep(0.4)
        tx_floor = 0.0 if self.disconnected else _tail_rms(det_wav, 0.3)
        barge_thresh = min(max(BARGE_RMS_MIN, tx_floor * 2.0), RMS_CAP)
        barge = _BargeState(barge_thresh)
        while time.time() - start < dur + 0.2:
            time.sleep(0.08)
            if self.disconnected:
                break
            if echo_mode:
                continue                            # echo detected: just finish speaking
            verdict = barge.feed(time.time() - start, _tail_rms(det_wav))
            if verdict == "echo":
                # too early to be a real interruption — almost certainly our own voice
                # echoing off a speaker (no phone AEC). Don't cut off; finish this line and
                # make the rest of the call half-duplex.
                echo_mode = True
                self.half_duplex = True
            elif verdict == "barge":
                interrupted_at = time.time() - start
                break
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
        _tlog("speech phase done (interrupted=%s)" % interrupted)
        words = text.split()
        if interrupted:
            frac = min(1.0, interrupted_at / max(dur, 0.1))
            k = max(1, int(round(len(words) * frac)))
            spoken, unsaid = " ".join(words[:k]), " ".join(words[k:])
        else:
            spoken, unsaid = text, ""

        # --- phase 2: capture the user's reply ---
        user_text = ""
        if interrupted:
            # they cut in (already talking) — capture the rest the SAME fast streaming way
            # as a normal reply (whisper-server, ends ~END_SILENCE after they stop). This
            # used to record the whole interruption then transcribe it ONCE with slow
            # whisper-cli, which made post-barge replies take ~20s on a real call.
            user_text = self.listen(max_sec=max_wait)
        elif listen_after:
            # normal turn: robust whisper-driven listen (two-phase start/endpoint timing)
            user_text = self.listen(max_sec=max_wait)
        _tlog("capture/listen done -> %r (total converse engine time)" % (user_text[:40]))

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
    s.speak("Hi, this is your assistant. Say something, then pause.")
    print("listening...")
    said = s.listen()
    print("HEARD:", repr(said))
    s.speak("You said: " + (said or "nothing"))
    s.hangup()
    s.shutdown()
    print("done")
