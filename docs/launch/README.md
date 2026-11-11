# Launch clip

- `magi-launch-16x9.mp4`: 1920×1080, 23 s
- `magi-launch-9x16.mp4`: 1080×1920, 23 s, the same take for phones: the whole desktop on top,
  the part that matters magnified below it
- `poster-16x9.png`: the last frame

One take, recorded in the MAGI distro test VM (installed from the ISO, 1920×1080 at scale 1.5).
The orchestrator and the worker are opencode 1.19.5, installed by `magi-agent install`, with
opencode's free GLM-5.3 Flash model and no account. Nothing in the clip is scripted output, and
nobody typed anything after the request.

The request, typed into the console (`SUPER+\``):

> New Python project in ~/code/tempconv with unit tests. Have an agent build it. When it is
> done, run the tests in a terminal and open the code in an editor, side by side.

What happened, at real time:

| real time | |
|---|---|
| 0:00–0:09 | the request is typed |
| 0:22 | the orchestrator writes a brief and runs `magi-agent spawn tempconv ~/code/tempconv …`, then ends its turn |
| 0:22–2:53 | the worker (tmux window 3) writes the package and the tests; the orchestrator waits |
| 2:54 | `spawn`'s watcher types `[magi] window tempconv (opencode) finished and is idle …` into the orchestrator |
| 2:55–4:45 | the orchestrator reads the worker's pane, runs the tests itself (48 passed) and checks the files |
| 4:45–4:50 | it opens a terminal running pytest and nano with `tempconv/core.py` on workspace 1, then checks them with a screenshot |
| 5:00–5:13 | it closes the worker's window and reports; it asks whether to install a graphical editor (none is installed; the question was not answered) |
| 6:10 | the console is hidden (`SUPER+\``): tests and code on the desktop |

The clip plays the whole take, from 6 min 17 s down to 23 s, at varying speed: the wait while
the worker builds is fastest, and the `[magi]` message (on screen for 1.3 s) plays at half
speed. Nothing is cut except 0.2 s of the console's hide animation. The real-time clock in the
corner comes from the frame timestamps.

Recording: `grim` in the VM, about 25 frames per second. Keystrokes were sent to the VM as key
presses over QMP.

## Choosing the model

The same request (an earlier wording: "open the tests in a terminal") was run once per model
without recording, with this branch's agent layer:

| free model | result |
|---|---|
| GLM-5.3 Flash | done without help: worker spawned, `[magi]` message acted on, windows opened |
| LongCat 2.5 | started `sudo dnf install gedit` unasked (ORCHESTRATOR.md now says to ask first) |
| Nemotron 3.5 Super | put the "side by side" view in a tmux window, not on the desktop |
| MiniMax M3.1 Flash | stopped on a permission prompt for a config folder it went looking for |
| DeepSeek V4.1 Flash, MiMo V2.6 Flash | not run: "Free usage limit reached" |

Take 4 was stopped before the agents did anything: my take script failed to restart the
orchestrator, which was still on MiniMax. Take 5 is the first take with GLM, and it is the
one shown.

## Setup that differs from a fresh install

- The VM ran this branch's agent layer, copied over the ISO's files: the opencode adapter
  starts fresh sessions without `--session`; `spawn` tells the orchestrator when a worker
  finishes, asks or exits; ORCHESTRATOR.md says to wait for that message, to open windows with
  a `workspace` rule, to open files by checked absolute paths, and to ask before installing.
- opencode's `external_directory` permission allows `~/code/**` and `/tmp/**`
  (`~/.config/opencode/opencode.json`), so the agents could work in the project folder without
  a prompt.
- The idle lock and the mouse cursor were turned off, and the tmux status line's right side was
  emptied.
