#!/usr/bin/env python3
"""Final LIVE confirmation of barge-in (the one thing only a real call can prove): we speak
a long line, you interrupt, and we should STOP and capture your words. Uses the latest
voice_agent (barge-in ON, during-TX-floor threshold). Coexists with the running MCP via
VOICE_RTP_PORT; SIP auth comes from voice.env.

Run:  VOICE_RTP_PORT=5000 ./tests/run_harvest.sh  -- no; use the barge launcher:
      ./tests/run_barge.sh
"""
import os
import sys

import voice_agent as va

LONG = ("Okay, here is the barge in test. I am going to keep talking for a while now, "
        "and at any moment you can just start speaking over me to interrupt. I will keep "
        "going — one, two, three, four, five, six, seven, eight, nine, ten — please cut in "
        "whenever you like and I should stop right away and tell you what I heard.")


def main():
    s = va.CallSession()
    s.start_lib()
    print("placing call...", flush=True)
    if not s.place_call():
        print("RESULT: [NO ANSWER]", flush=True)
        s.shutdown()
        return 2
    print("connected. noise_floor=%.0f half_duplex=%s" % (s.noise_floor, s.half_duplex), flush=True)

    s.speak("Hi, last quick test — barge in on me. When I start talking, just interrupt me "
            "by speaking over me, and I should stop and repeat what you said.")
    rounds = []
    for i in (1, 2):
        r = s.speak_interruptible(LONG, listen_after=True)
        if r.get("interrupted"):
            print("ROUND %d: BARGE DETECTED. spoken=%r user=%r" % (i, r["spoken"][:40], r["user"]), flush=True)
            rounds.append(("barge", r.get("user", "")))
            s.speak("Got it, you interrupted me and I heard: " + (r.get("user") or "something"))
        else:
            print("ROUND %d: spoke fully, no barge. user-after=%r" % (i, r.get("user")), flush=True)
            rounds.append(("nobarge", r.get("user", "")))
            s.speak("I finished that one without being interrupted. Let's try once more.")
        if s.disconnected:
            break

    s.speak("That is the barge in test done. Thanks, talk soon.")
    import time
    time.sleep(0.8)
    s.hangup()
    s.shutdown()
    barged = sum(1 for k, _ in rounds if k == "barge")
    print("RESULT: %d/%d rounds detected a barge-in" % (barged, len(rounds)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
