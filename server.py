#!/usr/bin/env -S uv run --quiet
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=1.2.0",
#     "httpx>=0.27",
# ]
# ///
"""Phone Alert MCP.

Gives a calling LLM a single tool — ``alert_me`` — that physically buzzes the
user's phone. The LLM decides *when* an alert is warranted (the criteria live
in the tool's docstring, NOT in this server). This server is only the dial-out:
it pushes the alert to ntfy and/or Pushover.

Backends are chosen by env (``ALERT_CHANNEL`` = ntfy | pushover | both):
  * ntfy     — free, loud push. On iOS it will NOT pierce Focus/silent unless
               the ntfy app is whitelisted per-Focus.
  * pushover — true iOS Critical Alerts (emergency priority pierces Focus +
               silent switch and repeats until acknowledged). $5 one-time.

Run:  uv run server.py        (deps resolved from the PEP-723 block above)
  or: pip install -r requirements.txt && python server.py
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ringback-alert")

# --------------------------------------------------------------------------- #
# Config (all from environment — never hardcode secrets in this file)
# --------------------------------------------------------------------------- #
CHANNEL = os.environ.get("ALERT_CHANNEL", "ntfy").strip().lower()  # ntfy|pushover|both

NTFY_URL = os.environ.get("NTFY_URL", "").strip()        # e.g. https://ntfy.sh/<topic>
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()    # optional (self-host auth)

PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "").strip()
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "").strip()

# SIP call (baresip + a free Linphone account) — places a *ringing* phone call
# that rings the phone full-screen via CallKit and pierces silent/Focus like a
# normal call. Credentials live in the baresip config dir's `accounts` file,
# NOT in this server's env, so no SIP password passes through the MCP client.
SIP_BARESIP_BIN = os.environ.get("SIP_BARESIP_BIN", "/opt/homebrew/bin/baresip")
SIP_CONFIG_DIR = os.environ.get("SIP_CONFIG_DIR", "").strip()   # baresip dir holding the account
SIP_CALLEE = os.environ.get("SIP_CALLEE", "").strip()           # e.g. sip:you@sip.linphone.org
SIP_RING_SECONDS = os.environ.get("SIP_RING_SECONDS", "30").strip()  # auto-hangup after N sec
# A call interrupts harder than a push, so by default it only fires at this
# severity and above (so 'info'/'warn' don't ring your phone).
CALL_MIN_SEVERITY = os.environ.get("CALL_MIN_SEVERITY", "critical").strip().lower()

# Rate-limit guard: at most MAX_ALERTS in a rolling WINDOW_SEC, so a misfiring
# watcher can't turn the phone into an unstoppable siren.
MAX_ALERTS = int(os.environ.get("ALERT_MAX_PER_WINDOW", "5"))
WINDOW_SEC = int(os.environ.get("ALERT_WINDOW_SEC", "60"))

_recent: deque[float] = deque()
_lock = threading.Lock()


def _rate_ok() -> bool:
    now = time.time()
    with _lock:
        while _recent and now - _recent[0] > WINDOW_SEC:
            _recent.popleft()
        if len(_recent) >= MAX_ALERTS:
            return False
        _recent.append(now)
        return True


# severity -> backend-specific knobs
_NTFY_PRIORITY = {"info": "3", "warn": "4", "warning": "4", "critical": "5", "error": "5"}
_NTFY_TAGS = {
    "info": "information_source",
    "warn": "warning",
    "warning": "warning",
    "critical": "rotating_light",
    "error": "rotating_light",
}
_PUSHOVER_PRIORITY = {"info": "0", "warn": "1", "warning": "1", "critical": "2", "error": "2"}

# severity ordering, for gating which channels fire
_SEV_RANK = {"info": 1, "warn": 2, "warning": 2, "critical": 3, "error": 3}


def _send_ntfy(message: str, title: str, severity: str) -> str:
    if not NTFY_URL:
        return "ntfy: skipped (NTFY_URL not set)"
    headers = {
        "Title": title,
        "Priority": _NTFY_PRIORITY.get(severity, "4"),
        "Tags": _NTFY_TAGS.get(severity, "warning"),
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    r = httpx.post(NTFY_URL, content=message.encode("utf-8"), headers=headers, timeout=10.0)
    r.raise_for_status()
    return f"ntfy: sent ({r.status_code})"


def _send_pushover(message: str, title: str, severity: str) -> str:
    if not (PUSHOVER_TOKEN and PUSHOVER_USER):
        return "pushover: skipped (PUSHOVER_TOKEN/PUSHOVER_USER not set)"
    prio = _PUSHOVER_PRIORITY.get(severity, "1")
    data = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title,
        "message": message,
        "priority": prio,
    }
    if prio == "2":  # emergency: must repeat until acknowledged
        data["retry"] = os.environ.get("PUSHOVER_RETRY", "30")    # re-alert every 30s
        data["expire"] = os.environ.get("PUSHOVER_EXPIRE", "300")  # give up after 5 min
    r = httpx.post("https://api.pushover.net/1/messages.json", data=data, timeout=10.0)
    r.raise_for_status()
    return f"pushover: sent ({r.status_code})"


def _send_call(message: str, title: str, severity: str) -> str:
    if not (SIP_CONFIG_DIR and SIP_CALLEE):
        return "call: skipped (SIP_CONFIG_DIR/SIP_CALLEE not set)"
    # Launch baresip detached. It always sends a SIP MESSAGE (the alert text,
    # which lands in Linphone chat with its own push), and ALSO places a ringing
    # call when severity >= CALL_MIN_SEVERITY. The call wakes the killed/locked
    # app full-screen via CallKit; baresip auto-hangs-up after SIP_RING_SECONDS.
    # The current contact in SIP_CONFIG_DIR/contacts is the message recipient.
    do_ring = _SEV_RANK.get(severity, 2) >= _SEV_RANK.get(CALL_MIN_SEVERITY, 3)
    text = f"[{severity}] {title}: {message}".replace("\n", " ").replace("\r", " ")[:300]
    cmd = [SIP_BARESIP_BIN, "-f", SIP_CONFIG_DIR, "-m", "srtp.so", "-e", f"/message {text}"]
    if do_ring:
        cmd += ["-e", f"/dial {SIP_CALLEE}", "-t", SIP_RING_SECONDS]
    else:
        cmd += ["-t", "6"]  # just enough to send the message, then quit
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,   # don't fight the MCP's own stdio transport
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,     # survive past this process; don't get reaped
        )
    except FileNotFoundError:
        return f"call: ERROR baresip not found at {SIP_BARESIP_BIN}"
    return (
        f"call: message sent to {SIP_CALLEE}"
        + (f" + ringing (~{SIP_RING_SECONDS}s)" if do_ring else " (no ring; below threshold)")
    )


def _selected_channels() -> list[str]:
    """Parse ALERT_CHANNEL into a list of backends.

    Accepts: a comma list ("ntfy,call"), "both" (ntfy+pushover, legacy), or
    "all" (every backend).
    """
    if CHANNEL == "both":
        return ["ntfy", "pushover"]
    if CHANNEL == "all":
        return ["ntfy", "pushover", "call"]
    return [c.strip() for c in CHANNEL.split(",") if c.strip()]


def _dispatch(message: str, title: str, severity: str) -> str:
    results: list[str] = []
    for t in _selected_channels():
        try:
            if t == "ntfy":
                results.append(_send_ntfy(message, title, severity))
            elif t == "pushover":
                results.append(_send_pushover(message, title, severity))
            elif t == "call":
                results.append(_send_call(message, title, severity))
            else:
                results.append(f"{t}: unknown channel")
        except Exception as exc:  # surface, don't crash the tool call
            results.append(f"{t}: ERROR {exc!r}")
    return " | ".join(results) if results else "no channel configured"


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def alert_me(message: str, severity: str = "warn", title: str = "Alert") -> str:
    """Physically buzz the user's phone with an urgent push notification.

    The user is NOT watching the screen. Calling this interrupts them in the
    real world, so use it only when that interruption is justified.

    USE FOR:
      - a production alert / pager condition fired
      - a long-running script, deploy, or migration FAILED or crashed
      - a watched value crossed a critical threshold
      - a task the user explicitly asked to be alerted about finished or blocked
      - anything time-sensitive the user would want to know NOW, not later

    DO NOT USE FOR:
      - routine progress updates or status the user can read whenever
      - ordinary task completion that is not time-sensitive
      - anything the user did not ask to be interrupted about
      - chatty / informational messages — those belong in the normal reply

    Args:
        message: One-sentence body, shown on the lock screen. Be specific and
            actionable, e.g. "QA deploy failed: migration 0042 errored on
            playground_management" — not "something went wrong".
        severity: "info" | "warn" | "critical".
            - "critical" = loudest + most intrusive. May place a *ringing phone
              call* (SIP / Linphone — full-screen, pierces silent) and/or a
              Pushover emergency alert that repeats until acknowledged. Reserve
              for things that genuinely must interrupt the user right now.
            - "warn" (default) = high-priority push, non-repeating, no call.
            - "info" = normal-priority push, no call.
        title: Short notification title (a few words).

    Returns a status string describing what each backend did.
    """
    severity = (severity or "warn").strip().lower()
    if not _rate_ok():
        return (
            f"RATE LIMITED: more than {MAX_ALERTS} alerts in {WINDOW_SEC}s. "
            "This alert was suppressed to avoid spamming the phone. If it is "
            "truly critical, wait for the window to clear and retry."
        )
    return _dispatch(message=message, title=title, severity=severity)


@mcp.tool()
def alert_test() -> str:
    """Send a low-priority TEST notification to verify connectivity.

    Safe to call during setup: it always uses 'info' severity so it will not
    fire a loud or repeating critical alert. Use this to confirm the phone
    receives pushes before relying on alert_me for real conditions.
    """
    return _dispatch(
        message="ringback-alert MCP connectivity test - if you see this, setup works.",
        title="ringback-alert test",
        severity="info",
    )


@mcp.tool()
def alert_status() -> str:
    """Report which alert channels are configured (no secrets revealed).

    Useful for debugging setup: shows the active channel, whether each backend
    has its credentials, and the current rate-limit settings.
    """
    lines = [
        f"ALERT_CHANNEL = {CHANNEL}  ->  active: {', '.join(_selected_channels()) or '(none)'}",
        f"ntfy:     {'configured' if NTFY_URL else 'NOT configured (NTFY_URL missing)'}"
        + (" (+auth token)" if NTFY_TOKEN else ""),
        f"pushover: {'configured' if (PUSHOVER_TOKEN and PUSHOVER_USER) else 'NOT configured (token/user missing)'}",
        f"call:     {'configured (' + SIP_CALLEE + ')' if (SIP_CONFIG_DIR and SIP_CALLEE) else 'NOT configured (SIP_CONFIG_DIR/SIP_CALLEE missing)'}"
        + f"  [rings on severity >= {CALL_MIN_SEVERITY}]",
        f"rate limit: max {MAX_ALERTS} alerts / {WINDOW_SEC}s",
    ]
    return "\n".join(lines)


def main() -> None:
    mcp.run()  # stdio transport — for Claude Desktop / Claude Code


if __name__ == "__main__":
    main()
