# The mAGI event bus

Author: Pankaj Garkoti. First published 16 Nov 2026. Covers the event bus as shipped in mAGI
0.2.0 (15 Nov 2026) and the guard added on the `bus-guard` branch (16 Nov 2026).

## Problem

In mAGI the main interface is one orchestrator agent (Claude Code, opencode, Codex or another
terminal agent) running in a tmux pane. It starts other agents and waits on things: a worker
finishing, a build page changing, a message arriving in a browser tab, a timer.

A terminal agent only acts when text arrives in its input. The usual ways to make it notice
something are polling (a timer or a sleep loop inside the session) or interrupting it. Polling
spends tokens and fills the context with "nothing new". Interrupting lands text in the middle of
a turn, or on top of something the user is typing.

## Design

1. **One append-only log.** `orch-bus emit SOURCE "text"` appends one JSON line
   (`ts`, `at`, `source`, `text`, optional dedupe `key`) to
   `~/.local/state/orchestrator/events.jsonl`. No broker, no port. Anything that can run a
   command can publish: workers, shell scripts, systemd timers, browser probes.
2. **Idle-gated delivery.** `orch-bus dispatch` (the `orch-bus.service` user unit) watches the
   log's size. When there are undelivered events it waits 3 s so a burst coalesces, then types
   them into the orchestrator's pane as one `[event bus] …` line, but only if:
   - the agent's own state reports idle (for Claude Code: `~/.claude/sessions/<pid>.json`;
     for opencode its local server API; for Codex its session rollout file), and
   - the input box is empty, read from the pane per agent, so a half-typed message is never
     overwritten.

   If either check fails the events stay queued and the read offset doesn't move. Delivery is
   `tmux send-keys -l` followed by a separate Enter.
3. **Typed sources and a pull mode.** Every event carries its source (`agent build-2`,
   `page-title`, `orch-doctor`, …). With `BUS_DELIVERY=off` nothing is typed and the agent reads
   with `orch-bus unread`, which keeps its own offset.
4. **Worker events on the same path.** `magi-agent spawn` watches each worker it starts and
   delivers "finished", "waiting at a prompt" and "exited" through the same idle gate, then logs
   them on the bus marked `delivered`.
5. **Probes from the browser.** `browser-bus` attaches to the agent browser over CDP
   (127.0.0.1:9222) and runs small probe scripts inside matching tabs, in an isolated world. A
   probe declares `@match`, the `@source` names it may emit under, and a purpose, and calls
   `emit({text, key})`. The page a person is already signed in to becomes an event source
   without an API or scraping from the agent's side. Example:
   [`page-title.js`](../agent/browser-bus/examples/page-title.js) emits when a local dev
   server's page title changes.
6. **A ledger.** `orch` keeps agents, open threads, watchers, decisions and deliverables in
   SQLite so the state survives reboots and context compaction.

## Guard and breaker (bus-guard branch, 16 Nov 2026)

Once probes relay messages written by other people, bus text is untrusted input to an LLM.
The guard gives every event a trust level (owner, internal, external), normalises the text
(control characters, zero-width and bidi overrides, homoglyph folding for detection, length
cap), scores it for injection patterns (role tags, "ignore previous", fake approvals, encoded
payloads, fake `[event bus]` frames), and presents external text only inside a fixed frame
marked as data, not instructions. High-risk events are delivered as a one-line summary and
kept in full in a quarantine file. Only allow-listed probe sources can emit external source
names. A circuit breaker stops all typing after repeated high-risk events until the owner runs
`orch-bus resume`. See PR #1.

## Dates

From this repository's git history (IST, +0530):

| Date | What | Commit |
|---|---|---|
| 11 Nov 2026 04:15 | Workers report finished / asking / exited to the orchestrator, idle-gated | `c64be03` |
| 15 Nov 2026 20:19 | Event bus, browser-bus probes and agent browser ship as default components | `aa752e9` |
| 15 Nov 2026 21:28 | Release 0.2.0 (ISO) | tag `v0.2.0` |
| 16 Nov 2026 06:40 | Prompt-injection guard and circuit breaker | `890205c` |

The bus ran on the author's own machine from 10 Nov 2026 before it went into mAGI. Release
write-up: <https://pankajgarkoti.github.io/blog-magi-0-2-event-bus>.

This file has an OpenTimestamps proof next to it (`event-bus.md.ots`). Check it with
`ots verify docs/event-bus.md.ots`.

## Related work

Others have built pieces of this. Related work found before this write-up:

- tmux orchestrators that message agents with `send-keys` and detect a ready prompt by reading
  the pane, e.g. [primeline-ai/claude-tmux-orchestration](https://github.com/primeline-ai/claude-tmux-orchestration),
  [twaldin/tmux-orchestrator](https://github.com/twaldin/tmux-orchestrator),
  [dbmcco/claude-tmux-orchestration](https://github.com/dbmcco/claude-tmux-orchestration).
- Hook-based inboxes that inject queued messages through Claude Code hooks, e.g.
  [ka2n/agentqueue](https://github.com/ka2n/agentqueue).
- [Waking idle agents with Claude Code plugin monitors](https://nikiforovall.blog/ai/2026/08/21/claude-code-plugin-monitors.html) (Aug 2026).
- [Typing into a busy agent: prompt readiness, type-ahead and idle-gated commands](https://zylos.ai/research/2026-10-05-idle-gated-commands-interactive-agents/) (Oct 2026).

What mAGI puts together: one append-only log any process can write to, delivery gated on both
the agent's own idle state and an empty input box across several agent CLIs, probes running in
a signed-in browser as event sources, and a trust/quarantine layer on the delivery path.
