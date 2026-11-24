#!/usr/bin/env python3
"""Offline tests for magi-reflex (agent/lib/reflex.py) — no browser, no network.

Covers what the replay path depends on: the script wrapper (args + the
retry-snapshot helpers), the offline steady-state math behind the dogma filter,
the verify contract, store seeding/recording/definition sync, and the websocket
accept-key against the RFC 6455 example vector. The live acceptance
(wa-send-docs on the agent Chrome, --trace proving zero model calls) is
described in docs/magi-reflex.md.

  python3 tests/agent/reflex/test_offline.py
"""
import base64
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
LIB = HERE.parents[2] / "agent" / "lib"

fails = total = 0


def check(what, cond):
    global fails, total
    total += 1
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        fails += 1


# ---- wrap(): the prelude every script gets (load by path: a sibling package shadows the name) ----
sys.path.insert(0, str(LIB))
import importlib.util

_spec = importlib.util.spec_from_file_location("reflex_runner", LIB / "reflex.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

expr = R.wrap("return args.docs.length + ($('x') ? 1 : 0);", {"docs": ["/a.pdf"]})
check("wrap() injects args JSON", '"docs": ["/a.pdf"]' in expr)
check("wrap() keeps the script body", "args.docs.length" in expr)
check("wrap() is an awaited async IIFE", expr.startswith("(async () => {") and expr.rstrip().endswith("})()"))
for helper in ("until(", "sleep(", "const $ =", "const $$ ="):
    check(f"wrap() provides {helper.strip()}", helper in R.SCRIPT_PRELUDE)
helper_src = (LIB / "reflex.py").read_text()
check("the world helper wires __refAct to the __refActBind binding",
      "window.__refAct = (o) => new Promise" in helper_src and "__refActBind(JSON.stringify" in helper_src)
check("until() re-runs fn fresh each attempt (no element caching)",
      "const v = fn()" in R.SCRIPT_PRELUDE and "try { const v = fn()" in R.SCRIPT_PRELUDE)

# ---- judge(): the verify contract ----
check("judge(true) passes", R.judge(True) == (True, ""))
check("judge(false) fails", R.judge(False)[0] is False)
check("judge('') fails", R.judge("")[0] is False)
check("judge({ok:true}) passes", R.judge({"ok": True})[0] is True)
check("judge({ok:false,why}) carries why", R.judge({"ok": False, "why": "no bubbles"}) == (False, "no bubbles"))

# ---- _steady_at(): offline detector math (the dogma filter's core) ----
t0, reqs = 1000, [1100, 1200, 1500]
check("net quiesce fires at last request + N", R._steady_at("net", 300, t0, reqs) == 1800)
check("net quiesce ignores requests outside the quiet window", R._steady_at("net", 300, t0, [1300, 1310]) == 1610)
check("net quiesce with no events fires at t0 + N", R._steady_at("net", 300, t0, []) == 1300)
check("fixed fires at t0 + D whatever the page does", R._steady_at("fixed", 1500, t0, reqs) == 2500)

# ---- store: definition() sanitising, seeding, recording, notes sync (in a throwaway HOME) ----
tmp = tempfile.mkdtemp()
store = pathlib.Path(tmp) / "reflexes"
sync = pathlib.Path(tmp) / "notes" / "orchestrator" / "reflexes"
bundled = pathlib.Path(tmp) / "bundled" / "reflexes"
sync.mkdir(parents=True)
bundled.mkdir(parents=True)
(bundled / "t-reflex.json").write_text(json.dumps({
    "name": "t-reflex", "description": "d", "entry_url": "https://x/",
    "phases": [{"name": "p1", "reflex_script": "1", "verify_script": "1", "steady": {"type": "fixed", "ms": 10}}],
}))
code = f"""
import json, pathlib, sys, importlib.util
sys.path.insert(0, {str(LIB)!r})
_spec = importlib.util.spec_from_file_location("reflex_runner", {str(LIB / 'reflex.py')!r})
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)
R.BUNDLED = pathlib.Path({str(bundled)!r})
r = R.load_reflex("t-reflex")
assert r and r["name"] == "t-reflex", "seed failed"
assert (R.STORE / "t-reflex.json").exists(), "live store not seeded"
r["phases"][0]["_ok"] = True
R.record(r, ok=True)
live = json.loads((R.STORE / "t-reflex.json").read_text())
assert live["fail_count"] == 0 and live["last_ok"], "record(ok=True) failed"
sp = R.SYNC / "t-reflex.json"
assert sp.exists(), "notes sync missing"
d = json.loads(sp.read_text())
assert all(not k.startswith("_") for p in d["phases"] for k in p), "volatile _ok leaked into the definition"
assert all(k not in p for p in d["phases"] for k in ("last_ok", "fail_count")), "run counters leaked into the definition"
R.record(r, ok=False)
live = json.loads((R.STORE / "t-reflex.json").read_text())
assert live["fail_count"] == 1, "fail_count did not increment"
d2 = json.loads(sp.read_text())
assert d2 == d, "notes copy churned on a run that changed no definition"
print("store-ok")
"""
env = dict(os.environ, MAGI_REFLEX_STORE=str(store), MAGI_REFLEX_SYNC_DIR=str(sync))
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
if "store-ok" not in r.stdout:
    print("     stderr:", r.stderr.strip()[:400])
check("store: seed -> record -> sync, no churn on runs", "store-ok" in r.stdout)
shutil.rmtree(tmp, ignore_errors=True)

# ---- websocket handshake key: RFC 6455's own example vector ----
vec_key = "dGhlIHNhbXBsZSBub25jZQ=="
want = base64.b64encode(hashlib.sha1((vec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
check("ws accept-key matches the RFC 6455 example", want == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

# ---- trunc(): trace caps never hide a method ----
check("trunc caps long payloads", len(R.trunc({"a": "x" * 5000})) < 300)
check("trunc keeps short payloads whole", R.trunc({"a": 1}) == '{"a": 1}')

# ---- Slice C structural target guard (package: agent/lib/reflex/) ----
PKG = LIB / "reflex"
sys.path.insert(0, str(LIB))
from reflex import store as S  # noqa: E402
from reflex import runner as RR  # noqa: E402

for d in ("127.0.0.1", "localhost", "example.com", "httpbin.org", "the-internet.herokuapp.com", "sub.httpbin.org"):
    check(f"domain_allowed({d})", S.domain_allowed(d))
for d in ("web.whatsapp.com", "x.com", "twitter.com", "mail.google.com", "evil.com", ""):
    check(f"domain disallowed({d})", not S.domain_allowed(d))

try:
    S.check_domain_allowed("web.whatsapp.com", owner_registered=False)
    check("disallowed+owner_registered=0 raises", False)
except S.StoreError as e:
    check("disallowed+owner_registered=0 raises", "not owner-registered" in str(e))

try:
    S.check_domain_allowed("web.whatsapp.com", owner_registered=True)
    check("disallowed+owner_registered=1 allowed by guard", True)
except S.StoreError:
    check("disallowed+owner_registered=1 allowed by guard", False)

check("host_matches exact", RR.host_matches("example.com", "example.com"))
check("host_matches subdomain", RR.host_matches("a.example.com", "example.com"))
check("host_matches rejects other", not RR.host_matches("evil.com", "example.com"))
check("host_matches rejects suffix trick", not RR.host_matches("notexample.com", "example.com"))

# runner refusal path: must refuse before opening CDP (no browser needed)
refused = RR.run_reflex({
    "name": "bad-reflex", "domain": "web.whatsapp.com", "entry_url": "https://web.whatsapp.com/",
    "owner_registered": 0,
    "phases": [{"name": "p1", "target_assert": "true", "reflex_script": "return {ok:true}", "verify_script": "return {ok:true}"}],
}, {})
check("run_reflex refuses disallowed domain", refused["ok"] is False and refused.get("phase") == "(refusal)")
check("run_reflex refusal line exact", refused.get("line") == "magi-reflex bad-reflex REFUSED: domain web.whatsapp.com not owner-registered")

# owner_registered outside allowlist requires exact entry URL
refused2 = RR.run_reflex({
    "name": "armed-bad-url", "domain": "evil.com", "entry_url": "https://not-evil.com/path",
    "owner_registered": 1,
    "phases": [{"name": "p1", "target_assert": "true", "reflex_script": "return {ok:true}", "verify_script": "return {ok:true}"}],
}, {})
check("owner_registered bad entry_url refused", refused2["ok"] is False and "exact entry URL" in refused2.get("why", ""))

print(f"\n{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
