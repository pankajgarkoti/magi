"""runner: the replay loop. Zero model calls by construction — CDP only.

Generic on purpose: all site knowledge lives in the reflex doc's scripts (the next
reflexes are new JSON, not new code — and per the 25 Nov incident rules they are
authored against harmless practice surfaces only). The runner knows only: own tab,
navigate, entry-domain lock, per-phase pre-action target assertion, phase ->
reflex script -> steady wait -> verify script (probe-style, isolated world),
repeat, close tab, one bus line. Every fumble from 24 Nov is structural here:

  menu uid flicker        h.click(selector, {tries, gap}) re-finds the element
                          fresh on every try (a re-render between find and
                          click costs one try, not the run)
  message sync wait       verify_poll {interval_ms, timeout_ms} retries the
                          verify script for up to its timeout (3 min for send)
  verify-bubbles-before-  verify always runs in OUR tab, and the tab is closed
  close                   only after the whole phase list ends (finally)

Scripts (reflex + verify) are the BODY of an async function receiving
(ARGS, h). ARGS is the --args JSON; h is the helper channel backed by a
Runtime.addBinding in the reflex world (the browser-bus probe pattern):

  h.click(sel, {closest, tries, gap})  trusted CDP mouse click at the element's
                                       center; re-queries sel on every try
  h.focus(sel)                         focus an element
  h.text(str)                          Input.insertText into the focused element
  h.key('Enter'|'Escape'|'Tab')        trusted key event
  h.eval(expr)                         run JS in the reflex world, JSON back
  h.files([abs paths])                 answer the intercepted file chooser
  h.sleep(ms), h.now()

A script returns {ok: true} or {ok: false, why: '...'}. Verify scripts must
return {ok, why?} too and never act — they read the DOM like a probe.

Steady rules (dogma-filtered, measured on web.whatsapp.com / github.com /
en.wikipedia.org, see docs/magi-reflex.md): after each reflex action wait for
DOM stability by default {type: 'dom_stable', quiet_ms: 700, timeout_ms: 8000}
— network quiesce measured FALSE-settle on chatty SPAs (WhatsApp: 217 requests
after a 1.2 s quiesce fire), so it is an explicit opt-in per phase, with a
timeout backstop because verify is the real authority. fixed delay is available
for phases that want it.
"""
import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from . import store
from .cdp import CDP, CDPError, CDPClosed

WORLD = "magi-reflex"
HELPER_CMD = "__mrCmd"
NAV_TIMEOUT_S = 45
EVAL_CHUNK_S = 20  # one verify attempt gets at most this long by itself

BOOT = r"""
(() => {
  if (window.__mrReady) return 'already';
  window.__mrReady = 1;
  window.__mr = { id: 0, pending: {} };
  window.__mrMut = { last: Date.now(), n: 0 };
  new MutationObserver((muts) => { window.__mrMut.last = Date.now(); window.__mrMut.n += muts.length; })
    .observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  window.__mrCall = (o) => new Promise((res, rej) => {
    const id = ++window.__mr.id;
    window.__mr.pending[id] = { res, rej };
    try { __mrCmd(JSON.stringify(Object.assign({ id }, o))); }
    catch (e) { delete window.__mr.pending[id]; rej(String(e)); }
  });
  // helpers throw on failure and return raw values: a failed helper IS a phase
  // failure, with the why in the error text — scripts stay small
  const unwrap = (r) => { if (r && r.ok === false) throw new Error(r.why || 'helper failed'); return r && r.value !== undefined ? r.value : r; };
  window.__mrH = {
    click: async (sel, opts) => unwrap(await window.__mrCall(Object.assign({ cmd: 'click_at', sel: sel }, opts || {}))),
    clickAt: async (x, y) => unwrap(await window.__mrCall({ cmd: 'click_at', x: Number(x), y: Number(y) })),
    focus: async (sel) => unwrap(await window.__mrCall({ cmd: 'eval', expr: 'document.querySelector(' + JSON.stringify(sel) + ') ? (document.querySelector(' + JSON.stringify(sel) + ').focus(), true) : false' })),
    text: async (t) => unwrap(await window.__mrCall({ cmd: 'insert_text', text: String(t) })),
    key: async (k) => unwrap(await window.__mrCall({ cmd: 'key', key: String(k) })),
    eval: async (expr) => unwrap(await window.__mrCall({ cmd: 'eval', expr: expr })),
    files: async (paths, opts) => unwrap(await window.__mrCall(Object.assign({ cmd: 'set_files', files: paths }, opts || {}))),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    now: () => Date.now(),
  };
  return 'started';
})()
"""

