"""platform_compat.py — OS abstraction seams for ringback (macOS / Linux / Windows).

The voice + alert engines are otherwise platform-neutral; the OS-specific bits live
here so voice_agent.py / server.py / channel/stop_hook.py stay clean:
  - detached_popen_kwargs(): spawn a child that outlives the parent (POSIX vs Windows)
  - lib_path_var():          name of the dynamic-linker search-path env var per OS
  - hid_idle_seconds():      seconds since last user input (presence detection)
  - synthesize_to_wav():     text -> 16 kHz mono 16-bit WAV via Piper (default) /
                             say / espeak / SAPI / a custom command

Nothing here imports pjsua2 or any heavy dependency, so it is safe to import anywhere.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt" or sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")

# Piper neural TTS (cross-platform default). "Available" means the binary is on PATH
# AND the voice model file exists; otherwise we transparently fall back to the OS voice.
PIPER_BIN = os.environ.get("VOICE_PIPER_BIN", "piper")
PIPER_MODEL = os.environ.get(
    "VOICE_PIPER_MODEL", os.path.expanduser("~/.piper-voices/en_US-lessac-medium.onnx"))


def _rm(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---- detached child processes ------------------------------------------------
def detached_popen_kwargs() -> dict:
    """subprocess.Popen kwargs to fully detach a child so it outlives this process and
    isn't reaped when we exit (whisper-server, baresip, the call driver). POSIX starts a
    new session; Windows uses detached / new-process-group creation flags."""
    if IS_WIN:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) \
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


# ---- dynamic-linker search path ----------------------------------------------
def lib_path_var() -> str:
    """Name of the env var the OS uses to find shared libraries at runtime (pjproject's
    dylibs/so's + OpenSSL). The launcher sets this to the pj lib dirs."""
    if IS_MAC:
        return "DYLD_LIBRARY_PATH"
    if IS_WIN:
        return "PATH"            # Windows resolves DLLs via PATH
    return "LD_LIBRARY_PATH"     # Linux / other Unix


# ---- presence / idle detection -----------------------------------------------
def hid_idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input. Large => the user is away.

    The watchdog/stop-hook uses this to gate phone escalation (only escalate when away).
    Returns 0.0 ("present" — the conservative, do-not-escalate default) when idle can't
    be determined (e.g. Wayland, headless). Force it with RINGBACK_PRESENCE=present|absent.
    """
    override = os.environ.get("RINGBACK_PRESENCE", "").strip().lower()
    if override == "present":
        return 0.0
    if override == "absent":
        return 1e9               # effectively "always away"
    if IS_MAC:
        return _idle_macos()
    if IS_WIN:
        return _idle_windows()
    return _idle_linux()


def _idle_macos() -> float:
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1e9
    except Exception:
        pass
    return 0.0


def _idle_windows() -> float:
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
            return max(0.0, millis / 1000.0)
    except Exception:
        pass
    return 0.0


def _idle_linux() -> float:
    # X11: xprintidle prints idle time in milliseconds.
    exe = shutil.which("xprintidle")
    if exe:
        try:
            out = subprocess.run([exe], capture_output=True, text=True, timeout=5).stdout.strip()
            return max(0.0, int(out) / 1000.0)
        except Exception:
            pass
    # GNOME (Mutter) idle monitor over D-Bus -> milliseconds.
    gdbus = shutil.which("gdbus")
    if gdbus:
        try:
            out = subprocess.run(
                [gdbus, "call", "--session",
                 "--dest", "org.gnome.Mutter.IdleMonitor",
                 "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                 "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"],
                capture_output=True, text=True, timeout=5).stdout
            digits = "".join(ch for ch in out if ch.isdigit())
            if digits:
                return max(0.0, int(digits) / 1000.0)
        except Exception:
            pass
    # Wayland / headless: no standard idle source. Treat as present (don't auto-escalate)
    # unless RINGBACK_PRESENCE=absent was set above.
    return 0.0


# ---- text-to-speech ----------------------------------------------------------
def piper_available() -> bool:
    return bool(shutil.which(PIPER_BIN)) and os.path.exists(PIPER_MODEL)


def tts_engine() -> str:
    """Which TTS engine to use. VOICE_TTS forces it (piper|say|espeak|sapi); the default
    'auto' prefers Piper when installed, else the OS-native fast path."""
    choice = os.environ.get("VOICE_TTS", "auto").strip().lower()
    if choice and choice != "auto":
        return choice
    if piper_available():
        return "piper"
    if IS_MAC:
        return "say"
    if IS_WIN:
        return "sapi"
    return "espeak"


def _ffmpeg_to_16k_mono(src: str, dst: str) -> None:
    """Normalize any TTS output to the 16 kHz mono 16-bit PCM WAV pjsua2 expects."""
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", src,
                    "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le", dst], check=True)


def _synth_say(text: str) -> str:
    aiff = tempfile.mktemp(suffix=".aiff")
    subprocess.run(["say", "-o", aiff, text], check=True)
    return aiff


def _synth_piper(text: str) -> str:
    wav = tempfile.mktemp(suffix=".wav")
    # piper reads the text on stdin and writes a WAV to -f/--output_file. The matching
    # <model>.onnx.json config must sit next to the .onnx model.
    subprocess.run([PIPER_BIN, "-m", PIPER_MODEL, "-f", wav],
                   input=text, text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return wav


def _synth_espeak(text: str) -> str:
    wav = tempfile.mktemp(suffix=".wav")
    exe = shutil.which("espeak-ng") or shutil.which("espeak") or "espeak-ng"
    subprocess.run([exe, "-w", wav, text], check=True)
    return wav


def _synth_sapi(text: str) -> str:
    wav = tempfile.mktemp(suffix=".wav")
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{wav}'); "
        "$s.Speak([Console]::In.ReadToEnd()); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   input=text, text=True, check=True)
    return wav


def _synth_custom(template: str, text: str) -> str:
    """VOICE_TTS_CMD is a command template with {text} and {out} placeholders, e.g.
    'mytts --say {text} --wav {out}'. It must write a WAV file to {out}."""
    wav = tempfile.mktemp(suffix=".wav")
    cmd = [a.replace("{out}", wav).replace("{text}", text) for a in shlex.split(template)]
    subprocess.run(cmd, check=True)
    return wav


def _dispatch(engine: str, text: str) -> str:
    if engine == "piper":
        return _synth_piper(text)
    if engine == "say":
        return _synth_say(text)
    if engine in ("espeak", "espeak-ng"):
        return _synth_espeak(text)
    if engine == "sapi":
        return _synth_sapi(text)
    raise RuntimeError(f"unknown VOICE_TTS engine: {engine!r}")


def _os_native_engine() -> str:
    return "say" if IS_MAC else ("sapi" if IS_WIN else "espeak")


def synthesize_to_wav(text: str, out_wav: str) -> str:
    """Render `text` to a 16 kHz mono 16-bit PCM WAV at `out_wav` (pjsua2's format).

    Engine selected by VOICE_TTS (default 'auto': Piper if installed, else the OS-native
    voice). VOICE_TTS_CMD overrides everything with a custom command template. If the
    selected engine FAILS at runtime, we fall back to the OS-native voice so TTS never
    hard-fails — e.g. a misconfigured Piper on macOS still degrades to `say`.
    """
    custom = os.environ.get("VOICE_TTS_CMD", "").strip()
    if custom:
        produced = _synth_custom(custom, text)
    else:
        engine = tts_engine()
        try:
            produced = _dispatch(engine, text)
        except Exception:
            fallback = _os_native_engine()
            if engine == fallback:
                raise
            produced = _dispatch(fallback, text)
    try:
        _ffmpeg_to_16k_mono(produced, out_wav)
    finally:
        _rm(produced)
    return out_wav
