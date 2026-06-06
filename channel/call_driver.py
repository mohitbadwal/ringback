#!/usr/bin/env python3
"""ringback call-driver — the REAL version of channel/inject.sh.

Turns "an answer arrives via /inject" into an actual phone call. It:
  1. dials you (reusing CallSession from voice_agent.py — IMPORTED, never edited),
  2. speaks the agent's question,
  3. listens to your spoken reply and transcribes it,
  4. POSTs that answer to the ringback channel's /inject endpoint, so the idle
     session wakes and continues, and
  5. while the call is up, relays the session's `say` replies (tailed from the
     channel's outbound.jsonl) back onto the call, answering follow-up questions.

Reuse boundary: imports `voice_agent.CallSession` as-is. Additive only — the
ringback-voice MCP and its tools are untouched.

Run via channel/run_call_driver.sh (it sets the pjsua2 env like run_voice_mcp.sh).

Examples:
  ./channel/run_call_driver.sh --dry-run
  ./channel/run_call_driver.sh --question "Which option, A or B?" --no-inject   # phone half only
  ./channel/run_call_driver.sh --question "Which option, A or B?"               # full loop
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOUND = os.path.join(HERE, "outbound.jsonl")   # where the channel logs Claude's `say`
LOCK = os.path.join(HERE, ".call_active")          # cross-process "a call is in flight"


def post_inject(url: str, content: str, call_id: str, token: str = "") -> int:
    body = json.dumps({"content": content, "call_id": call_id}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Ringback-Token", token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def outbound_line_count() -> int:
    try:
        with open(OUTBOUND) as f:
            return sum(1 for _ in f)
    except FileNotFoundError:
        return 0


def read_new_says(baseline: int):
    """Return (new say-records appended after `baseline` lines, updated baseline)."""
    try:
        with open(OUTBOUND) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return [], baseline
    recs = []
    for ln in lines[baseline:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    return recs, len(lines)


def looks_like_question(t: str) -> bool:
    return t.strip().endswith("?")


def dry_run() -> int:
    import shutil
    ok = True
    for tool in (os.environ.get("WHISPER_BIN", "whisper-cli"),
                 os.environ.get("FFMPEG_BIN", "ffmpeg"), "say"):
        p = shutil.which(tool)
        print(f"  {tool}: {'found ' + p if p else 'MISSING'}")
        ok = ok and bool(p)
    print("  pjsua2 import: OK, CallSession available")
    callee = os.environ.get("VOICE_SIP_CALLEE", os.environ.get("VOICE_SIP_ID", "(unset)"))
    print(f"  VOICE_SIP_ID={os.environ.get('VOICE_SIP_ID', '(unset)')}  CALLEE={callee}")
    has_pass = bool(os.environ.get("VOICE_SIP_PASS"))
    print(f"  VOICE_SIP_PASS: {'set' if has_pass else 'MISSING (needed to authenticate the call)'}")
    print("DRY RUN OK" if (ok and has_pass) else "DRY RUN: missing deps/config")
    return 0 if (ok and has_pass) else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="ringback call-driver")
    ap.add_argument("--question", help="What to ask the user on the call.")
    ap.add_argument("--call-id", default="phone")
    ap.add_argument("--channel-url",
                    default=f"http://127.0.0.1:{os.environ.get('RINGBACK_CHANNEL_PORT', '8790')}/inject")
    ap.add_argument("--token", default=os.environ.get("RINGBACK_CHANNEL_TOKEN", ""))
    ap.add_argument("--max-call-sec", type=float, default=180.0)
    ap.add_argument("--say-wait", type=float, default=30.0,
                    help="Seconds to wait for the session's spoken reply before ending.")
    ap.add_argument("--no-inject", action="store_true",
                    help="Call/ask/print only; don't POST or relay (phone-half test).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Check env/deps without placing a call.")
    args = ap.parse_args()

    # Import here so --help / arg errors don't require the pjsua2 env. (run_call_driver.sh
    # sets PYTHONPATH so both voice_agent and pjsua2 resolve.)
    from voice_agent import CallSession  # noqa: E402  imported as-is, never edited

    if args.dry_run:
        return dry_run()
    if not args.question:
        ap.error("--question is required (unless --dry-run)")

    # cross-process call lock so the Stop hook / ask_user_by_phone don't double-dial
    try:
        with open(LOCK, "w") as f:
            json.dump({"by": "call_driver", "ts": time.time()}, f)
    except OSError:
        pass

    s = CallSession()
    try:
        s.start_lib()
        print("placing call...", flush=True)
        if not s.place_call():
            print("call not answered", flush=True)
            s.shutdown()
            return 1
        print("connected.", flush=True)
        start = time.time()
        baseline = outbound_line_count()

        # 1) ask the question; capture the spoken answer (one retry if we miss it)
        s.speak(args.question)
        ans = s.listen()
        if not ans:
            s.speak("Sorry, I didn't catch that. Please say it again after the beep.")
            ans = s.listen()
        print("HEARD:", repr(ans), flush=True)
        if ans and not args.no_inject:
            try:
                post_inject(args.channel_url, ans, args.call_id, args.token)
                print("posted answer to channel /inject", flush=True)
            except Exception as e:  # noqa: BLE001
                print("inject failed:", e, flush=True)

        # 2) relay the session's `say` replies back onto the call; answer follow-ups
        if not args.no_inject:
            while time.time() - start < args.max_call_sec and not s.disconnected:
                recs = []
                wait_end = time.time() + args.say_wait
                while time.time() < wait_end and not s.disconnected:
                    recs, baseline = read_new_says(baseline)
                    if recs:
                        break
                    time.sleep(0.5)
                if not recs:
                    break  # session went quiet -> wrap up the call
                for rec in recs:
                    txt = (rec.get("text") or "").strip()
                    if not txt:
                        continue
                    print("SAY -> speak:", txt, flush=True)
                    s.speak(txt)
                    if looks_like_question(txt):
                        follow = s.listen()
                        print("HEARD:", repr(follow), flush=True)
                        if follow:
                            try:
                                post_inject(args.channel_url, follow, args.call_id, args.token)
                            except Exception as e:  # noqa: BLE001
                                print("inject failed:", e, flush=True)

        s.speak("Okay, talk to you later.")
        s.hangup()
        s.shutdown()
        print("call ended", flush=True)
        return 0
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
