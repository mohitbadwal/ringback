"""Shared helper: build the environment a pjsua2 subprocess needs (PYTHONPATH to the SWIG
build dir, DYLD_LIBRARY_PATH to the pjproject dylibs + openssl). Mirrors run_voice_mcp.sh
so pjsua2 tests can spawn workers without a separate launcher script."""
import glob
import os


def pj_env(extra=None):
    home = os.path.expanduser("~")
    pjdir = os.environ.get("PJPROJECT_DIR", os.path.join(home, "build", "pjproject-2.17"))
    swig = sorted(glob.glob(os.path.join(pjdir, "pjsip-apps/src/swig/python/build/lib.*")))
    swig_lib = swig[0] if swig else ""
    openssl = "/opt/homebrew/opt/openssl@3"
    if not os.path.isdir(openssl):
        openssl = "/usr/local"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dyld = ":".join([
        os.path.join(pjdir, "pjlib/lib"), os.path.join(pjdir, "pjlib-util/lib"),
        os.path.join(pjdir, "pjnath/lib"), os.path.join(pjdir, "pjmedia/lib"),
        os.path.join(pjdir, "pjsip/lib"), os.path.join(pjdir, "third_party/lib"),
        os.path.join(openssl, "lib"),
    ])
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{swig_lib}:{repo}"
    env["DYLD_LIBRARY_PATH"] = dyld
    if extra:
        env.update(extra)
    return env