KEYS = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13, "text": "\r"},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9},
}


class RunFail(Exception):
    def __init__(self, phase, why):
        super().__init__(f"{phase}: {why}")
        self.phase, self.why = phase, why


def _head(obj, n=300):
    try:
        s = json.dumps(obj, ensure_ascii=True)
    except Exception:
        s = str(obj)
    return s if len(s) <= n else s[: n - 1] + "…"


class Tracer:
    """JSONL trace of every CDP message in both directions + run notes.

    This is the zero-model-calls proof: a replay run's entire behaviour is one
    CDP conversation, timestamped, with nothing else in it. With echo=True the
    same stream is printed compactly (--trace).
    """

    def __init__(self, path, echo=False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a", buffering=1)
        self.t0 = time.time()
        self.echo = echo

    def _write(self, rec):
        rec["dt"] = round((time.time() - self.t0) * 1000)
        self.f.write(json.dumps(rec, ensure_ascii=True) + "\n")
        if self.echo:
            d = rec.get("d")
            if d == "send":
                print(f"  > {rec.get('m')}{' (session)' if rec.get('s') else ''} {rec.get('p', '')}", flush=True)
            elif d == "resp" and not rec.get("ok"):
                print(f"  < resp {rec.get('id')} ERROR {rec.get('e')}", flush=True)
            elif d == "note":
                print(f"  · {rec.get('text')} {({k: v for k, v in rec.items() if k not in ('d', 'dt', 'text')})}", flush=True)

    def __call__(self, direction, msg):
        rec = {"d": direction}
        if direction == "send":
            rec.update(id=msg.get("id"), m=msg.get("method"), s=bool(msg.get("sessionId")), p=_head(msg.get("params")))
        elif direction == "resp":
            ok = "error" not in msg
            rec.update(id=msg.get("id"), ok=ok)
            if ok:
                rec.update(r=_head(msg.get("result"), 160))
            else:
                rec.update(e=_head(msg.get("error", {}).get("message")))
        elif direction == "event":
            rec.update(m=msg.get("method"), s=bool(msg.get("sessionId")), p=_head(msg.get("params")))
        self._write(rec)

    def note(self, text, **kw):
        rec = {"d": "note", "text": text}
        rec.update(kw)
        self._write(rec)

    def close(self):
        self.f.close()


class OwnTab:
    """The one tab a run may touch: created for the run, always closed by it."""

    def __init__(self, cdp, tracer=None):
        self.cdp = cdp
        self.tracer = tracer
        self.tid = None
        self.sid = None
        self._ctx = None
        self._world_lock = threading.Lock()
        self._reqs = []
        self._req_lock = threading.Lock()
        self._chooser = None  # (backendNodeId, ts)
        self._dead = False
        self._last_load = 0.0
        self._load_evt = threading.Event()
        self._helper_q = None
        self._helper_thread = None

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        self.tid = self.cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        # bring OUR tab to the front: WhatsApp Web is a single-active-session app
        # (a new tab that stays background shows the "open in another window" lock
        # banner and never renders the chat list). Activation claims the session
        # for the run; closing the tab at the end releases it back.
        try:
            self.cdp.send("Target.activateTarget", {"targetId": self.tid})
        except CDPError:
            pass
        self.sid = self.cdp.send("Target.attachToTarget", {"targetId": self.tid, "flatten": True})["sessionId"]
        for m, p in (("Page.enable", {}), ("Runtime.enable", {}), ("Network.enable", {}),
                     ("Page.setInterceptFileChooserDialog", {"enabled": True})):
            self.cdp.send(m, p, self.sid)
        # the helper binding lives in the reflex world only (a page's main world cannot call it)
        self.cdp.send("Runtime.addBinding", {"name": HELPER_CMD, "executionContextName": WORLD}, self.sid)
        self.cdp.on(self._on_event)
        self._helper_q = queue.Queue()
        self._helper_thread = threading.Thread(target=self._helper_loop, daemon=True)
        self._helper_thread.start()
        return self

    def close(self):
        try:
            if self.tid and not self._dead:
                self.cdp.send("Target.closeTarget", {"targetId": self.tid}, timeout=10)
        except (CDPError, CDPClosed):
            pass
        self._dead = True

    # -- events ------------------------------------------------------------
    def _on_event(self, msg):
        if msg.get("sessionId") != self.sid:
            if msg.get("method") == "Target.targetDestroyed" and msg.get("params", {}).get("targetId") == self.tid:
                self._dead = True
            return
        m = msg.get("method")
        if m == "Network.requestWillBeSent":
            with self._req_lock:
                self._reqs.append(time.time())
        elif m == "Page.loadEventFired":
            self._last_load = time.time()
            self._load_evt.set()
        elif m == "Page.fileChooserOpened":
            self._chooser = (msg["params"].get("backendNodeId"), time.time())
        elif m == "Runtime.executionContextsCleared":
            self._ctx = None
        elif m == "Runtime.bindingCalled" and msg["params"].get("name") == HELPER_CMD:
            self._helper_q.put(msg["params"].get("payload"))

    # -- helpers (worker thread; the reader thread must never block) --------
    def _helper_loop(self):
        q = self._helper_q
        while True:
            payload = q.get()
            if payload is None:
                return
            try:
                req = json.loads(payload)
                result = self._do_helper(req)
            except Exception as e:  # a helper bug fails the phase, not the runner
                result = {"ok": False, "why": f"helper error: {e}"}
            try:
                self._respond(req.get("id"), result)
            except (CDPError, CDPClosed):
                pass

    def _do_helper(self, req):
        cmd = req.get("cmd")
        if cmd == "click_at":
            return self._cmd_click(req)
        if cmd == "insert_text":
            self.cdp.send("Input.insertText", {"text": req.get("text", "")}, self.sid)
            return {"ok": True}
        if cmd == "key":
            k = KEYS.get(req.get("key"))
            if not k:
                return {"ok": False, "why": f"unknown key {req.get('key')!r}"}
            for typ in ("keyDown", "keyUp"):
                params = dict(k, type=typ)
                if typ == "keyUp":
                    params.pop("text", None)
                self.cdp.send("Input.dispatchKeyEvent", params, self.sid)
            return {"ok": True}
        if cmd == "eval":
            return {"ok": True, "value": self.eval_world(req.get("expr", "null"))}
        if cmd == "set_files":
            return self._cmd_set_files(req)
        return {"ok": False, "why": f"unknown helper cmd {cmd!r}"}

    def _cmd_click(self, req):
        if "x" in req and "y" in req and not req.get("sel"):
            # raw trusted click at page coordinates (scripts find the point themselves)
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                self.cdp.send("Input.dispatchMouseEvent",
                              {"type": typ, "x": float(req["x"]), "y": float(req["y"]), "button": "left", "clickCount": 1},
                              self.sid)
            return {"ok": True}
        sel = req.get("sel")
        closest, text = req.get("closest"), req.get("text")
        tries, gap = int(req.get("tries", 3)), float(req.get("gap", 250)) / 1000.0
        if not sel:
            return {"ok": False, "why": "click: no selector"}
        find = (
            "(() => { try { let el = document.querySelector(" + json.dumps(sel) + ");"
            " if (!el) return { ok: 0, why: 'not found' };"
            + (f"el = el.closest({json.dumps(closest)}) || el;" if closest else "")
            + (f"if ({json.dumps(text)} && (el.innerText || '').trim().toLowerCase() !== {json.dumps(text.lower())})"
              " return { ok: 0, why: 'text mismatch: ' + (el.innerText || '').trim().slice(0, 40) };"
              if text else "")
            + " el.scrollIntoView({ block: 'center' });"
            " const r = el.getBoundingClientRect();"
            " if (r.width < 1 || r.height < 1) return { ok: 0, why: 'zero size' };"
            " const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
            " return { ok: 1, x: r.left + r.width / 2, y: r.top + r.height / 2 }; }"
            " catch (e) { return { ok: 0, why: String(e) }; } })()"
        )
        # menu items re-render between find and click (uid flicker): every try
        # re-runs the find expression fresh, and the click only dispatches when
        # two finds ~150ms apart agree on the element's position — a re-render
        # costs a retry, not a misclick
        last = "not found"
        for i in range(max(1, tries)):
            if self._dead:
                return {"ok": False, "why": "own tab died"}
            pos = self.eval_world(find) or {}
            if not pos.get("ok"):
                last = pos.get("why", "not found")
                if i + 1 < tries:
                    time.sleep(gap)
                continue
            time.sleep(0.15)
            pos2 = self.eval_world(find) or {}
            stable = (pos2.get("ok") and abs(pos2.get("x", -99) - pos["x"]) <= 3
                      and abs(pos2.get("y", -99) - pos["y"]) <= 3)
            if not stable:
                last = "element moved between reads (re-render)"
                if i + 1 < tries:
                    time.sleep(gap)
                continue
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                self.cdp.send("Input.dispatchMouseEvent",
                              {"type": typ, "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1},
                              self.sid)
            return {"ok": True, "x": pos["x"], "y": pos["y"]}
        why = f"click {sel}" + (f" [{text}]" if text else "") + f": {last} after {tries} tries"
        return {"ok": False, "why": why}

    def _cmd_set_files(self, req):
        files = req.get("files") or []
        sel = req.get("sel")
        for p in files:
            if not Path(p).is_absolute() or not Path(p).is_file():
                return {"ok": False, "why": f"file not found: {p}"}
        if sel:
            # direct mode: resolve the input the page itself can reach (pierces
            # shadow roots, where some apps keep their <input type=file>)
            ctx = self.ensure_world()
            rr = self.cdp.send("Runtime.evaluate",
                               {"expression": f"document.querySelector({json.dumps(sel)})",
                                "contextId": ctx, "returnByValue": False}, self.sid)
            oid = (rr.get("result") or {}).get("objectId")
            if not oid:
                return {"ok": False, "why": f"set_files: no element matches {sel}"}
            try:
                self.cdp.send("DOM.setFileInputFiles", {"files": files, "objectId": oid}, self.sid)
            except CDPError:
                # fallback for older targets: resolve to nodeId via DOM domain
                try:
                    self.cdp.send("DOM.enable", {}, self.sid)
                except CDPError:
                    pass
                try:
                    node = self.cdp.send("DOM.requestNode", {"objectId": oid}, self.sid)
                except CDPError as e:
                    return {"ok": False, "why": f"set_files: DOM.requestNode: {e}"}
                self.cdp.send("DOM.setFileInputFiles", {"files": files, "nodeId": node["nodeId"]}, self.sid)
            self._fire_file_events(sel)
            return {"ok": True, "mode": "direct"}
        # chooser mode: an attach click opened the (intercepted) file chooser
        chooser, ts = self._chooser or (None, 0)
        waited = 0.0
        while not chooser and waited < 5.0 and not self._dead:
            time.sleep(0.1)
            waited += 0.1
            chooser, ts = self._chooser or (None, 0)
        if not chooser:
            return {"ok": False, "why": "no file chooser opened (did the attach click land?)"}
        self._chooser = None
        # no synthetic change dispatch here: the native change from setFileInputFiles
        # carries the files into the app's draft state, and a spurious empty-files
        # change can reset it. Scripts dispatch explicitly (h.eval) only when a
        # draft fails to appear.
        self.cdp.send("DOM.setFileInputFiles", {"files": files, "backendNodeId": chooser}, self.sid)
        return {"ok": True, "mode": "chooser"}

    def _fire_file_events(self, sel):
        """Dispatch input+change on every input matching sel (belt and braces:
        CDP sets the files; the app's React handler runs on the change event)."""
        try:
            self.eval_world(
                "(() => { const els = Array.from(document.querySelectorAll(" + json.dumps(sel) + "));"
                " els.forEach((el) => { el.dispatchEvent(new Event('input', { bubbles: true }));"
                " el.dispatchEvent(new Event('change', { bubbles: true })); });"
                " return els.length; })()")
        except (CDPError, CDPClosed):
            pass

    def _respond(self, mid, result):
        expr = ("((id, v) => { const s = window.__mr.pending[id];"
                " if (s) { delete window.__mr.pending[id]; s.res(v); } })("
                + f"{int(mid)}, {json.dumps(result)})")
        self.eval_world(expr)

    # -- world + eval ------------------------------------------------------
    def ensure_world(self):
        with self._world_lock:
            if self._ctx:
                return self._ctx
            if self._dead:
                raise RunFail("(tab)", "own tab died")
            r = self.cdp.send("Page.createIsolatedWorld", {"frameId": self.tid, "worldName": WORLD}, self.sid)
            self._ctx = r["executionContextId"]
            self._eval(self._ctx, BOOT)  # helpers + mutation observer; must not re-take the world lock
            return self._ctx

    def _eval(self, ctx, expr, timeout=30):
        r = self.cdp.send("Runtime.evaluate",
                          {"expression": expr, "contextId": ctx, "returnByValue": True, "awaitPromise": True},
                          self.sid, timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise CDPError((d.get("exception") or {}).get("description") or d.get("text") or "js exception")
        return r["result"].get("value")

    def eval_world(self, expr, timeout=30):
        return self._eval(self.ensure_world(), expr, timeout)

    # -- navigation + steady ----------------------------------------------
    def navigate(self, url):
        self._ctx = None
        t = time.time()
        self.cdp.send("Page.navigate", {"url": url}, self.sid, timeout=NAV_TIMEOUT_S)
        if not self.wait_load_since(t, NAV_TIMEOUT_S):
            raise RunFail("(nav)", f"no load event within {NAV_TIMEOUT_S}s for {url}")
        return round((time.time() - t) * 1000)

    def wait_load_since(self, t0, timeout=20.0):
        """True once a loadEventFired newer than t0 arrives (a phase's action may
        navigate; the reflex script's own eval dies with it, so the runner waits
        for the new document here and lets verify judge the outcome)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._last_load >= t0:
                return True
            if self._dead:
                return False
            time.sleep(0.05)
        return False

    def steady(self, rule, note=""):
        rule = rule or {}
        typ = rule.get("type", "dom_stable")
        if typ == "fixed":
            time.sleep(rule.get("delay_ms", 1000) / 1000.0)
            return {"fired": rule.get("delay_ms", 1000), "timeout": False}
        quiet = rule.get("quiet_ms", 700) / 1000.0
        timeout = rule.get("timeout_ms", 8000) / 1000.0
        deadline = time.time() + timeout
        if typ == "net_quiesce":
            t0 = time.time()
            while time.time() < deadline:
                with self._req_lock:
                    last = self._reqs[-1] if self._reqs else t0
                if time.time() - last >= quiet:
                    return {"fired": round((time.time() - t0) * 1000), "timeout": False}
                time.sleep(0.1)
        elif typ == "dom_stable":
            t0 = time.time()
            while time.time() < deadline:
                try:
                    st = self.eval_world("(() => ({ last: window.__mrMut ? window.__mrMut.last : 0, now: Date.now() }))()") or {}
                    if st.get("now", 0) - st.get("last", 0) >= quiet * 1000:
                        return {"fired": round((time.time() - t0) * 1000), "timeout": False}
                except (CDPError, CDPClosed):
                    break
                time.sleep(0.15)
        else:
            raise RunFail("(steady)", f"unknown steady_rule type {typ!r}")
        # steady is best-effort: verify is the authority, a timeout proceeds to it
        if self.tracer:
            self.tracer.note(f"steady {typ} timed out after {rule.get('timeout_ms')}ms; proceeding to verify", phase=note)
        return {"fired": round((time.time() - t0) * 1000), "timeout": True}

    def last_why(self):
        return "own tab died" if self._dead else None


def wrap_script(script, args):
    return f"(async (ARGS, h) => {{\n{script}\n}})({json.dumps(args)}, window.__mrH)"


def wrap_assert(expr, args):
    """The pre-action target assertion: run EXPR with the script helpers, judge truthiness.

    This is the structural guarantee from the 25 Nov incident: the exact intended
    target (element/page state) is proven BEFORE any typing or clicking happens,
    on every phase, every replay. A falsy or throwing assertion fails the phase
    before the reflex script's first action can run.
    """
    why = json.dumps(f"pre-action target assertion falsy: {_head(expr, 120)}")
    return ("(async (ARGS, h) => { const v = (" + expr + ");\n"
            f"  return v ? {{ok: true}} : {{ok: false, why: {why}}};\n"
            f"}})({json.dumps(args)}, window.__mrH)")


def host_matches(host, domain):
    """The entry-domain lock's comparison: exact host, or a subdomain of the reflex's domain."""
    host = (host or "").lower().rstrip(".")
    domain = (domain or "").lower().rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def enforce_entry_domain(tab, doc, phase):
    """Abort if the tab's current origin has left the reflex's entry domain."""
    try:
        loc = tab.eval_world("(() => ({ host: location.hostname, href: location.href }))()", timeout=10) or {}
    except Exception as e:
        raise RunFail(phase, f"entry-domain lock: could not read location ({e})")
    host = loc.get("host")
    if not host_matches(host, doc.get("domain")):
        raise RunFail(
            phase,
            f"entry-domain lock: page host {host!r} left reflex domain {doc.get('domain')!r} (href={loc.get('href')!r})",
        )
    return loc


def check_result(v, what):
    """Scripts speak {ok, why?}. Anything else is a failure with the raw value."""
    if isinstance(v, dict) and v.get("ok") is True:
        return None
    if isinstance(v, dict) and v.get("ok") is False:
        return str(v.get("why", "no why given"))[:400]
    return f"{what} returned {_head(v, 160)} (expected {{ok: true|false, why?}})"


def emit_bus(name, line):
    try:
        subprocess.run(["orch-bus", "emit", "magi-reflex", line[:280]], check=False, timeout=15)
    except Exception as e:  # the bus is best-effort; the run result does not depend on it
        print(f"orch-bus emit failed: {e}", flush=True)


def run_reflex(doc, args, verbose=False, trace_echo=False):
    """Replay one reflex doc. Returns {ok, elapsed_ms, phase?, why?, trace}.

    Own tab only; the tab is closed in every path. One bus line on completion.
    """
    name = doc["name"]
    phases = doc["phases"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    trace_path = store.TRACE_DIR / f"{ts}-{name}.jsonl"
    tracer = Tracer(trace_path, echo=trace_echo)
    t_run = time.time()
    fail_phase = fail_why = None
    phase_rows = []
    cdp = None
    tab = None
    # Incident rules, 25 Nov — enforced before anything opens:
    #   1. no reflex work on messaging/social surfaces (the wa-send-docs incident);
    #   2. every phase must carry a pre-action target assertion;
    #   3. the page we land on must be the reflex's own domain (entry-domain lock).
    owner_registered = bool(doc.get("owner_registered", 0))
    try:
        store.check_domain_allowed(doc.get("domain") or "", owner_registered=owner_registered)
    except store.StoreError as e:
        tracer.note("refused", why=str(e))
        tracer.close()
        emit_bus(name, f"magi-reflex {name} REFUSED: {e}")
        return {"ok": False, "elapsed_ms": 0, "phase": "(refusal)", "why": str(e),
                "line": f"magi-reflex {name} REFUSED: {e}", "trace": str(trace_path)}
    if owner_registered and not store.domain_allowed(doc.get("domain") or ""):
        from urllib.parse import urlparse
        eu = (doc.get("entry_url") or "").strip()
        p = urlparse(eu)
        host = (p.hostname or "").lower().rstrip(".")
        dom = (doc.get("domain") or "").lower().rstrip(".")
        if p.scheme not in ("http", "https") or not host or host != dom or not eu:
            why = f"owner-registered reflex requires exact entry URL on domain {doc.get('domain')!r}"
            tracer.note("refused", why=why)
            tracer.close()
            line = f"magi-reflex {name} REFUSED: {why}"
            emit_bus(name, line)
            return {"ok": False, "elapsed_ms": 0, "phase": "(refusal)", "why": why,
                    "line": line, "trace": str(trace_path)}
    missing = [ph.get("name", "?") for ph in phases if not str(ph.get("target_assert") or "").strip()]
    if missing:
        why = ("no pre-action target assertion on phase(s): " + ", ".join(missing)
               + " (incident rule, 25 Nov)")
        tracer.note("refused", why=why)
        tracer.close()
        line = f"magi-reflex {name} REFUSED: {why}"
        emit_bus(name, line)
        return {"ok": False, "elapsed_ms": 0, "phase": "(refusal)", "why": why,
                "line": line, "trace": str(trace_path)}
    try:
        if verbose:
            print(f"magi-reflex run {name}: {len(phases)} phase(s), entry {doc['entry_url']}", flush=True)
        cdp = CDP()
        cdp.trace = tracer
        cdp.send("Target.setDiscoverTargets", {"discover": True})
        tab = OwnTab(cdp, tracer).open()
        tracer.note(f"run start {name}", entry_url=doc["entry_url"], phases=[p["name"] for p in doc["phases"]])
        nav_ms = tab.navigate(doc["entry_url"])
        tracer.note("navigated", ms=nav_ms)
        entry_steady = doc.get("entry_steady") or {"type": "fixed", "delay_ms": 1000}
        tab.steady(entry_steady, note="(entry)")

        # entry-domain lock (incident rule, 25 Nov): the reflex acts only on its
        # own domain — a redirect, a parked page or a wrong entry can never turn
        # a reflex loose on some other site's page
        loc = tab.eval_world("(() => ({ host: location.hostname, href: location.href }))()", timeout=10) or {}
        if not host_matches(loc.get("host"), doc.get("domain")):
            raise RunFail(
                "(entry)",
                f"entry-domain lock: page host {loc.get('host')!r} is not the reflex domain "
                f"{doc.get('domain')!r} — refusing to act")
        tracer.note("entry-domain lock ok", host=loc.get("host"))

        # entry probe: a page that never booted (blank/stuck app) cannot be
        # rescued by scripts — reload and re-probe, bounded. The probe is the
        # reflex's own "this page is alive" expression, so it stays generic.
        probe = doc.get("entry_probe")
        if probe:
            retries = int(doc.get("entry_retries", 1))
            while True:
                deadline = time.time() + doc.get("entry_probe_timeout_ms", 25000) / 1000.0
                alive = False
                while time.time() < deadline:
                    try:
                        alive = bool(tab.eval_world(probe, timeout=10))
                    except (CDPError, CDPClosed):
                        alive = False
                    if alive:
                        break
                    time.sleep(0.5)
                if alive or retries <= 0 or tab._dead:
                    if not alive:
                        tracer.note("entry probe never passed; proceeding (phase scripts will judge)")
                    break
                retries -= 1
                tracer.note("entry probe failed; reloading the page", retries_left=retries)
                tab.navigate(doc["entry_url"])
                tab.steady(entry_steady, note="(entry-retry)")

        for ph in phases:
            ph_name = ph["name"]
            t_ph = time.time()
            if tab._dead:
                raise RunFail(ph_name, "own tab died")
            try:
                tracer.note("phase reflex", phase=ph_name)
                enforce_entry_domain(tab, doc, ph_name)
                # the pre-action target assertion runs FIRST: the exact intended
                # target is proven before the phase types or clicks anything
                ta = ph.get("target_assert") or "true"
                v = tab.eval_world(wrap_assert(ta, args), timeout=min(20.0, ph.get("timeout_ms", 90000) / 1000.0))
                why_ta = check_result(v, "target_assert")
                if why_ta:
                    raise RunFail(ph_name, f"pre-action target assertion failed: {why_ta}")
                v = tab.eval_world(wrap_script(ph["reflex_script"], args), timeout=ph.get("timeout_ms", 90000) / 1000.0)
                why = check_result(v, "reflex script")
            except (CDPError, CDPClosed) as e:
                msg = str(e)
                if ("navigated or closed" in msg or "Cannot find context" in msg
                        or "context was destroyed" in msg or "execution context" in msg.lower()):
                    # the action itself navigated (its own eval dies with the old
                    # document); wait for the new one and let verify judge
                    tracer.note("reflex eval ended by a navigation; waiting for the new document", phase=ph_name)
                    tab.wait_load_since(t_ph)
                    why = None
                    why_ta = None
                else:
                    why = f"reflex script error: {_head(str(e), 300)}"
                    why_ta = None
            if why:
                raise RunFail(ph_name, why)
            st = tab.steady(ph.get("steady_rule"), note=ph_name)
            tracer.note("steady", phase=ph_name, **st)
            enforce_entry_domain(tab, doc, ph_name)
            poll = ph.get("verify_poll") or {}
            deadline = time.time() + (poll.get("timeout_ms", 0) / 1000.0)
            interval = poll.get("interval_ms", 1500) / 1000.0
            why = "verify never ran"
            while True:
                try:
                    v = tab.eval_world(wrap_script(ph["verify_script"], args),
                                       timeout=min(EVAL_CHUNK_S, (ph.get("timeout_ms", 90000) / 1000.0) or EVAL_CHUNK_S))
                    why = check_result(v, "verify script")
                except (CDPError, CDPClosed) as e:
                    why = f"verify script error: {_head(str(e), 300)}"
                if not why or not poll or time.time() >= deadline:
                    break
                tracer.note("verify retry", phase=ph_name, **{"in": round((deadline - time.time()) * 1000)})
                time.sleep(max(0.1, interval))
            if why:
                raise RunFail(ph_name, why)
            elapsed = round((time.time() - t_ph) * 1000)
            phase_rows.append((ph_name, True, elapsed))
            tracer.note("phase ok", phase=ph_name, ms=elapsed)
            if doc.get("pause_after"):
                time.sleep(doc["pause_after"] / 1000.0)
    except RunFail as e:
        fail_phase, fail_why = e.phase, e.why
    except (CDPError, CDPClosed) as e:
        fail_phase, fail_why = "(connect)", f"CDP: {_head(str(e), 200)}"
    finally:
        # ALWAYS close our own tab — a leaked tab poisons the app's session for
        # every later run (measured: 5 WhatsApp tabs fighting over one session)
        try:
            if tab is not None:
                tab.close()
        except Exception:
            pass
        try:
            if cdp is not None:
                cdp.close()
        except Exception:
            pass
    elapsed_ms = round((time.time() - t_run) * 1000)
    ok = fail_phase is None

    # store updates + bounded audit (WAL db, shared with other agents)
    try:
        conn = store.connect()
        for ph_name, ph_ok, ph_ms in phase_rows:
            store.record_run(conn, name, ph_name, ph_ok, ph_ms, True)
        if fail_phase:
            store.record_run(conn, name, fail_phase, False, elapsed_ms, True)
        store.record_run(conn, name, "<run>", ok, elapsed_ms, True)
        if ok:
            store.mark_success(conn, doc["domain"], name, doc.get("_served_candidate", False))
        else:
            store.mark_failure(conn, doc["domain"], name, doc.get("_served_candidate", False))
        conn.close()
    except store.StoreError as e:
        print(f"store update failed: {e}", flush=True)

    line = (f"magi-reflex {name} ok {elapsed_ms}ms" if ok
            else f"magi-reflex {name} FAILED at {fail_phase}: {fail_why}")
    tracer.note("bus emit", line=line)
    tracer.close()
    emit_bus(name, line)
    if verbose:
        print(line, flush=True)
        print(f"trace: {trace_path}", flush=True)
    return {"ok": ok, "elapsed_ms": elapsed_ms, "phase": fail_phase, "why": fail_why,
            "line": line, "trace": str(trace_path)}
