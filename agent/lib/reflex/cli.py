"""magi-reflex CLI: ls | show | suggest | install | run | lease | dogma.

The muscle-memory layer (spec: notes/orchestrator/briefs/2026-11-24/magi-reflex-spec.md).
A REFLEX is a replayable browser flow: {domain, name, entry_url, phases[]}; each
phase = reflex_script (act) + verify_script (check) + steady_rule (when the page
has settled). Replay is CDP-only, in its own tab, provably zero model calls
(--trace logs every protocol message).

  magi-reflex ls                        registry: name, purpose line, lifecycle state
  magi-reflex show NAME [--json]       full docs (phases, scripts, inputs, runs)
  magi-reflex suggest --url URL        JIT discovery for a page's domain
  magi-reflex install FILE.json        author/learn entry point (candidate lifecycle)
  magi-reflex run NAME [--args JSON] [--trace] [--version active|candidate]
  magi-reflex lease NAME --by AGENT [--minutes N] [--release]
  magi-reflex dogma [--window S]      re-measure the steady-state detectors

Store: SQLite WAL at ~/.local/state/magi/reflexes.db (busy_timeout 10s, shared
across tabs and agent instances). Lifecycle: candidate -> active (after 1
successful replay) -> broken (after consecutive failures); a regenerated script
waits as candidate and never clobbers the active one.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # agent/lib on the path
from reflex import store  # noqa: E402
from reflex import runner  # noqa: E402

SUGGEST_HEADER = ("Optional fast-paths for pages like this - using one is optional; "
                  "hand-driving is always fine; if you do something new and repeatable, "
                  "consider magi-reflex learn.")


def die(msg, code=2):
    print(f"magi-reflex: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve(conn, key):
    """NAME or DOMAIN/NAME -> row (bare NAME must be unique)."""
    if "/" in key:
        domain, name = key.split("/", 1)
        row = store.get(conn, domain, name)
        if row is None:
            die(f"no reflex {key}")
        return row
    rows = store.find_by_name(conn, key)
    if not rows:
        die(f"no reflex named {key} (installed? magi-reflex ls)")
    if len(rows) > 1:
        die(f"ambiguous name {key}: " + ", ".join(f"{r['domain']}/{r['name']}" for r in rows))
    return rows[0]


def validate_args(inputs, args):
    """Fail fast, before any tab opens: required keys present, file paths real."""
    if not isinstance(args, dict):
        die("--args must be a JSON object")
    for key, spec in (inputs or {}).items():
        if spec.get("required") and key not in args:
            die(f"--args missing required '{key}'")
        if spec.get("kind") == "file_paths":
            files = args.get(key) or []
            if not isinstance(files, list) or spec.get("min", 1) and len(files) < spec.get("min", 1):
                die(f"--args '{key}' needs at least {spec.get('min', 1)} file path(s)")
            for p in files:
                if not Path(p).is_absolute() or not Path(p).is_file():
                    die(f"file not found: {p}")
    return args


def cmd_ls(conn, _):
    rows = conn.execute("SELECT * FROM reflexes ORDER BY domain, name").fetchall()
    if not rows:
        print(f"(no reflexes in {store.DB_PATH}; author one and `magi-reflex install FILE.json`)")
        return 0
    for r in rows:
        state = r["status"]
        if r["candidate"]:
            state += " (+candidate waiting)"
        lease = ""
        if r["lease_until"] and r["lease_until"] > time.time():
            lease = f" leased:{r['lease_by']}"
        print(f"{r['domain']}/{r['name']:<22} {state:<24} ok:{r['successes']} fail:{r['failures']}"
              f" streak:{r['consecutive_failures']}{lease}  {r['description']}")
    return 0


def cmd_show(conn, a):
    row = resolve(conn, a.reflex)
    doc = store.row_doc(row, use_candidate=a.candidate)
    print(f"{row['domain']}/{row['name']} — {doc['description']}")
    print(f"  status: {row['status']}"
          + (f" (candidate version shown below; serving version differs)" if a.candidate and row["candidate"] else "")
          + (f" | a candidate next-version is waiting (show --candidate)" if row["candidate"] and not a.candidate else ""))
    print(f"  lifecycle: {row['successes']} ok / {row['failures']} fail, consecutive {row['consecutive_failures']},"
          f" updated {row['updated_at']}, owner_registered={bool(row['owner_registered'])}")
    print(f"  when to use: {doc['when_to_use'] or '-'}")
    print(f"  entry: {doc['entry_url']}   pause_after: {doc.get('pause_after', 0)}ms   tags: {doc.get('tags') or '-'}")
    print(f"  inputs: {json.dumps(doc['inputs'])}")
    for i, ph in enumerate(doc["phases"], 1):
        print(f"  phase {i}: {ph['name']}   steady: {json.dumps(ph.get('steady_rule'))}"
              f"   verify_poll: {json.dumps(ph.get('verify_poll'))}"
              f"   net_signature: {json.dumps(ph.get('net_signature'))}")
        print(f"    reflex: {ph['reflex_script'][:400]}{'…' if len(ph['reflex_script']) > 400 else ''}")
        print(f"    verify: {ph['verify_script'][:400]}{'…' if len(ph['verify_script']) > 400 else ''}")
    if a.json:
        print(json.dumps(doc, indent=2))
    runs = store.recent_runs(conn, row["name"], 10)
    if runs:
        print("  recent runs (bounded audit, last 100 per name kept):")
        for r in runs:
            print(f"    {r['ts']} {r['phase']:<18} {'ok' if r['ok'] else 'FAIL'} {r['elapsed_ms']}ms"
                  f" zero_model_calls={bool(r['zero_model_calls'])}")
    return 0


def cmd_suggest(conn, a):
    print(SUGGEST_HEADER)
    rows = store.suggest(conn, a.url)
    if not rows:
        print("  (none for this domain yet)")
        return 0
    for r in rows:
        doc = store.row_doc(r)
        inputs = ", ".join(f"{k}{'*' if v.get('required') else ''}" for k, v in (doc.get("inputs") or {}).items())
        print(f"  magi-reflex run {r['name']} --args '{{{inputs}}}'"
              f"{'  [candidate, not proven yet]' if r['status'] == 'candidate' else ''}")
        print(f"    {doc['description']}")
        if doc.get("when_to_use"):
            print(f"    when to use: {doc['when_to_use']}")
    return 0


def cmd_install(conn, a):
    try:
        doc = json.loads(Path(a.file).read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"cannot read {a.file}: {e}")
    print(store.install(conn, doc))
    return 0


def cmd_run(conn, a):
    row = resolve(conn, a.reflex)
    if row["status"] == "broken":
        die(f"{row['domain']}/{row['name']} is BROKEN (consecutive failures) — re-learn it, don't replay it", 1)
    use_candidate = row["candidate"] is not None
    if a.version == "active" and use_candidate:
        use_candidate = False
    elif a.version == "candidate" and not use_candidate:
        die("no candidate version waiting for this reflex")
    doc = store.row_doc(row, use_candidate=use_candidate)
    doc["_served_candidate"] = use_candidate
    doc["owner_registered"] = bool(row["owner_registered"])
    args = validate_args(doc.get("inputs"), json.loads(a.args) if a.args else {})
    res = runner.run_reflex(doc, args, verbose=not a.quiet, trace_echo=a.trace)
    return 0 if res["ok"] else 1


def cmd_lease(conn, a):
    row = resolve(conn, a.reflex)
    if a.release:
        store.release_lease(conn, row["domain"], row["name"], a.by)
        print(f"released {row['domain']}/{row['name']}")
        return 0
    try:
        r = store.acquire_lease(conn, row["domain"], row["name"], a.by, a.minutes)
    except store.StoreError as e:
        die(str(e))
    if r["ok"]:
        print(f"lease acquired by {a.by} for {a.minutes} min (until {time.strftime('%H:%M:%S', time.localtime(r['until']))})")
    else:
        print(f"lease HELD by {r['lease_by']} until {time.strftime('%H:%M:%S', time.localtime(r['lease_until'] or 0))}")
        return 1
    return 0


def cmd_dogma(_conn, a):
    from reflex import dogma_filter
    sys.argv = ["dogma_filter", *a.rest]
    dogma_filter.main()
    return 0


def main():
    ap = argparse.ArgumentParser(prog="magi-reflex", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ls", help="registry: name, purpose, state")

    p = sub.add_parser("show", help="full docs for one reflex")
    p.add_argument("reflex")
    p.add_argument("--json", action="store_true", help="also print the full serving doc as JSON")
    p.add_argument("--candidate", action="store_true", help="show the waiting candidate version instead")

    p = sub.add_parser("suggest", help="reflexes applicable to a URL (JIT discovery)")
    p.add_argument("--url", required=True)

    p = sub.add_parser("install", help="install/refresh a reflex doc (enters as candidate)")
    p.add_argument("file")

    p = sub.add_parser("run", help="replay: own tab, zero model calls, one bus line")
    p.add_argument("reflex")
    p.add_argument("--args", help="JSON object with the reflex's inputs")
    p.add_argument("--trace", action="store_true", help="echo every CDP message to stdout too")
    p.add_argument("--version", choices=("serving", "active", "candidate"), default="serving")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("lease", help="learn-exclusive lease (two agents never re-learn one reflex)")
    p.add_argument("reflex")
    p.add_argument("--by", required=True, help="who is learning (agent name)")
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--release", action="store_true")

    p = sub.add_parser("dogma", help="re-measure steady-state detectors on real pages")
    p.add_argument("rest", nargs=argparse.REMAINDER)

    a = ap.parse_args()
    conn = store.connect()
    try:
        rc = {"ls": cmd_ls, "show": cmd_show, "suggest": cmd_suggest, "install": cmd_install,
              "run": cmd_run, "lease": cmd_lease, "dogma": cmd_dogma}[a.cmd](conn, a)
    finally:
        conn.close()
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
