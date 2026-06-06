#!/usr/bin/env python3
"""ringback Stop hook — the fully-automatic backstop.

Fires when a Claude Code session ends a turn. If the user is AWAY and the agent
just asked them something (ended its turn with a question), and no call is already
in flight, it phones the user with that question via the call-driver — even when
the agent didn't think to call `ask_user_by_phone` itself.

ALL of these gates must pass before it dials (so it's quiet in normal use):
  0. ringback is actually set up (SIP config present) — else no-op, harmless in a clone
  1. user is AWAY      — macOS HID idle > RINGBACK_AWAY_IDLE_SEC (default 300s)
  2. no active call    — channel/.call_active lockfile absent/stale
  3. it's a question   — the agent's last message to the user ends with "?"
  4. not a repeat      — we haven't already handled this exact turn

Env knobs (handy for testing):
  RINGBACK_AWAY_IDLE_SEC  override the "away" threshold (set 0 to always pass)
  RINGBACK_HOOK_DRYRUN=1  log the decision instead of placing a call
  RINGBACK_CHANNEL_PORT   port of the session's channel /inject endpoint

Wired via the repo .claude/settings.json (Stop event). The agent's deliberate
`ask_user_by_phone` is the precise path; this heuristic hook is the safety net.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, ".call_active")
STATE = os.path.join(HERE, ".stop_hook_state.json")
HOOKLOG = os.path.join(HERE, "stop_hook.log")
AWAY_IDLE_SEC = float(os.environ.get("RINGBACK_AWAY_IDLE_SEC", "300"))
LOCK_STALE_SEC = 900
DRYRUN = os.environ.get("RINGBACK_HOOK_DRYRUN") == "1"


def log(m):
    try:
        with open(HOOKLOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {m}\n")
    except OSError:
        pass


def hid_idle_seconds():
    """macOS: seconds since the last keyboard/mouse input. Large => user is away."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1e9
    except Exception:
        pass
    return 0.0


def lock_active():
    if not os.path.exists(LOCK):
        return False
    try:
        return (time.time() - os.path.getmtime(LOCK)) < LOCK_STALE_SEC
    except OSError:
        return False


def last_assistant_text(transcript_path):
    """(text, uuid) of the MOST RECENT assistant message's text blocks ('' if none)."""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except OSError:
        return "", ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        msg = m.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content")
            texts = []
            if isinstance(content, list):
                texts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
            # return on the FIRST (most recent) assistant message, even if it has no
            # text — that turn is the one that matters; don't reach back to an older one.
            return " ".join(t.strip() for t in texts if t.strip()), m.get("uuid", "")
    return "", ""


def looks_like_question(t):
    return t.strip().endswith("?")


def already_handled(uuid):
    try:
        return json.load(open(STATE)).get("last_uuid") == uuid
    except Exception:
        return False


def mark_handled(uuid):
    try:
        json.dump({"last_uuid": uuid, "ts": time.time()}, open(STATE, "w"))
    except OSError:
        pass


def main():
    raw = sys.stdin.read()
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except ValueError:
        ev = {}
    transcript = ev.get("transcript_path", "")

    # gate 0: ringback must be configured, else stay completely out of the way.
    if not (os.environ.get("VOICE_SIP_PASS") or os.path.exists(os.path.join(HERE, "..", "voice.env"))):
        return 0

    idle = hid_idle_seconds()
    if idle < AWAY_IDLE_SEC:
        log(f"skip: user present (idle {idle:.0f}s < {AWAY_IDLE_SEC:.0f}s)")
        return 0
    if lock_active():
        log("skip: a call is already active")
        return 0
    # the transcript can lag the Stop event by a beat — retry briefly until the
    # agent's final message is flushed (only loops in the empty/race case).
    text, uuid = "", ""
    for _ in range(8):
        text, uuid = last_assistant_text(transcript)
        if text:
            break
        time.sleep(0.4)
    if not looks_like_question(text):
        log(f"skip: not a question: {text[:80]!r} (transcript={os.path.basename(transcript) or '(none)'})")
        return 0
    if already_handled(uuid):
        log("skip: already handled this turn")
        return 0

    mark_handled(uuid)
    if DRYRUN:
        log(f"DRYRUN would call. idle={idle:.0f}s question={text[:160]!r}")
        print(f"[ringback stop-hook] DRYRUN would phone the user: {text[:120]}", file=sys.stderr)
        return 0

    # place the call: write the lockfile FIRST (race-free vs ask_user_by_phone),
    # then spawn the call-driver detached. The call-driver removes the lock on exit.
    try:
        with open(LOCK, "w") as f:
            json.dump({"by": "stop_hook", "ts": time.time()}, f)
        env = dict(os.environ)
        env["RINGBACK_CHANNEL_PORT"] = os.environ.get("RINGBACK_CHANNEL_PORT", "8790")
        out = open(os.path.join(HERE, "call_driver.log"), "a")
        subprocess.Popen(["bash", os.path.join(HERE, "run_call_driver.sh"),
                          "--question", text, "--call-id", "phone", "--say-wait", "45"],
                         cwd=os.path.join(HERE, ".."), env=env,
                         stdout=out, stderr=out, start_new_session=True)
        log(f"PLACED call. idle={idle:.0f}s question={text[:160]!r}")
    except Exception as e:
        log(f"ERROR placing call: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
