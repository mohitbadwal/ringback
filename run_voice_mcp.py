#!/usr/bin/env python3
"""Cross-platform launcher for the ringback-voice MCP (macOS / Linux / Windows/WSL).

Mirrors run_voice_mcp.sh but works on any OS: it sets up the environment so
`import pjsua2` (built from source by setup.sh / setup-linux.sh) resolves, then
re-execs voice_mcp.py under that environment. The dynamic-linker search path
(DYLD_LIBRARY_PATH on macOS, LD_LIBRARY_PATH on Linux, PATH on Windows) must be
in place BEFORE the Python that loads pjsua2 starts, which is why we re-exec.

Register it with your MCP client as:  python run_voice_mcp.py
Overrides (export before launch): PJPROJECT_DIR, PYTHON_BIN, OPENSSL_PREFIX.

macOS note: the original run_voice_mcp.sh is unchanged and remains the macOS
default; this launcher also works there but isn't required.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

APP = os.path.dirname(os.path.abspath(__file__))


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _is_win() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def _lib_path_var() -> str:
    if _is_mac():
        return "DYLD_LIBRARY_PATH"
    if _is_win():
        return "PATH"
    return "LD_LIBRARY_PATH"


def _load_env_file(path: str) -> None:
    """Source a KEY=VALUE / `export KEY=VALUE` file into os.environ (voice.env)."""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ[key] = val


def _openssl_prefix() -> str:
    pref = os.environ.get("OPENSSL_PREFIX", "").strip()
    if pref:
        return pref
    if _is_mac():
        try:
            out = subprocess.run(["brew", "--prefix", "openssl@3"],
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
        return "/usr/local"
    return ""   # Linux/Windows: system OpenSSL is already on the loader path


def main() -> int:
    _load_env_file(os.path.join(APP, "voice.env"))

    pjdir = os.environ.get("PJPROJECT_DIR", os.path.expanduser("~/build/pjproject-2.17"))
    python_bin = os.environ.get("PYTHON_BIN", sys.executable)

    swig_matches = sorted(glob.glob(
        os.path.join(pjdir, "pjsip-apps", "src", "swig", "python", "build", "lib.*")))
    swig_lib = swig_matches[0] if swig_matches else ""
    if not swig_lib or not os.path.isdir(os.path.join(pjdir, "pjlib", "lib")):
        sys.stderr.write(
            f"ringback-voice: pjsua2 build not found under PJPROJECT_DIR={pjdir}\n"
            "Run ./setup.sh (macOS) or ./setup-linux.sh (Linux) first — it compiles\n"
            "pjproject + the Python bindings.\n")
        return 1

    # PYTHONPATH: SWIG bindings dir + the app dir (so voice_mcp/voice_agent/platform_compat
    # all resolve).
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [swig_lib, APP] + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH") else []))

    # Dynamic-linker search path for the pjproject shared libs (+ OpenSSL where needed).
    lib_dirs = [os.path.join(pjdir, p, "lib") for p in
                ("pjlib", "pjlib-util", "pjnath", "pjmedia", "pjsip", "third_party")]
    ossl = _openssl_prefix()
    if ossl:
        lib_dirs.append(os.path.join(ossl, "lib"))
    var = _lib_path_var()
    existing = os.environ.get(var, "")
    os.environ[var] = os.pathsep.join(lib_dirs + ([existing] if existing else []))

    # Make common tool dirs discoverable (ffmpeg/whisper/piper/say). Harmless if absent.
    extra_path = []
    if _is_mac():
        extra_path.append("/opt/homebrew/bin")
    extra_path.append(os.path.expanduser("~/.local/bin"))
    os.environ["PATH"] = os.pathsep.join(
        extra_path + ([os.environ["PATH"]] if os.environ.get("PATH") else []))

    server = os.path.join(APP, "voice_mcp.py")
    if _is_win():
        # os.execv on Windows can mangle stdio for the MCP transport; spawn + wait instead.
        return subprocess.run([python_bin, server]).returncode
    os.execv(python_bin, [python_bin, server])
    return 0  # unreachable on POSIX (execv replaces the process)


if __name__ == "__main__":
    sys.exit(main())
