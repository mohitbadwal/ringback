---
name: watchdog
description: Arm an autonomous escalation watchdog that monitors a situation and reaches you by chat → warning → phone alert → phone call, but only when you are actually away from the laptop (judged by macOS HID idle time). Use when the user wants to be notified/called about a long-running task, deploy, script run, or any condition while they step away. Arming auto-starts the monitoring loop (via ScheduleWakeup) — no manual /loop needed.
user-invocable: true
disable-model-invocation: false
argument-hint: "<what to watch> | priority=<low|medium|critical>"
---

# Watchdog — autonomous, presence-aware escalation

Turns this session into a watchdog: each tick it checks a **watched condition**, and when that condition needs you, it decides **whether and how to reach you** based on **whether you're at the laptop**. It never interrupts you while you're actively typing; it escalates only after you've gone idle and stayed idle through a short confirmation window.

Escalation ladder (how far it climbs is capped by the declared priority):

```
chat status  →  chat WARNING  →  phone ALERT (one-way)  →  phone CALL (two-way)
   always         user away         away + warned             away + still silent
```

- `priority=low`     → chat only, never alert/call.
- `priority=medium`  → may climb to phone **alert** (one-way SIP message / push). Default.
- `priority=critical`→ may climb to phone **call** (live conversation).

Backends: `mcp__ringback-alert__alert_me` (one-way) and `mcp__ringback-voice__call_start` + `converse` (two-way) — the two MCP servers from this repo.

---

## State file

All cross-tick state lives in `~/.claude/watchdog/state.json`. Read it at the start of every tick; write it before finishing. Shape:

```json
{
  "task": "human description of what is being watched",
  "condition": "exactly how to check it each tick (command/API + what counts as 'needs me')",
  "priority": "medium",
  "armed_at": "2026-05-24T10:00:00Z",
  "rung": "monitoring",          // monitoring | warned | alerted | called | resolved
  "warned_at": null,             // ISO time the chat WARNING was posted
  "last_idle_seconds": 0,
  "notes": ""                    // anything to carry forward (run IDs, etc.)
}
```

`rung` meanings: `monitoring` = nothing fired yet; `warned` = warning posted, in the 1–2 min confirmation window; `alerted` = one-way alert sent; `called` = phone call placed; `resolved` = watch is fully done and the user has been informed — stop looping. Note: a call where the user hands over a NEW task does NOT go to `resolved` — it rewrites `task`/`condition` and goes back to `monitoring` (see "Resolving vs. pivoting after contact").

---

## Arming (first invocation) — also STARTS the loop

When invoked with arguments, **set up** the watch and **start the monitoring loop yourself**. Do NOT escalate on this invocation.

1. Parse `<what to watch>` and `priority=` from the args. If priority is omitted, ask the user (low/medium/critical) — it controls the escalation ceiling, so don't guess on something that may phone them.
2. Pin down the **machine-checkable condition**: e.g. `gh run view <id>` status, a New Relic query, a file/marker, a pod state. Write it into `condition` precisely enough that a future tick can evaluate it without you re-asking.
3. Write the state file with `rung: "monitoring"`, `armed_at` = now. Store the chosen base interval in `notes` if useful.
4. **Start the loop automatically** — do NOT make the user type `/loop`. Call `ScheduleWakeup` with `prompt: "/watchdog"` and a `delaySeconds` that fits the situation (default ~180s; faster for things that can fail quickly, e.g. 90–120s). This re-fires the skill as a tick. The user does not need to do anything else.
5. Confirm to the user: what's being watched, the priority/ceiling, the interval, and that they can disarm by deleting `~/.claude/watchdog/state.json` (or telling you to stop). Then stop — do not check idle or escalate on the arming invocation.

---

## Each loop tick — main agent delegates the work to a fanned-out agent

The tick is split into two roles so the main session stays clean over many ticks:

- **Main agent = thin loop heartbeat.** It owns scheduling (only the main session can `ScheduleWakeup` itself). It does NOT do the evaluation in-line.
- **Fanned-out tick agent = the brain.** A `general-purpose` sub-agent does the actual evaluate → decide → escalate work (including placing the phone call) and returns a compact verdict. Its tool calls (ioreg, `gh run view`, MCP phone tools, etc.) stay in the sub-agent, not the main transcript.

