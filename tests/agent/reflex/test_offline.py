#!/usr/bin/env python3
"""Offline tests for magi-reflex (agent/lib/reflex/) — no browser, no network.

Covers what the live replay path (cli.py -> runner.py) depends on: the script
wrappers every phase runs through (wrap_script args + h helpers, wrap_assert
pre-action target assertion), the verify contract (check_result), the trace
cap (_head), the helper dispatch behind h.click/h.text/h.key, the
entry-domain lock, the structural guard (allowlist/owner_registered), the net
signature math, the stale/seal lifecycle, and the post-action navigation wait.
The live acceptance (practice reflexes on harmless surfaces, --trace proving
zero model calls) is described in docs/magi-reflex.md.

  python3 tests/agent/reflex/test_offline.py
"""
import json
import pathlib
import shutil
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
LIB = HERE.parents[2] / "agent" / "lib"

fails = total = 0


def check(what, cond):
    global fails, total
    total += 1
    print(("  PASS  " if cond else "  FAIL  ") + what)
    if not cond:
        fails += 1


# ---- wrap_script / wrap_assert: the live wrapper every phase runs through ----
# (runner.py: reflex script -> steady -> verify all evaluate through these;
# the deleted v1 agent/lib/reflex.py wrap/judge tested here before covered
# nothing the runner executes)
sys.path.insert(0, str(LIB))
from reflex import runner as RR  # noqa: E402

expr = RR.wrap_script("return {ok: true};", {"docs": ["/a.pdf"]})
check("wrap_script() injects ARGS JSON", '"docs": ["/a.pdf"]' in expr)
check("wrap_script() keeps the script body", "return {ok: true};" in expr)
check("wrap_script() is an async fn of (ARGS, h)",
      expr.startswith("(async (ARGS, h) => {") and expr.rstrip().endswith("window.__mrH)"))
check("wrap_script() passes the live helper channel, not page globals",
      "window.__mrH" in expr and "args.docs" not in expr)

assert_src = RR.wrap_assert("await h.eval('!!document.querySelector(\"#x\")')", {"docs": ["/a.pdf"]})
check("wrap_assert() keeps the assertion expr", "document.querySelector" in assert_src)
check("wrap_assert() passes ARGS to the assertion", '"docs": ["/a.pdf"]' in assert_src)
check("wrap_assert() returns {ok:true} on truthy", "{ok: true}" in assert_src)
check("wrap_assert() carries why on falsy", "pre-action target assertion falsy" in assert_src)
check("wrap_assert() quotes the failing expr in why", "document.querySelector" in assert_src.split("pre-action target assertion falsy")[1])

for helper in ("click:", "clickAt:", "focus:", "text:", "key:", "eval:", "files:", "sleep:", "now:"):
    check(f"BOOT provides h.{helper[:-1]}", helper in RR.BOOT)
check("helper channel uses the __mrCmd binding", RR.HELPER_CMD == "__mrCmd" and "__mrCmd" in RR.BOOT)
check("helpers throw on {ok:false} so a failed helper fails the phase",
      "r.ok === false" in RR.BOOT)

# ---- check_result(): the verify contract the runner judges every script by ----
check("check_result({ok:true}) passes", RR.check_result({"ok": True}, "reflex script") is None)
check("check_result({ok:false,why}) carries why",
      RR.check_result({"ok": False, "why": "no bubbles"}, "reflex script") == "no bubbles")
check("check_result({ok:false}) without why names it",
      RR.check_result({"ok": False}, "reflex script") == "no why given")
check("check_result() is strict: only True passes",
      RR.check_result({"ok": 1}, "reflex script") is not None)
check("check_result(non-dict) fails with the raw value",
      "expected {ok:" in RR.check_result(True, "verify script"))

# ---- _head(): the trace cap (the live equivalent of the old trunc) ----
check("_head caps long payloads", len(RR._head({"a": "x" * 5000})) <= 300)
check("_head keeps short payloads whole", RR._head({"a": 1}) == '{"a": 1}')

