#!/usr/bin/env python3
"""MAGI listener: mic -> speech detection -> whisper (local) -> the tmux window "orchestrator".

Audio stays on this machine; only the transcribed text is typed into the orchestrator pane,
the same as typing it. Based on the bridge listener (github.com/pankajgarkoti/bridge).

  listen.py                 run; target from HUD_TARGET (exact name, e.g. =magi:=orchestrator)
  listen.py --once SECONDS  record from the mic for SECONDS, print the transcript, exit
  listen.py --level         print the mic level (0-100) ten times a second
"""
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # models ship with MAGI; never fetch at start-up
import subprocess
import sys
import time
from datetime import datetime

import numpy as np

TARGET = os.environ.get("HUD_TARGET", "")
MODEL = os.environ.get("HUD_MODEL", "base.en")
MODEL_DIR = os.environ.get("HUD_MODEL_DIR", "")
RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
LOCK = f"{RUN}/hud-speaking"
STATE = os.path.expanduser("~/.local/state/magi/agent")
LOG = f"{STATE}/heard.log"
RATE = 16000
FRAME = int(RATE * 0.03)
END_SILENCE_S = 1.4
MIN_SPEECH_S = 0.6
MAX_SPEECH_S = 30
JUNK = {"", "you", "thank you", "thanks", "bye", "okay", "ok", "hmm", "mm", "uh", "um"}
NOTIFY = os.environ.get("MAGI_LISTEN_NOTIFY", "1") == "1"


def log(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(STATE, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def load_model():
    from faster_whisper import WhisperModel
    path = os.path.join(MODEL_DIR, f"whisper-{MODEL}") if MODEL_DIR else ""
    return WhisperModel(path if path and os.path.isdir(path) else MODEL,
                        device="cpu", compute_type="int8", cpu_threads=4)


def transcribe(model, pcm):
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = model.transcribe(
        audio, language="en", beam_size=2, vad_filter=False, condition_on_previous_text=False,
        no_speech_threshold=0.5, log_prob_threshold=-1.0,
    )
    kept = [s.text.strip() for s in segments if s.no_speech_prob < 0.5 and s.avg_logprob > -1.0]
    return " ".join(kept).strip()


def laptop_mic():
    """Default mic: the built-in (ALSA) one, so a Bluetooth headset can stay in A2DP."""
    out = subprocess.run(["pactl", "list", "sources", "short"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) > 1 and cols[1].startswith("alsa_input"):
            return cols[1]
    return subprocess.check_output(["pactl", "get-default-source"], text=True).strip()


SOURCE = os.environ.get("HUD_SOURCE") or laptop_mic()


def mic():
    return subprocess.Popen(
        ["parec", f"--device={SOURCE}", "--format=s16le", f"--rate={RATE}", "--channels=1", "--raw",
         "--latency-msec=60"], stdout=subprocess.PIPE)


def rms_of(chunk):
    return float(np.sqrt(np.mean(np.frombuffer(chunk, dtype=np.int16).astype(np.float32) ** 2)))


def fail(msg):
    log(f"ERROR: {msg}. Dictation stopped.")
    sys.exit(2)


def resolve_target():
    """Pane id for TARGET, or stop. Never falls back to another pane."""
    if ":=" not in TARGET:
        fail(f"HUD_TARGET {TARGET!r} must be an exact name like =magi:=orchestrator")
    found = subprocess.run(["tmux", "list-panes", "-t", TARGET, "-F", "#{pane_id}"], capture_output=True, text=True)
    panes = found.stdout.split()
    if found.returncode != 0:
        fail(f"tmux target {TARGET!r} not found or not unique: {found.stderr.strip()}")
    if len(panes) != 1:
        fail(f"tmux target {TARGET!r} has {len(panes)} panes, expected 1")
    return panes[0]


def toast(text):
    if NOTIFY:
        subprocess.run(["notify-send", "-a", "MAGI", "-h", "string:x-canonical-private-synchronous:magi-voice",
                        "-h", "string:category:magi.voice", "-t", "4000", "VOICE", text],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def deliver(text):
    pane = resolve_target()
    try:
        subprocess.run(["tmux", "send-keys", "-t", pane, "-l", f"[voice] {text}"], check=True)
        time.sleep(0.4)
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=True)
    except subprocess.CalledProcessError as e:
        fail(f"could not send to {pane} ({TARGET!r}): {e}")
    toast(text)


def once(seconds):
    model = load_model()
    proc = mic()
    pcm = proc.stdout.read(int(RATE * seconds) * 2)
    proc.terminate()
    print(transcribe(model, pcm))


def level():
    proc = mic()
    while True:
        chunk = proc.stdout.read(int(RATE * 0.1) * 2)
        if not chunk:
            return
        print(min(100, int(rms_of(chunk) / 40)), flush=True)


def main():
    pane = resolve_target()
    log(f"loading {MODEL}")
    try:
        model = load_model()
    except Exception as e:
        fail(f"could not load {MODEL} ({e})")
    proc = mic()
    log(f"listening on {SOURCE}, sending to {TARGET} ({pane})")
    floor = 300.0
    buf = bytearray()
    pre = []
    in_speech = False
    voiced = silent = start_run = 0
    while True:
        chunk = proc.stdout.read(FRAME * 2)
        if not chunk:
            log("mic stream ended")
            sys.exit(1)
        if os.path.exists(LOCK):  # don't hear our own voice
            buf.clear(); pre.clear()
            in_speech = False
            voiced = silent = start_run = 0
            continue
        rms = rms_of(chunk)
        loud = rms > max(floor * 2.0, 220)
        if not in_speech:
            floor = floor * 0.995 + min(rms, floor * 2) * 0.005
            pre = (pre + [chunk])[-15:]
            start_run = start_run + 1 if loud else 0
            if start_run >= 5:
                in_speech = True
                buf = bytearray(b"".join(pre))
                voiced = start_run
                silent = 0
            continue
        buf += chunk
        if loud:
            voiced += 1
            silent = 0
        else:
            silent += 1
        secs = len(buf) / 2 / RATE
        if silent * 0.03 >= END_SILENCE_S or secs >= MAX_SPEECH_S:
            in_speech = False
            start_run = 0
            pcm = bytes(buf)
            buf.clear(); pre.clear()
            if voiced * 0.03 < MIN_SPEECH_S:
                continue
            text = transcribe(model, pcm)
            if text.lower().strip(" .,!?") in JUNK or len(text.split()) < 2:
                log(f"ignored: {text!r}")
                continue
            log(f"heard: {text}")
            deliver(text)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        once(float(sys.argv[2]) if len(sys.argv) > 2 else 4)
    elif len(sys.argv) > 1 and sys.argv[1] == "--level":
        level()
    else:
        main()
