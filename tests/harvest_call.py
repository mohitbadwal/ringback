#!/usr/bin/env python3
"""ONE purposeful call: (1) verify the capture/turn-logic fix LIVE, (2) harvest REAL
speakerphone echo audio so the barge-in AEC can be developed and tuned OFFLINE afterwards
(synthetic echo can't reproduce real nonlinear speaker echo — proven in tests/test_aec.py).

Saves, under tests/fixtures/real/:
  capture_heard.txt              - what listen() captured live (capture-fix confirmation)
  echo_near.wav / echo_far.wav   - near-end while WE speak + the TTS we played (echo-only)
  barge_near.wav / barge_far.wav - near-end while the USER talks over us + the TTS
  meta.json                      - pre-roll offsets + sample rate for offline AEC alignment

Run:  ./tests/run_harvest.sh      (sources voice.env, sets the pjsua2 env)
After this, NO more calls — tune AEC against these recordings with tests/tune_aec.py.
"""
import json
import os
import shutil
import time

import pjsua2 as pj
import voice_agent as va

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures", "real")
PRE_ROLL = 0.5      # record this long before we start the TTS, so offline can align far->near


def _record_while_speaking(s, phrase, near_path, far_path, tail=1.0):
    """Play `phrase` to the call while recording the near-end. Saves the near-end recording
    and the exact TTS we played (the far-end reference). Returns nothing; files are written."""
    far_wav = va._tts_to_wav(phrase)
    shutil.copy(far_wav, far_path)
    dur = va._wav_duration(far_wav)
    rec = pj.AudioMediaRecorder()
    rec.createRecorder(near_path)
    s.aud.startTransmit(rec)          # near-end recording starts now (t=0)
    time.sleep(PRE_ROLL)              # pre-roll, then start the TTS at t=PRE_ROLL
    player = pj.AudioMediaPlayer()
    player.createPlayer(far_wav, pj.PJMEDIA_FILE_NO_LOOP)
    player.startTransmit(s.aud)
    end = time.time() + dur + tail
    while time.time() < end and not s.disconnected:
        time.sleep(0.05)
    try:
        player.stopTransmit(s.aud)
        s.aud.stopTransmit(rec)
    except Exception:
        pass
    del player, rec
    va._rm(far_wav)


def main():
    os.makedirs(OUT, exist_ok=True)
    s = va.CallSession()
    s.start_lib()
    print("placing call...", flush=True)
    if not s.place_call():
        print("RESULT: [NO ANSWER]", flush=True)
        s.shutdown()
        return 2
    print("connected. noise_floor=%.0f" % s.noise_floor, flush=True)
    meta = {"sample_rate": 16000, "pre_roll_sec": PRE_ROLL}

    # (1) CAPTURE VERIFICATION — the real win, confirmed live.
    s.speak("Hi, it's your assistant. This is a two minute test to finish the voice fixes. "
            "First, let me check I can hear you properly. After I stop talking, please say a "
            "full sentence about anything, then pause.")
    heard = s.listen()
    print("CAPTURE heard: %r" % heard, flush=True)
    with open(os.path.join(OUT, "capture_heard.txt"), "w") as f:
        f.write(heard or "")
    if heard:
        s.speak("Got it. I heard: " + heard)
    else:
        s.speak("I did not catch that, but that is okay, I have what I need to keep testing.")

    # (2) ECHO HARVEST — user silent, on speaker: records our echo only.
    s.speak("Now please put me on speakerphone, and then stay completely quiet for about ten "
            "seconds while I talk, so I can record my own echo. Going quiet now, you stay quiet.")
    time.sleep(0.4)
    _record_while_speaking(
        s,
        "I am now speaking a long, steady sentence so that my voice plays out of your speaker "
        "and echoes back into the microphone, which is exactly the echo I need to measure and "
        "cancel. I will keep talking for several seconds. One. Two. Three. Four. Five. Six.",
        os.path.join(OUT, "echo_near.wav"), os.path.join(OUT, "echo_far.wav"))
    print("echo harvest done", flush=True)

    # (3) BARGE HARVEST — user talks over us, on speaker: echo + user voice together.
    s.speak("Great. Last part. Keep me on speaker. This time, please TALK OVER me — interrupt "
            "me, say whatever you like, the moment I start counting. Here I go.")
    time.sleep(0.3)
    _record_while_speaking(
        s,
        "Okay, I am counting now, please interrupt me any time. One. Two. Three. Four. Five. "
        "Six. Seven. Eight. Nine. Ten. Eleven. Twelve. Thirteen. Fourteen. Fifteen.",
        os.path.join(OUT, "barge_near.wav"), os.path.join(OUT, "barge_far.wav"))
    print("barge harvest done", flush=True)

    with open(os.path.join(OUT, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    s.speak("Perfect, that is everything I need. Thank you. I will take it from here offline, "
            "no more test calls. Talk soon.")
    time.sleep(0.8)
    s.hangup()
    s.shutdown()
    print("RESULT: HARVEST COMPLETE ->", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