# ---- entry-domain lock + helper dispatch: the code that clicks/types ----
class _FakeTab:
    def __init__(self, host):
        self._host = host

    def eval_world(self, expr, timeout=10):
        return {"host": self._host, "href": f"https://{self._host}/"}

loc = RR.enforce_entry_domain(_FakeTab("example.com"), {"domain": "example.com"}, "p1")
check("enforce_entry_domain passes on-domain", loc["host"] == "example.com")
try:
    RR.enforce_entry_domain(_FakeTab("evil.com"), {"domain": "example.com"}, "p1")
    check("enforce_entry_domain aborts off-domain", False)
except RR.RunFail as e:
    check("enforce_entry_domain aborts off-domain", "entry-domain lock" in str(e))


class _FakeCDP:
    def __init__(self):
        self.sent = []

    def send(self, method, params, session_id=None, timeout=None):
        self.sent.append((method, params))
        return {}


def _bare_tab():
    t = RR.OwnTab.__new__(RR.OwnTab)
    t.cdp, t.sid, t._dead, t._chooser = _FakeCDP(), "s1", False, None
    return t


t = _bare_tab()
check("helper insert_text dispatches Input.insertText",
      t._do_helper({"cmd": "insert_text", "text": "hi"}) == {"ok": True}
      and t.cdp.sent[-1] == ("Input.insertText", {"text": "hi"}))
t = _bare_tab()
check("helper key Enter dispatches keyDown+keyUp",
      t._do_helper({"cmd": "key", "key": "Enter"}) == {"ok": True}
      and [m for m, _ in t.cdp.sent] == ["Input.dispatchKeyEvent"] * 2)
check("helper unknown key fails the phase, not the runner",
      _bare_tab()._do_helper({"cmd": "key", "key": "F13"}).get("ok") is False)
check("helper unknown cmd fails the phase, not the runner",
      _bare_tab()._do_helper({"cmd": "bogus"}).get("ok") is False)
t = _bare_tab()
check("helper click_at x/y dispatches trusted mouse events",
      t._do_helper({"cmd": "click_at", "x": 10, "y": 20}) == {"ok": True}
      and [m for m, _ in t.cdp.sent] == ["Input.dispatchMouseEvent"] * 3)
check("click without selector fails with why, no CDP touched",
      _bare_tab()._cmd_click({}) == {"ok": False, "why": "click: no selector"})

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

# ---- v2a: net signature normalization / matching / drift gate ----
from reflex import signature as SG  # noqa: E402

check("normalize_path drops query and normalizes ids",
      SG.normalize_path("https://x/users/123/posts/550e8400-e29b-41d4-a716-446655440000?q=1#f")
      == "/users/:id/posts/:id")
check("normalize_path keeps words", SG.normalize_path("https://x/authenticate") == "/authenticate")
check("normalize_path root", SG.normalize_path("https://x/") == "/")
check("normalize_request uppercases method",
      SG.normalize_request("post", "https://x/post") == "POST /post")
check("matches substring", SG.matches("https://x/authenticate", ["/authenticate"]))
check("matches glob", SG.matches("https://x/post", ["*/post"]))
check("matches empty patterns false", not SG.matches("https://x/post", []))

reqs = [
    {"method": "GET", "url": "https://x/", "type": "Document"},
    {"method": "GET", "url": "https://x/app.js", "type": "Script"},
    {"method": "POST", "url": "https://x/authenticate", "type": "Document"},
    {"method": "GET", "url": "https://x/authenticate?x=1", "type": "XHR"},
]
obs = SG.observe(reqs, ["/authenticate"])
check("observe filters type and path",
      obs["count"] == 2 and obs["entries"] == ["POST /authenticate", "GET /authenticate"])
check("observe is ordered-hash stable", SG.observe(reqs, ["/authenticate"])["hash"] == obs["hash"])
check("observe empty filter is a real empty signature", SG.observe(reqs, [])["count"] == 0)
check("compare accepts equal", SG.compare(obs, obs) is None)
check("compare catches count drift",
      "drift" in SG.compare({"hash": obs["hash"], "count": 99}, obs))
