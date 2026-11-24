"""dogma_filter: measure steady-state detectors on real pages, then pick by numbers.

FlowPilot's hint was "wait for the network sequence"; the brief says measure before
believing. After a navigation (and after a navigation-like action) three candidates
for "the page has settled" are:

  1. net_quiesce(q)  - no new network request for q ms
  2. dom_stable(q)   - no DOM mutation for q ms
  3. fixed(d)        - just wait d ms

For each candidate we record, per page: latency to fire, whether it fired before
loadEventFired, DOM mutations that still happened AFTER it fired (prematurity),
requests still happening after it fired, and a fingerprint drift (node count /
text length) between fire time and a reference snapshot taken much later.

Own tabs only; every tab is closed in finally. Nothing is typed into WhatsApp.

  python3 dogma_filter.py [--json OUT] [--window NAV_SETTLE] [--action-window AW]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cdp import CDP, CDPError  # noqa: E402

PAGES = [
    ("web.whatsapp.com", "https://web.whatsapp.com/", "wa-open-self-chat"),
    ("github.com", "https://github.com/", "gh-search-typing"),
    ("en.wikipedia.org", "https://en.wikipedia.org/wiki/Automation", "wiki-search-typing"),
]
NET_QUIESCE_MS = [300, 500, 800, 1200]
DOM_STABLE_MS = [400, 700, 1000, 1500]
FIXED_MS = [2000, 3000, 5000]
POLL_MS = 0.4
SNAPSHOT_MARGIN = 1.0  # fingerprint at fire time = first snapshot at/after fire + margin

MUT_BOOT = (
    "(() => { if (window.__mrMutBoot) return 'already'; window.__mrMutBoot = 1;"
    " const boot = () => { const d = document.documentElement;"
    "   if (!d) return setTimeout(boot, 10);"
    "   if (window.__mrMut) return;"
    "   window.__mrMut = { last: Date.now(), n: 0 };"
    "   new MutationObserver((muts) => { window.__mrMut.last = Date.now();"
    "   window.__mrMut.n += muts.length; }).observe(d,"
    "   { subtree: true, childList: true, attributes: true, characterData: true }); };"
    " boot(); return 'started'; })()"
)
FINGERPRINT = (
    "(() => { try { return { nodes: document.getElementsByTagName('*').length,"
    " text: (document.body && document.body.innerText ? document.body.innerText.length : 0) };"
    " } catch (e) { return { nodes: -1, text: -1 }; } })()"
)


def q_fire(times, t_start, quiet_s):
    """First moment >= t_start where no event in times falls in the (T-q, T] window."""
    t = t_start + quiet_s
    while True:
        window = [x for x in times if t - quiet_s < x <= t]
        if not window:
            return t
        t = window[-1] + quiet_s


class Recorder:
    """Collects one settle window: request times, mutation times, fingerprint snapshots."""

    def __init__(self, cdp, sid):
        self.cdp, self.sid = cdp, sid
        self.reqs, self.muts, self.snaps, self.mut_counts = [], [], [], []
        self.t0 = None
        self.stop_flag = False

        def on_event(msg):
            if msg.get("sessionId") != sid:
                return
            if msg["method"] == "Network.requestWillBeSent":
                self.reqs.append(time.time())

        cdp.on(on_event)

    def _mut_state(self):
        r = self.cdp.send("Runtime.evaluate", {"expression": "(() => window.__mrMut ? {last: window.__mrMut.last, n: window.__mrMut.n} : null)()", "returnByValue": True}, self.sid)
        return r["result"]["value"]

    def _fingerprint(self):
        r = self.cdp.send("Runtime.evaluate", {"expression": FINGERPRINT, "returnByValue": True}, self.sid)
        return r["result"]["value"]

    def record(self, seconds):
        """Record for `seconds` wall-clock, starting the clock on first call."""
        if self.t0 is None:
            self.t0 = time.time()
            st = self._mut_state()
            if st:
                self.mut_t0, self.n0 = st["last"], st["n"]
        end = time.time() + seconds
        while time.time() < end and not self.stop_flag:
            try:
                st = self._mut_state()
                if st:
                    self.muts.append(st["last"])
                    self.mut_counts.append(st["n"])
                self.snaps.append((time.time(), self._fingerprint()))
            except (CDPError, KeyError):
                pass
            time.sleep(POLL_MS)

    def stop_early_if_settled(self, min_seconds):
        """After min_seconds, stop once 3 s pass with no request and no mutation."""
        if self.t0 is None or time.time() - self.t0 < min_seconds:
            return
        now = time.time()
        if not [x for x in self.reqs if x > now - 3.0] and not [x for x in self.muts if x > now - 3.0]:
            self.stop_flag = True

    def summarize(self, t_ref_snap=None):
        t0 = self.t0
        out = {"requests_total": len(self.reqs), "mutations_total": (self.mut_counts[-1] - self.n0) if self.mut_counts else 0}
        ref = self.snaps[-1][1] if self.snaps else None
        detectors = []
        for q in NET_QUIESCE_MS:
            detectors.append(("net_quiesce", q, q_fire(self.reqs, t0, q / 1000.0)))
        for q in DOM_STABLE_MS:
            detectors.append(("dom_stable", q, q_fire(self.muts, t0, q / 1000.0)))
        for d in FIXED_MS:
            detectors.append(("fixed", d, t0 + d / 1000.0))
        for kind, param, t_fire in detectors:
            # fingerprint at fire time = first snapshot at/after fire (margin for poll jitter);
            # prematurity = how much DOM/network still happened after the detector said "settled"
            late = [s for s in self.snaps if s[0] >= t_fire - SNAPSHOT_MARGIN]
            fp = late[0][1] if late else None
            idx = min(range(len(self.snaps)), key=lambda i: abs(self.snaps[i][0] - t_fire)) if self.snaps else 0
            muts_after = (self.mut_counts[-1] - self.mut_counts[idx]) if self.mut_counts and idx < len(self.mut_counts) else None
            reqs_after = len([x for x in self.reqs if x > t_fire])
            drift = {"dnodes": fp["nodes"] - ref["nodes"], "dtext": fp["text"] - ref["text"]} if fp and ref else None
            row = {
                "detector": kind, "param_ms": param, "latency_ms": round((t_fire - t0) * 1000),
                "muts_after": muts_after, "reqs_after": reqs_after, "drift": drift,
            }
            if t_ref_snap is not None:
                row["fired_before_load"] = t_fire < t_ref_snap
            out.setdefault("detectors", []).append(row)
        return out


def eval_js(cdp, sid, expr, world=None):
    params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
    if world:
        params["contextId"] = world
    r = cdp.send("Runtime.evaluate", params, sid)
    if r.get("exceptionDetails"):
        raise CDPError(r["exceptionDetails"].get("exception", {}).get("description", "js exception"))
    return r["result"].get("value")


def do_action(cdp, sid, action):
    """The navigation-like action, trusted input only. Returns True if it happened."""
    if action == "wa-open-self-chat":
        pos = eval_js(cdp, sid, "(() => { const sel = document.querySelector('#pane-side [data-testid=\"message-yourself-row\"]');"
                                " const row = sel && sel.closest('[role=\"row\"]'); if (!row) return null;"
                                " row.scrollIntoView({ block: 'center' }); const r = row.getBoundingClientRect();"
                                " return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()")
        if not pos:
            return False
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            cdp.send("Input.dispatchMouseEvent", {"type": typ, "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
        return True
    if action in ("gh-search-typing", "wiki-search-typing"):
        sel = "input[name='q'], input[name='query'], input[name='search'], #searchInput, input[aria-label='Search']"
        expr = ("(() => { const el = document.querySelector(\"" + sel + "\"); if (!el) return null;"
                " el.scrollIntoView({ block: 'center' }); el.focus();"
                " const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()")
        pos = eval_js(cdp, sid, expr)
        if not pos:
            return False
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            cdp.send("Input.dispatchMouseEvent", {"type": typ, "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1}, sid)
        time.sleep(0.4)
        cdp.send("Input.insertText", {"text": "magi reflex"}, sid)
        return True
    raise ValueError(action)


def measure_page(cdp, name, url, action, nav_settle, action_window):
    tid = cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
    sid = None
    try:
        sid = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        cdp.send("Page.enable", {}, sid)
        cdp.send("Runtime.enable", {}, sid)
        cdp.send("Network.enable", {}, sid)
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": MUT_BOOT}, sid)

        load_event = {"t": None}
        cdp.on(lambda m: load_event.update(t=time.time()) if m.get("method") == "Page.loadEventFired" and m.get("sessionId") == sid and load_event["t"] is None else None)

        rec = Recorder(cdp, sid)
        t_nav = time.time()
        cdp.send("Page.navigate", {"url": url}, sid, timeout=45)
        while load_event["t"] is None and time.time() - t_nav < 45:
            time.sleep(0.1)
        rec.t0 = t_nav  # clock starts at navigate
        st0 = rec._mut_state()
        if st0:
            rec.mut_t0, rec.n0 = st0["last"], st0["n"]

        rec.record(nav_settle)
        nav = rec.summarize(t_ref_snap=load_event["t"])
        nav["load_ms"] = round((load_event["t"] - t_nav) * 1000) if load_event["t"] else None
        nav["settled_early"] = rec.stop_flag

        result = {"page": name, "url": url, "nav": nav, "action": None}

        if action:
            time.sleep(1.0)
            ok = do_action(cdp, sid, action)
            if ok:
                rec2 = Recorder(cdp, sid)
                rec2.record(action_window)
                result["action"] = {"kind": action, **rec2.summarize()}
        return result
    finally:
        try:
            cdp.send("Target.closeTarget", {"targetId": tid}, timeout=10)
        except CDPError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="/tmp/opencode/magi-reflex-dogma.json")
    ap.add_argument("--window", type=float, default=20.0, help="nav settle window seconds")
    ap.add_argument("--action-window", type=float, default=12.0, help="action settle window seconds")
    args = ap.parse_args()

    cdp = CDP()
    cdp.send("Target.setDiscoverTargets", {"discover": True})
    results = []
    try:
        for name, url, action in PAGES:
            print(f"== {name} ({url})", flush=True)
            r = measure_page(cdp, name, url, action, args.window, args.action_window)
            results.append(r)
            nav = r["nav"]
            print(f"   load: {nav['load_ms']}ms, requests: {nav['requests_total']}, mutations: {nav['mutations_total']}")
            for d in nav["detectors"]:
                dr = d["drift"] or {}
                print(f"   {d['detector']:<11} {d['param_ms']:>5}ms  latency {d['latency_ms']:>6}ms  "
                      f"muts_after {d['muts_after']:>6}  reqs_after {d['reqs_after']:>3}  "
                      f"drift dnodes {dr.get('dnodes')} dtext {dr.get('dtext')}"
                      + ("  FIRED-BEFORE-LOAD" if d.get("fired_before_load") else ""))
            if r["action"]:
                a = r["action"]
                print(f"   -- after action {a['kind']}: requests {a['requests_total']}, mutations {a['mutations_total']}")
                for d in a["detectors"]:
                    dr = d["drift"] or {}
                    print(f"   {d['detector']:<11} {d['param_ms']:>5}ms  latency {d['latency_ms']:>6}ms  "
                          f"muts_after {d['muts_after']:>6}  reqs_after {d['reqs_after']:>3}  "
                          f"drift dnodes {dr.get('dnodes')} dtext {dr.get('dtext')}")
            else:
                print("   -- action not available on this page")
    finally:
        cdp.close()
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(results, indent=1))
    print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
