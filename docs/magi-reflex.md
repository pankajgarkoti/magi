# magi-reflex — the muscle-memory layer

Replayable browser flows with zero model calls: the senses (browser-bus) watch pages, this is
the muscle that acts on them. A REFLEX is a small JSON document — a flow recorded once and
replayed forever. Built from the FlowPilot investigation (notes/tech/flowpilot-investigation.md)
with its human-event fidelity deliberately left out: scripts drive semantic CDP actions, not
synthetic mouse physics (the investigation itself calls that path a maintenance tar pit).

## Model

A reflex lives in the live store `~/.local/state/magi/reflexes/<name>.json` (the exact shape
below); the same document ships bundled with mAGI in `agent/reflexes/` and seeds the live store
on first use. A definition-only copy syncs to the notes repo (`orchestrator/reflexes/`) so
learned assets are versioned like briefs — never untracked — while `last_ok`/`fail_count` stay
machine-local and don't churn the notes.

```json
{ "name": "wa-send-docs", "description": "…", "entry_url": "https://…",
  "args_schema": { "docs": { "kind": "file_paths", "required": true, "min": 1 } },
  "phases": [
    { "name": "open-self-chat", "safe_to_retry": true,
      "reflex_script": "…plain JS…", "verify_script": "…plain JS…",
      "steady": { "type": "dom", "quiet_ms": 1000, "timeout_ms": 15000 },
      "last_ok": null, "fail_count": 0 } ] }
```

A phase is one semantic step on one page (FlowPilot's central lesson): a `reflex_script`
that does it, a `verify_script` that proves it, and a `steady` rule for when the page has
settled in between. Scripts run over CDP against the agent Chrome (127.0.0.1:9222) — the
same transport browser-bus uses; the connection, attach, isolated-world and binding patterns
are ported from `agent/lib/browser-bus.mjs`. Scripts run in an isolated world (the whole DOM,
none of the page's JS globals), with `$`, `$$`, `sleep`, `until` (which re-runs its probe
fresh on every attempt — stale element references are the 24 Nov failure mode), `args` from
`--args`, and `__refAct` for what isolated JS cannot do:

```js
await __refAct({do: "click", x, y})                 // a trusted mouse click
await __refAct({do: "type", text})                  // Input.insertText into the focused box
await __refAct({do: "key", key: "Enter"})           // a real key press
await __refAct({do: "setFiles", selector, files})   // local files onto an <input type=file>
```

## The run loop

`magi-reflex run NAME --args '{…}'` opens its own tab (never touches another), navigates
`entry_url`, and per phase runs reflex → steady → verify, then closes its tab on every exit
path. Any failure stops the run and emits exactly one bus line,
`orch-bus emit magi-reflex "magi-reflex <name> FAILED at <phase>: <why>"`; success emits one
line with the elapsed ms. The store's `last_ok`/`fail_count` update either way.

`safe_to_retry` (true only for phases before the first send) buys one full re-run from a
fresh tab: a second WhatsApp Web instance connecting can force-reload our tab mid-flow
(seen live, 25 Nov), and re-running phase 1 after such a reload is free — while re-running a
send phase could double-send, so it fails loudly instead.

## The dogma filter: which steady state?

The steady rule decides when verify runs, so it was measured before it was chosen
(`magi-reflex dogma`; harness in the same file). Three candidates after a navigation-like
action on three real pages — web.whatsapp.com, github.com, news.ycombinator.com (plus WA's
chat-open as an in-page action) — 3 trials each, every candidate computed offline from the
same event streams (every request timestamp, every mutation batch, the moment a post-action
marker first appeared). A config is *safe* on a trial when it would fire no earlier than the
marker (30 ms clock tolerance) and inside the 25 s horizon:

| config | safe (load, n=7) | capped | median latency | safe (WA chat-open, n=2) |
|---|---|---|---|---|
| net-quiesce 300–1000 ms | 4/7 | 3 | ~18 s | 2/2 but ~16.7 s to quiet |
| **dom-stable 400 ms** | 7/7 | 0 | 1.6 s | 0/2 (fires mid-render) |
| **dom-stable 700–1000 ms** | **7/7** | **0** | **1.9–2.2 s** | **2/2** |
| fixed 1500–5000 ms | 5/7 | 0 | 1.5–5 s | 0/2 (before content) |

The verdict: **DOM-stable, quiet_ms = 1000** — safe on every measured trial, ~2 s typical.
FlowPilot's network-sequence hint measured *worse* here: WhatsApp never goes network-quiet
after load (a steady request drumbeat — 3/7 trials capped at the horizon), and where it does
quiet it takes ~17 s. Fixed delays are disqualified outright: every value fast enough to be
useful fires before WhatsApp's content exists. `dom:400` is the interesting near miss —
after an in-page click it settles during the re-render and fires 188 ms *before* the effect;
the extra 600 ms of quiet buys the whole margin. Raw trials: `tests/agent/reflex/dogma-2026-11-25.json`;
an independent second harness (measuring web.whatsapp.com, github.com, en.wikipedia.org)
reached the same conclusion the same night, which is why the runner ships dom-stable without
fear of a single-measurement fluke.

## The first reflex: wa-send-docs

Open the Me (You) chat, attach N documents, caption, send, verify — hand-authored from a
live pass on 25 Nov with every fumble the orchestrator hit on 24 Nov baked in:

- **menu uid flicker → retry-snapshot**: menus and chat lists re-render; scripts re-query
  fresh on every attempt (`until` is built for it) and never hold an element across awaits.
  The attach menu also sometimes ignores its first click (live-measured, roughly 1-in-3):
  the whole clip → Document → input sequence retries up to 4 times, dismissing dead state by
  toggling the clip button — never `Escape`, which closes the whole conversation (measured
  the hard way: a blank `#main` that took three runs to attribute).
- **message sync wait → poll up to 3 min**: a just-sent message can take minutes to echo
  back into the web client; `verify-sent` polls for the bubbles (+ caption + every doc chip)
  with a 2-minute budget, then its Read ticks for another minute.
- **verify-bubbles-before-close**: the runner closes its tab only after every phase — the
  verify phase runs while the tab is open by construction.
- Attach uses no dialogs at all: clicking Document mints a hidden
  `<input type="file" multiple>`, and `setFiles` puts all N docs on it in one trusted call;
  the send button's own label (`Send N selected`) verifies the count before anything is sent.
- One honest finding: the self-chat renders no per-message tick icons for older messages
  (live dump, 25 Nov), so Read verification is best-effort and reported on the bus line via
  the trace rather than run-fatal; bubbles + caption + doc chips are the hard gate.

## Zero model calls, provably

Replay is agent-assisted only at AUTHOR time. The run path is one process that opens one
websocket to 127.0.0.1:9222, evaluates the stored scripts, reads local files, and emits one
orch-bus line. `--trace` logs every CDP frame sent or received — the complete activity record
of the process, and it contains nothing but CDP. The wa-send-docs acceptance trace is kept
with the test evidence; grep it for anything that isn't CDP and you will not find it.

## Commands

```
magi-reflex ls                        live store (+ bundled, seeds on first use)
magi-reflex show NAME                 one reflex definition
magi-reflex run NAME [--args JSON] [--trace]
magi-reflex dogma [-p wa,gh,hn] [-n N] [--save FILE]   re-measure the steady state
```

Never learns or runs flows that type credentials, OTPs, or solve challenges — such phases end
with an ASK OWNER handoff. Learn (recording a deliberate pass), drift detection
(net-signature + STALE), and arming on bus events are later slices per
notes/orchestrator/magi-reflex-plan.md.