check("compare catches hash drift",
      "drift" in SG.compare({"hash": "sha256:deadbeef", "count": obs["count"]}, obs))
check("compare rejects missing hash", "no hash" in SG.compare({}, obs))
ph = {"net_filter": ["/authenticate"], "net_signature": obs}
check("check_phase passes on match", SG.check_phase(ph, reqs)[1] is None)
check("check_phase flags tampered signature",
      "drift" in SG.check_phase({**ph, "net_signature": {"hash": "sha256:deadbeef", "count": 1}}, reqs)[1])
check("check_phase skips without filter", SG.check_phase({"net_filter": None}, reqs) == (None, None))

# the live drill showed a form POST can start after the reflex script returns;
# the runner must wait for that navigation + load before closing the drift window
tab = RR.OwnTab(None)
seen = {}
tab.wait_load_since = lambda t, timeout=20: seen.setdefault("t", t) is not None
tab._navs = [time.time() - 0.01]
check("wait_navigation_since waits for the load after an action navigation",
      tab.wait_navigation_since(time.time() - 1, grace=0.2) is True and "t" in seen)
tab2 = RR.OwnTab(None)
check("wait_navigation_since returns False when no navigation appears",
      tab2.wait_navigation_since(time.time(), grace=0.05) is False)

# ---- v2a: stale lifecycle + seal updates (throwaway db) ----
tmpdb = pathlib.Path(tempfile.mkdtemp()) / "reflexes.db"
conn = S.connect(tmpdb)


def drift_doc(sig=None):
    return {"domain": "example.com", "name": "drift-test", "entry_url": "https://example.com/",
            "phases": [{"name": "p1", "target_assert": "true", "reflex_script": "1",
                        "verify_script": "1", "net_filter": ["/api"], "net_signature": sig}]}


S.install(conn, drift_doc())
row = S.get(conn, "example.com", "drift-test")
check("new reflex is candidate", row["status"] == "candidate")
S.mark_success(conn, "example.com", "drift-test", False)
check("success promotes candidate", S.get(conn, "example.com", "drift-test")["status"] == "active")
check("suggest offers active",
      any(r["name"] == "drift-test" for r in S.suggest(conn, "https://example.com/x")))
S.mark_stale(conn, "example.com", "drift-test", False)
check("drift marks stale", S.get(conn, "example.com", "drift-test")["status"] == "stale")
check("suggest omits stale",
      not any(r["name"] == "drift-test" for r in S.suggest(conn, "https://example.com/x")))
S.update_signatures(conn, "example.com", "drift-test",
                    {"p1": {"hash": "sha256:" + "a" * 64, "count": 1, "entries": ["POST /api"]}}, False)
row = S.get(conn, "example.com", "drift-test")
check("seal returns stale to active", row["status"] == "active")
check("seal stores the signature",
      json.loads(row["phases"])[0]["net_signature"]["hash"] == "sha256:" + "a" * 64)
S.install(conn, drift_doc())
row = S.get(conn, "example.com", "drift-test")
check("install over active parks candidate", row["candidate"] is not None)
S.mark_stale(conn, "example.com", "drift-test", True)
row = S.get(conn, "example.com", "drift-test")
check("candidate drift drops candidate, active keeps serving",
      row["candidate"] is None and row["status"] == "active")
S.install(conn, drift_doc())  # active parks another candidate
S.mark_stale(conn, "example.com", "drift-test", False)  # the serving active drifts
row = S.get(conn, "example.com", "drift-test")
check("serving drift marks stale and keeps the waiting candidate",
      row["status"] == "stale" and row["candidate"] is not None)
S.install(conn, drift_doc())
row = S.get(conn, "example.com", "drift-test")
check("install over stale gives a fresh candidate and clears the shadow",
      row["status"] == "candidate" and row["candidate"] is None)
conn.close()
shutil.rmtree(tmpdb.parent, ignore_errors=True)

print(f"\n{total - fails}/{total} passed")
sys.exit(1 if fails else 0)
