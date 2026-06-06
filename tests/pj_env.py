"""Shared helper: build the environment a pjsua2 subprocess needs (PYTHONPATH to the SWIG
build dir + the OS dynamic-linker path to the pjproject libs). Mirrors run_voice_mcp.py so
pjsua2 tests can spawn workers without a separate launcher.

Cross-platform: sets DYLD_LIBRARY_PATH (macOS), LD_LIBRARY_PATH (Linux) or PATH (Windows).
If pjsua2 is already importable via the current environment (e.g. the Docker image exports
PYTHONPATH=/opt/pjsua2 and LD_LIBRARY_PATH=/usr/local/lib), that is preserved as-is.
"""
import glob
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from platform_compat import IS_MAC, lib_path_var  # noqa: E402


def pj_env(extra=None):
    env = dict(os.environ)
    home = os.path.expanduser("~")
    pjdir = os.environ.get("PJPROJECT_DIR", os.path.join(home, "build", "pjproject-2.17"))
    swig = sorted(glob.glob(os.path.join(pjdir, "pjsip-apps/src/swig/python/build/lib.*")))

    if swig:
        # a source build exists under PJPROJECT_DIR -> point at it (mirrors the launcher)
        libdirs = [os.path.join(pjdir, p, "lib") for p in
                   ("pjlib", "pjlib-util", "pjnath", "pjmedia", "pjsip", "third_party")]
        if IS_MAC:
            openssl = "/opt/homebrew/opt/openssl@3"
            if not os.path.isdir(openssl):
                openssl = "/usr/local"
            libdirs.append(os.path.join(openssl, "lib"))
        var = lib_path_var()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([swig[0], _ROOT]
                                            + ([existing_pp] if existing_pp else []))
        existing_lv = env.get(var, "")
        env[var] = os.pathsep.join(libdirs + ([existing_lv] if existing_lv else []))
    else:
        # No source build under PJPROJECT_DIR (e.g. the Docker image installs the bindings
        # elsewhere and already exports the right PYTHONPATH / lib path) — just make sure the
        # repo root is importable and leave the working environment intact.
        existing_pp = env.get("PYTHONPATH", "")
        if _ROOT not in existing_pp.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join([_ROOT] + ([existing_pp] if existing_pp else []))

    if extra:
        env.update(extra)
    return env
