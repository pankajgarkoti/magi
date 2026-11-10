# Launch clip

- `magi-launch-16x9.mp4`: 1920×1080, 25 s
- `magi-launch-9x16.mp4`: 1080×1920, 25 s, the same take cut for phones
- `poster-16x9.png`: the last frame

One take, recorded in the MAGI distro test VM (installed from the ISO, display at 2×). The
orchestrator is opencode 1.19.5, installed by `magi-agent install`, with opencode's free
GLM-5.3 Flash model and no account. The orchestrator and the worker are real agents. Nothing
in the clip is scripted output.

What happened, at real time:

| real time | |
|---|---|
| 0:03–0:11 | the request is typed into the console (`SUPER+\``) |
| 0:58 | the orchestrator writes a brief and runs `magi-agent spawn tempconv ~/code/tempconv …` |
| 1:00–2:08 | the worker (tmux window 3) writes `tempconv.py` and `test_tempconv.py` and runs them |
| 2:08–4:58 | waiting: the orchestrator's watcher script checked with pytest, which isn't installed, and gave up (cut) |
| 4:59 | follow-up: "The agent is done. Open it now." |
| 5:14 | a terminal with the test run (18 tests, OK) and vi open on workspace 1 |
| 6:08 | vi showed a new, empty `tempconv.py` (opened outside the project folder). Correction typed |
| 6:48 | the console is hidden: tests and code on the desktop |

The clip is sped up from 7 min 11 s to 25 s. The 2:08–4:58 wait and the orchestrator's report
between 5:16 and 6:07 are cut. Both follow-up messages are shown. The real-time clock in the corner is
taken from the frame timestamps.

Recording: `grim` in the VM, about 18 frames per second while things changed and 2 per second
while waiting. Keystrokes were sent to the VM as key presses over QMP.

Setup that differs from a fresh install: the VM ran this branch's two agent-layer fixes (the
opencode adapter starts fresh sessions without `--session`; ORCHESTRATOR.md says to open windows
with a `workspace` rule), copied over the ISO's files. Also, opencode's `external_directory` permission allows
`~/code/**` and `/tmp/**` (`~/.config/opencode/opencode.json`), so the orchestrator could
create the project folder without a prompt. The idle lock and the mouse cursor were turned off.