### Main agent steps (run on every ScheduleWakeup firing of `/watchdog`)

1. **Spawn the tick agent.** Use the `Agent` tool, `subagent_type: "general-purpose"`, with a prompt that says: *"Run ONE watchdog tick. Read `~/.claude/watchdog/state.json`, then follow the 'Fanned-out tick agent task' in `~/.claude/skills/watchdog/SKILL.md` exactly. Do all evaluation, presence-check, escalation (chat/alert/call) and state writes yourself. Return ONLY this JSON: `{rung, escalated, summary, next_delay_seconds, resolved}`."* Pass along the state-file path; the agent reads everything else from the file + SKILL.md.
2. **Read the returned verdict.** If `resolved: true` (or the agent reports the state file is gone) → the watch is over; do NOT reschedule. Optionally relay the one-line `summary` to the user.
3. **Reschedule.** Otherwise call `ScheduleWakeup` with `prompt: "/watchdog"` and `delaySeconds = next_delay_seconds` from the verdict (the agent computes this from the rung — 60–90s while `warned`, else the base interval). This is the only place the loop reschedules.

Keep the main agent's own footprint to those three steps — spawn, read verdict, reschedule. Don't re-do the agent's work or echo its tool output.

---

## Fanned-out tick agent task

This is what the spawned `general-purpose` agent executes each tick. Run these steps in order; keep it cheap and silent unless something changed. End by returning the verdict JSON in step 6 — the agent does NOT call `ScheduleWakeup` (the main agent owns that).

### 1. Load state
Read `~/.claude/watchdog/state.json`. If missing → nothing is armed (user disarmed); return `{resolved: true, summary: "disarmed"}`. If `rung == "resolved"` → return `{resolved: true}`.

### 2. Evaluate the watched condition
Run the `condition` check. Classify the result:
- **NOT-READY** — situation ongoing, nothing to report → go to step 5 (idle bookkeeping) and finish quietly (no chat spam).
- **NEEDS-USER** — finished, failed, blocked on a decision, or hit the alert condition → continue to step 3.

### 3. Measure presence (am I away?)
Get current HID idle seconds:
```bash
ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}'
```
- **ACTIVE** if idle `< 120s` → you're at the keyboard. Do NOT alert or call. Post the status in **chat only** (that's the right channel when you're looking at the screen), set `rung` appropriately, and finish. If you'd previously `warned`/`alerted`, note "you're back — handling in chat" and reset `rung` to `monitoring` (de-escalated).
- **AWAY** if idle `>= 300s` → escalation allowed; go to step 4.
- **In-between (120–300s)** → treat as borderline-present: post chat status, hold escalation one more tick, leave `rung` unchanged.

### 4. Escalate (only when AWAY) — climb one rung per tick, capped by priority

Decide the rung from current `rung` + priority:

- **From `monitoring` → WARN.** Post a chat warning naming the situation and that you'll escalate:
  `⚠️ <task>: <what happened>. You look away (idle <Xm>). If you don't respond in ~1–2 min, I'll <alert|call> you.`
  Use "call" if priority=critical, else "alert". Set `rung: "warned"`, `warned_at: now`. Finish — the next tick (keep the loop interval ~60–90s here so the confirmation window is genuinely 1–2 min) is the confirmation.
  - If `priority == "low"`: never go past this — just keep posting chat status, stay on `monitoring`.

- **From `warned` → escalate for real**, but ONLY if: still AWAY *and* at least ~60s since `warned_at` *and* the user hasn't responded (idle never dropped below 120s in between — if it did, step 3 already de-escalated you).
  - `priority == "medium"` → `mcp__ringback-alert__alert_me(message, severity, title)` with a one-line plain-language summary. Set `rung: "alerted"`. Do not climb to a call.
  - `priority == "critical"` → place a **call**: `mcp__ringback-voice__call_start(opening_line=...)` then drive the conversation (alternate `converse`; never read logs/UUIDs aloud; end with `call_end`). Set `rung: "called"`.

- **From `alerted` → CALL** (medium that stayed unacknowledged does NOT auto-call; only climb to a call if priority is critical). For critical, if the earlier alert went unanswered and you're still away, place the call as above and set `rung: "called"`.

