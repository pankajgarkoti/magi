# magi-reflex — the muscle-memory layer

Replayable browser flows with zero model calls: the senses (browser-bus) watch pages, this is
the muscle that acts on them. A REFLEX is a small JSON document — a flow recorded once and
replayed forever. Scripts drive semantic CDP actions, not synthetic mouse physics.

## Store

Reflexes live in the live SQLite store `~/.local/state/magi/reflexes.db` (WAL, `busy_timeout=10s`,
shared across tabs and agent instances). The schema is two tables:

- `reflexes` — keyed `(domain, name)`. Status lifecycle: `candidate` (new, never replayed
  successfully), `active` (proven by >= 1 successful replay), `stale` (network signature drifted;
  replay refuses and lookup stops offering it until `seal`), `broken` (demoted after consecutive
  failures; never offered to lookup). A regenerated script for an active reflex waits in
  `candidate` as a next-version doc and never clobbers the serving one; the next successful replay
  promotes it. `owner_registered` arms a reflex outside the harmless-surface allowlist.
  `lease_until`/`lease_by` provide a learn-exclusive lease.
- `runs` — bounded audit table: one row per phase per replay plus a `<run>` summary row, pruned to
  the last 100 rows per reflex name. Each row records `zero_model_calls`.

Traces are written as JSONL to `~/.local/state/magi/reflexes/traces/`.

## Reflex document

```json
{ "domain": "127.0.0.1", "name": "local-fill-form", "entry_url": "http://127.0.0.1:8765/",
  "inputs": { "name": { "kind": "string", "required": true } },
  "phases": [
    { "name": "fill",
      "target_assert": "await h.eval('!!document.querySelector(\"#name\")')",
      "reflex_script": "…plain JS…", "verify_script": "…plain JS…",
      "steady_rule": { "type": "dom_stable", "quiet_ms": 700, "timeout_ms": 8000 },
      "verify_poll": { "interval_ms": 500, "timeout_ms": 15000 },
      "timeout_ms": 90000 } ] }
```

A phase is one semantic step: a pre-action `target_assert` proves the exact intended target before
any action, a `reflex_script` does it, a `verify_script` proves it, and a `steady_rule` decides
when the page has settled. Scripts run over CDP against the agent Chrome (127.0.0.1:9222) in an
isolated world, with `ARGS` and helpers `h.click`, `h.clickAt`, `h.focus`, `h.text`, `h.key`,
`h.eval`, `h.files`, `h.sleep`, `h.now`. Scripts return `{ok: true|false, why?}`.

## Safety gates (incident law, 25 Nov)

- **Harmless-surface allowlist.** Before a run opens a tab, the reflex domain is checked against
  `127.0.0.1, localhost, example.com, httpbin.org, the-internet.herokuapp.com`. Outside the
  allowlist with `owner_registered=0`, the run is REFUSED with one bus line:
  `magi-reflex <name> REFUSED: domain <d> not owner-registered`. With `owner_registered=1`, the
  owner has explicitly armed it and the exact entry URL is still required.
- **Pre-action target assertion.** Every phase must carry `target_assert`; the runner refuses a
  reflex missing it. The assertion runs before the phase types or clicks anything.
- **Entry-domain lock.** After navigation and during each phase, the runner aborts if the tab's
  origin leaves the reflex domain. Reflexes never act on real messaging/social surfaces.

## The run loop

`magi-reflex run NAME --args '{…}'` opens its own tab (never touches another), navigates
`entry_url`, and per phase enforces the entry-domain lock, runs target_assert → reflex script →
steady → verify, then closes its tab on every exit path. Any failure stops the run and emits one
bus line, `orch-bus emit magi-reflex "magi-reflex <name> FAILED at <phase>: <why>"`; success emits
one line with elapsed ms. The store records per-phase and run audit rows with `zero_model_calls=1`.

## Drift detection (v2a)

A phase may declare `net_filter`: a positive list of URL substrings or globs for the API calls
that actually matter (`Document`, `XHR`, and `Fetch` requests only). While the phase runs, the
runner records those requests from the action through steady state, drops the query string, and
normalizes opaque path segments (numbers, UUIDs, long hex/base64-ish ids) to `:id`. The ordered
`METHOD /path` lines are hashed (`sha256`); `net_filter: []` means "this phase should make no
matching call".

`net_signature` stays `null` until `magi-reflex seal NAME` captures it from a verified replay.
Once sealed, every replay compares the observed signature before verify is trusted. A mismatch
fails the phase as `STALE` and emits one bus line:

```
magi-reflex <name> STALE at <phase>: net signature drift: expected …
```

The reflex becomes `stale`: `run` refuses it and `suggest` no longer offers it. `magi-reflex seal
NAME` runs the flow again in capture mode (verify still gates it), writes the fresh signatures,
and returns the reflex to `active`. This is signature re-learning only — an agentic re-learn of a
changed script remains a later slice. Filters must stay narrow: an unfiltered hash of a chatty
app's traffic is noise, not a signature.

## Steady state

The default steady rule is DOM-stable: wait for no DOM mutation for `quiet_ms` before verify runs.
The dogma filter (`magi-reflex dogma`) measured candidates on real pages; DOM-stable `quiet_ms=700`
was safe on measured loads, while network quiesce measured false-settle on chatty SPAs and fixed
delays fired before content existed. Verify is the real authority; steady timeout proceeds to
verify.

## Practice reflexes (harmless surfaces only)

These are hand-authored practice reflexes, not learned from real accounts:

- `local-fill-form` (`127.0.0.1`) — serves a tiny local test page from
  `agent/lib/reflex/testpage/` via `python3 -m http.server` on 127.0.0.1; fills name/email/message,
  uploads one small file, submits, and verifies echoed values.
- `httpbin-submit` (`httpbin.org`) — fills `https://httpbin.org/forms/post`, submits, and verifies
  the echoed JSON contains our values.
- `theinternet-login` (`the-internet.herokuapp.com`) — logs in with the public demo creds printed on
  the page (`tomsmith` / `SuperSecretPassword!`), submits, and verifies the success flash.

## Commands

```
magi-reflex ls                        registry: name, purpose, lifecycle state
magi-reflex show NAME [--json]        full doc (phases, scripts, inputs, runs)
magi-reflex suggest --url URL         JIT discovery for a page's domain (anti-bias header)
magi-reflex install FILE.json         install/refresh a reflex doc (candidate lifecycle)
magi-reflex run NAME [--args JSON] [--trace] [--version active|candidate]
magi-reflex seal NAME [--args JSON]   capture/re-learn per-phase net signatures
magi-reflex lease NAME --by AGENT [--minutes N] [--release]
magi-reflex dogma ...                 re-measure steady-state detectors
```

`magi-reflex suggest --url URL` prints the anti-bias header verbatim:

> Optional fast-paths for pages like this - using one is optional; hand-driving is always fine; if
> you do something new and repeatable, consider magi-reflex learn.

## Zero model calls, provably

Replay is agent-assisted only at AUTHOR time. The run path is one process that opens one websocket
to 127.0.0.1:9222, evaluates stored scripts, reads local files, and emits one orch-bus line.
`--trace` logs every CDP frame sent or received — the complete activity record of the process.

Never learns or runs flows that type credentials, OTPs, or solve challenges — such phases end with
an ASK OWNER handoff. Drift detection + signature re-seal are implemented (v2a above). The
agentic learn listener/script re-learn and arming on bus events remain later slices per
`notes/orchestrator/magi-reflex-plan.md`.