- **From `called`** → you've done the most intrusive thing allowed. Don't re-call on every tick. Hold; only re-call if a *new* distinct condition fires.

### Resolving vs. pivoting after contact

When a call/alert connects, decide based on what the user actually said:

- **Mere acknowledgement** ("ok", "thanks", "got it", "I'll look later", just hangs up) → the watch is done: set `rung: "resolved"`. The main agent stops the loop.
- **The user gives a NEW task or instruction during the call** ("retry the deploy and tell me when it's green", "watch the worker pod and call me if it crashes again", "kick off X then keep an eye on it") → **DO NOT resolve.** The watchdog keeps going:
  1. Rewrite `task` and `condition` in `state.json` to the new instruction (the new machine-checkable thing to watch / the success criterion the user gave).
  2. If the user asked you to *do* something first (retry, kick off a job), do it, and record any handle (run ID, pod name) in `notes`.
  3. Reset `rung: "monitoring"`, clear `warned_at`. Keep `priority` (or update it if the user implied urgency).
  4. Return `resolved: false` so the loop continues against the new condition.
- **Only an explicit stop ends a pivoted watch** — "stop the watchdog", "you can stop now", "we're done", or disarming (deleting the state file). Finishing one handed-off task does NOT auto-resolve if the user framed it as ongoing monitoring; when a discrete task the user gave is fully done, report it (chat if present, else the normal escalation ladder) and then treat *that* completion as a fresh NEEDS-USER contact — let the user say stop.

Capture the spoken instruction faithfully (use `get_conversation()` to re-read the exact words before rewriting `condition`) so the next tick watches the right thing.

### 5. Idle bookkeeping & persist
Write `last_idle_seconds` and any updated `rung`/`warned_at`/`notes` back to the state file.

### 6. Return the verdict (do NOT schedule — the main agent does that)
Return ONLY this JSON to the main agent:
```json
{"rung": "<current rung>", "escalated": true|false, "summary": "<one line: what you did this tick>",
 "next_delay_seconds": <int>, "resolved": true|false}
```
Compute `next_delay_seconds` from the rung you ended on:
- `warned` → **60–90s** so the confirmation window is genuinely 1–2 min (this is what makes the "respond or I'll call" promise accurate).
- otherwise → the base interval (~180s, or whatever fits the situation's failure speed; reuse the value in `notes` if set).
Set `resolved: true` once the watched condition is fully handled / acknowledged (or state was deleted) so the main agent stops the loop.

---

## Acknowledgement & de-escalation

The user "acknowledging" = they touch the laptop (idle drops below 120s) or send a message. Because step 3 runs every tick, presence is re-checked continuously: the moment you're active again, the watchdog drops back to chat-only and won't alert/call for that same condition. This is the core safety property — **it can only escalate against a confirmed-absent user.**

## Disarming

To stop: delete `~/.claude/watchdog/state.json` (or set `rung: "resolved"`). The very next tick reads the missing/resolved state in step 1 and **stops without rescheduling**, so the self-perpetuating loop ends on its own — there is no separate `/loop` to cancel. The user can also just say "stop the watchdog".

## Notes / guardrails

- Arming auto-starts the loop via `ScheduleWakeup` — the user never has to run `/loop` manually. The loop sustains itself because every non-terminal tick reschedules.
- **Division of labor:** the MAIN agent only spawns the tick agent + reschedules (owns `ScheduleWakeup`, since only the main session can re-fire itself). The FANNED-OUT `general-purpose` agent does all evaluation + escalation + state writes and returns a verdict JSON. This keeps the main transcript from accumulating per-tick tool noise. The tick agent must NOT call `ScheduleWakeup`.
- Never call or alert on the **arming** invocation — arming only sets state and schedules the first tick.
- One rung per tick — no skipping straight to a call from `monitoring`.
- Respect the ~30s Claude-side tool timeout (Claude Desktop); keep `converse`/`listen` turns short.
- Idle detection reflects **this Mac only**. If the user is away from this machine but reachable, the phone alert/call is exactly the right fallback — which is the point.
- Don't spam chat on NOT-READY ticks; only post when state changes or on escalation.
