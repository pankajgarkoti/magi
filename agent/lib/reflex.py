#!/usr/bin/env python3
"""magi-reflex: the muscle-memory layer — replayable browser flows with zero model calls.

A REFLEX = { name, description, entry_url, args_schema, phases[] }; one PHASE is one semantic
step on one page: reflex_script (does it), verify_script (proves it), steady (how long to wait
for the page to settle before verifying). Scripts are plain JS run over CDP against the agent
Chrome on 127.0.0.1:9222 — the same transport browser-bus uses; the connection, attach,
isolated-world and binding patterns are ported from agent/lib/browser-bus.mjs.

  magi-reflex ls                       reflexes in the live store (+ bundled, not yet seeded)
  magi-reflex show NAME               print one reflex definition
  magi-reflex run NAME [--args JSON] [--trace]
                                       open its OWN tab, navigate entry_url, run every phase
                                       (reflex -> steady -> verify), close the tab; one bus
                                       line out: ok (elapsed ms) or FAILED at <phase>: <why>
  magi-reflex dogma [-p wa,gh,hn] [-n N] [--save FILE]
                                       the steady-state calibration harness: measures
                                       net-quiesce / DOM-stable / fixed-delay on live pages
                                       after a navigation-like action and prints the numbers
                                       that pick the default (ran once, 24 Nov 2026; see
                                       docs/magi-reflex.md).

Store: ~/.local/state/magi/reflexes/<name>.json (live). Bundled reflexes ship with mAGI in
agent/reflexes/ and are seeded into the live store on first use. A synced copy of the
definition (not last_ok/fail_count) is written to the notes repo, orchestrator/reflexes/.

Scripts run in an isolated world ("magi-reflex"), like browser-bus probes: the whole DOM,
none of the page's JS globals. Scripts that need real input go through the __refAct binding
(wire name __refActBind) — the runner performs trusted CDP actions and resolves the promise:

  await __refAct({do: "click", x, y})        trusted mouse click at a point
  await __refAct({do: "type", text})         Input.insertText into the focused element
  await __refAct({do: "key", key: "Enter"})  a real key press (Enter/Escape/Tab/Backspace)
  await __refAct({do: "setFiles", selector, files})   put local files on an <input type=file>
  await __refAct({do: "log", text})          one line in the runner's trace

Every script also gets: args (from --args), $/$$ (querySelector helpers), sleep(ms) and
until(fn, ms) — until re-runs fn fresh on every attempt, never caching elements between
attempts (the "menu uid flicker" lesson: menus re-render and stale references go dead).

The replay path is provably model-free: --trace logs every CDP frame the process sends or
receives, and the process does nothing else — one socket to 127.0.0.1:9222, local file reads,
and the single orch-bus line at the end.

Never touches a tab it did not open (its own target only, closed on every exit path). Never
types credentials or solves challenges; such phases should end with an ASK OWNER handoff.
"""
import base64
import hashlib
import json
import os
import pathlib
import queue
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

HOME = pathlib.Path.home()
PORT = int(os.environ.get("MAGI_REFLEX_PORT", "9222"))
WORLD = "magi-reflex"
STORE = pathlib.Path(os.environ.get("MAGI_REFLEX_STORE", HOME / ".local/state/magi/reflexes"))
BUNDLED = pathlib.Path(__file__).resolve().parent.parent / "reflexes"
SYNC = pathlib.Path(os.environ.get("MAGI_REFLEX_SYNC_DIR", HOME / "Desktop/notes/orchestrator/reflexes"))
ACTIVE_PORT_FILES = ["chromium-agent", "chromium", "google-chrome", "google-chrome-beta"]
REFLEX_EVAL_TIMEOUT_MS = int(os.environ.get("MAGI_REFLEX_SCRIPT_MS", "120000"))
VERIFY_EVAL_TIMEOUT_MS = int(os.environ.get("MAGI_REFLEX_VERIFY_MS", "185000"))
BUS_TEXT = 280

# The steady rule the dogma filter measured best (docs/magi-reflex.md has the numbers):
# network quiesce — no new requests for quiet_ms — beat DOM-stable (which never fired on
# WhatsApp's never-quiet DOM) and beat every fixed delay that was actually safe. Phases may
# override with their own "steady" object.
DEFAULT_STEADY = {"type": "net", "quiet_ms": 700, "timeout_ms": 15000}

TRACE = []  # list of emit(line) functions; empty means no trace


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def trace(*a):
    for fn in TRACE:
        fn(" ".join(str(x) for x in a))


def add_trace(to=sys.stderr):
    def emit(line):
        print("%s.%03d %s" % (time.strftime("%H:%M:%S"), int(time.time() * 1000) % 1000, line), file=to, flush=True)
    TRACE.append(emit)


def trunc(obj, n=240):
    s = json.dumps(obj) if not isinstance(obj, str) else obj
    return s if len(s) <= n else s[: n - 1] + f"…<+{len(s) - n + 1} chars>"


# ---------------- websocket (stdlib only; CDP needs no extensions) ----------------
class WS:
    def __init__(self, host, port, path):
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(None)  # the connect timeout must not limit an idle CDP socket
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("ws handshake eof")
            buf += chunk
        head, rest = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(f"ws handshake refused: {head[:120]!r}")
        accept = None
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"sec-websocket-accept:"):
                accept = line.split(b":", 1)[1].strip()
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if accept != want:
            raise ConnectionError("ws handshake: bad accept key")
        self.buf = rest
        self.wlock = threading.Lock()
        self.closed = False

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(262144)
            if not chunk:
                raise ConnectionError("ws eof")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """Return (opcode, payload) of the next complete (possibly fragmented) message."""
        op, data = None, b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, ln = b2 & 0x80, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(ln) if ln else b""
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode != 0:  # 0 = continuation of a fragmented message
                op = opcode
            data += payload
            if fin:
                return op, data

    def send(self, data):
        if isinstance(data, str):
            data = data.encode()
        ln = len(data)
        head = bytes([0x81])  # FIN + text
        if ln < 126:
            head += bytes([0x80 | ln])
        elif ln < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", ln)
        mask = os.urandom(4)
        with self.wlock:
            self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                with self.wlock:
                    self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
                self.sock.close()
            except OSError:
                pass


def resolve_endpoint():
    """The browser endpoint, browser-bus style: fixed :9222 first, DevToolsActivePort second."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
            return json.loads(r.read())["webSocketDebuggerUrl"], "port"
    except Exception:
        pass
    for d in ACTIVE_PORT_FILES:
        try:
            port, path = (HOME / ".config" / d / "DevToolsActivePort").read_text().split("\n")[:2]
            if port and path:
                return f"ws://127.0.0.1:{port}{path}", "autoconnect"
        except Exception:
            pass
    raise SystemExit(f"magi-reflex: no browser debugging endpoint (port {PORT}); start it with chrome-debug")


class CDP:
    def __init__(self, url):
        host, rest = url[5:].split("/", 1)
        host, _, hport = host.partition(":")
        self.ws = WS(host, int(hport or 80), "/" + rest)
        self.id = 0
        self.pending = {}
        self.handlers = []
        self.waiters = []
        self.lock = threading.Lock()
        self.dead = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()

    def _fail_all(self, why):
        self.dead.set()
        with self.lock:
            pending, self.pending = self.pending, {}
        for ev, slot in pending.values():
            slot.append(ConnectionError(why))
            ev.set()
        for w in list(self.waiters):
            w["ev"].set()

    def _pump(self):
        try:
            while True:
                op, data = self.ws.recv()
                if op == 8:  # close
                    self._fail_all("browser closed the socket")
                    return
                if op == 9:  # ping
                    with self.ws.wlock:
                        self.ws.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                    continue
                if op != 1:
                    continue
                m = json.loads(data)
                if "id" in m:
                    with self.lock:
                        p = self.pending.pop(m["id"], None)
                    if p:
                        ev, slot = p
                        slot.append(m["result"] if "result" in m else ConnectionError(m.get("error", {}).get("message", "cdp error")))
                        ev.set()
                elif "method" in m:
                    trace("EVT", m["method"], trunc(m.get("params", {}), 160))
                    for w in list(self.waiters):
                        if w["method"] == m["method"] and w.get("session") in (None, m.get("sessionId")):
                            w["hit"] = m
                            w["ev"].set()
                    for h in list(self.handlers):
                        try:
                            h(m)
                        except Exception as e:
                            log("event handler error:", e)
        except Exception as e:
            self._fail_all(f"cdp socket: {e}")

    def on(self, handler):
        self.handlers.append(handler)

    def wait_event(self, session, method, timeout):
        w = {"session": session, "method": method, "ev": threading.Event(), "hit": None}
        self.waiters.append(w)
        try:
            w["ev"].wait(timeout)
            return w["hit"]
        finally:
            try:
                self.waiters.remove(w)
            except ValueError:
                pass

    def send(self, method, params=None, session=None, timeout=30.0):
        if self.dead.is_set():
            raise ConnectionError("cdp connection is down")
        with self.lock:
            self.id += 1
            mid = self.id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        trace("→", method, trunc(msg["params"]))
        ev, slot = threading.Event(), []
        with self.lock:
            self.pending[mid] = (ev, slot)
        self.ws.send(json.dumps(msg))
        if not ev.wait(timeout):
            with self.lock:
                self.pending.pop(mid, None)
            raise TimeoutError(f"cdp timeout: {method}")
        if isinstance(slot[0], ConnectionError):
            raise ConnectionError(f"{method}: {slot[0].args[0] if slot[0].args else slot[0]}")
        trace("←", method, trunc(slot[0]))
        return slot[0]


# ---------------- one tab that belongs to us ----------------
class TabError(Exception):
    pass


class Tab:
    """A target the runner created itself. Nothing outside this target is ever touched."""

    def __init__(self, cdp, url="about:blank"):
        self.cdp = cdp
        r = cdp.send("Target.createTarget", {"url": url})
        self.target_id = r["targetId"]
        self.session = cdp.send("Target.attachToTarget", {"targetId": self.target_id, "flatten": True})["sessionId"]
        self.world_id = None
        self.last_req_ms = 0
        self.actions = queue.Queue()
        cdp.send("Runtime.addBinding", {"name": "__refActBind", "executionContextName": WORLD}, self.session)
        for d in ("Page.enable", "Runtime.enable", "Network.enable", "DOM.enable"):
            cdp.send(d, {}, self.session)
        cdp.on(self._on_event)
        threading.Thread(target=self._action_worker, daemon=True).start()
        trace("tab", self.target_id, "session", self.session)

    def _on_event(self, m):
        if m.get("sessionId") != self.session:
            return
        p = m.get("params", {})
        if m["method"] == "Network.requestWillBeSent":
            self.last_req_ms = int(time.time() * 1000)
        elif m["method"] == "Runtime.executionContextsCleared":
            self.world_id = None
        elif m["method"] == "Runtime.bindingCalled" and p.get("name") == "__refActBind":
            self.actions.put(p.get("payload", ""))

    def send(self, method, params=None, timeout=30.0):
        return self.cdp.send(method, params, self.session, timeout)

    def navigate(self, url, timeout=45):
        self.world_id = None
        r = self.send("Page.navigate", {"url": url}, timeout=30)
        if r.get("errorText"):
            raise TabError(f"navigate {url}: {r['errorText']}")
        self.cdp.wait_event(self.session, "Page.loadEventFired", timeout)
        self.last_req_ms = int(time.time() * 1000)
        trace("navigated", url)

    def world(self, extra_setup=""):
        """The isolated world, browser-bus style, plus the __refAct promise plumbing."""
        if self.world_id:
            return self.world_id
        r = self.send("Page.createIsolatedWorld", {"frameId": self.target_id, "worldName": WORLD})
        self.world_id = r["executionContextId"]
        helper = """(() => {
          const w = (window.__mr = window.__mr || {seq: 0, pend: {}, muts: [], mutTs: 0});
          window.__refAct = (o) => new Promise((resolve, reject) => {
            const id = ++w.seq;
            w.pend[id] = {resolve, reject};
            try { __refActBind(JSON.stringify(Object.assign({id}, o))); } catch (e) { delete w.pend[id]; reject(e); }
          });
          new MutationObserver(() => { const t = Date.now(); w.mutTs = t; if (w.muts.length < 20000) w.muts.push(t); })
            .observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
          w.mutTs = Date.now();
          return "ok";
        })()"""
        got = self.evaluate(helper + extra_setup + "; 'ok'", timeout=10)
        if got != "ok":
            raise TabError(f"world helper failed: {got!r}")
        return self.world_id

    def evaluate(self, expression, timeout=30.0, world_id=None):
        """Evaluate in the isolated world; returns the by-value result or raises TabError."""
        wid = world_id if world_id is not None else self.world()
        r = self.send("Runtime.evaluate",
                      {"expression": expression, "contextId": wid, "returnByValue": True, "awaitPromise": True},
                      timeout=timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise TabError((d.get("exception") or {}).get("description") or d.get("text") or "script error")
        return (r.get("result") or {}).get("value")

    def click(self, x, y):
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})

    def press(self, key):
        base = {"Enter": ("Enter", "Enter", 13, "\r"), "Escape": ("Escape", "Escape", 27, None),
                "Tab": ("Tab", "Tab", 9, None), "Backspace": ("Backspace", "Backspace", 8, None)}[key]
        for t in ("keyDown", "keyUp"):
            p = {"type": t, "key": base[0], "code": base[1], "windowsVirtualKeyCode": base[2], "nativeVirtualKeyCode": base[2]}
            if t == "keyDown" and base[3]:
                p["text"] = base[3]
            self.send("Input.dispatchKeyEvent", p)

    def _resolve(self, id_, result, is_error=False):
        call = f"p.reject(new Error({json.dumps(str(result))}))" if is_error else f"p.resolve({json.dumps(result)})"
        self.evaluate(f"(function(){{var p=window.__mr.pend[{id_}];if(!p)return 1;"
                      f"delete window.__mr.pend[{id_}];{call};return 1;}})()", timeout=10)

    def _action_worker(self):
        while True:
            payload = self.actions.get()
            if payload is None:
                return
            try:
                o = json.loads(payload)
                id_ = o.pop("id")
                do = o.pop("do", "")
                trace("act", do, trunc(o, 160))
                if do == "click":
                    self.click(o["x"], o["y"])
                    self._resolve(id_, {"ok": True})
                elif do == "type":
                    self.send("Input.insertText", {"text": str(o.get("text", ""))})
                    self._resolve(id_, {"ok": True})
                elif do == "key":
                    self.press(o.get("key", "Enter"))
                    self._resolve(id_, {"ok": True})
                elif do == "setFiles":
                    files = [str(f) for f in o.get("files", [])]
                    for f in files:
                        if not os.path.isfile(f):
                            raise TabError(f"no such file: {f}")
                    doc = self.send("DOM.getDocument", {})
                    node = self.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": o["selector"]})
                    if not node.get("nodeId"):
                        raise TabError(f"selector not found: {o['selector']}")
                    self.send("DOM.setFileInputFiles", {"files": files, "nodeId": node["nodeId"]})
                    self._resolve(id_, {"ok": True, "files": len(files)})
                elif do == "log":
                    trace("script:", str(o.get("text", ""))[:300])
                    self._resolve(id_, {"ok": True})
                else:
                    raise TabError(f"unknown action: {do}")
            except Exception as e:
                try:
                    self._resolve(id_, e, is_error=True)
                except Exception:
                    trace("resolve failed:", e)

    def close(self):
        trace("closing tab", self.target_id)
        try:
            self.cdp.send("Target.closeTarget", {"targetId": self.target_id}, timeout=10)
        except Exception as e:
            log("tab close failed:", e)


# ---------------- the run loop ----------------
SCRIPT_PRELUDE = """"use strict";
const args = %s;
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// until(): poll fn until it returns truthy. fn is re-run fresh on EVERY attempt — never hold
// an element across awaits; menus re-render and stale references go dead (the 24 Nov lesson).
const until = async (fn, ms, step) => { const end = Date.now() + (ms || 5000); for (;;) {
  try { const v = fn(); if (v) return v; } catch (e) {}
  if (Date.now() >= end) return null; await sleep(step || 150); } };
"""


def wrap(script, args):
    return "(async () => {\n" + (SCRIPT_PRELUDE % json.dumps(args)) + script + "\n})()"


def judge(v):
    """Verify results: {ok: false, why} fails with why; anything else by truthiness."""
    if isinstance(v, dict) and "ok" in v:
        return bool(v["ok"]), str(v.get("why") or ("" if v["ok"] else "verify returned ok:false"))
    return bool(v), "" if v else "verify returned falsy"


def steady_wait(tab, cfg):
    """Wait for the page to settle after a phase's action. Capped, never fatal: verify gates."""
    cfg = cfg or {}
    kind = cfg.get("type", DEFAULT_STEADY["type"])
    quiet = cfg.get("quiet_ms", DEFAULT_STEADY["quiet_ms"])
    cap = cfg.get("timeout_ms", DEFAULT_STEADY["timeout_ms"])
    t0 = int(time.time() * 1000)
    if kind == "fixed":
        time.sleep(cfg.get("ms", 2000) / 1000)
        trace(f"steady fixed {cfg.get('ms', 2000)}ms")
        return
    if kind == "net":
        # fire at last-request + quiet, never before wait-start + quiet
        while True:
            now = int(time.time() * 1000)
            if now > t0 + cap:
                trace(f"steady net capped at {cap}ms (still requesting); proceeding")
                return
            if now >= max(tab.last_req_ms, t0) + quiet:
                trace(f"steady net: quiet {quiet}ms after {max(tab.last_req_ms, t0) - t0}ms of requests")
                return
            time.sleep(0.05)
    if kind == "dom":
        while True:
            now = int(time.time() * 1000)
            if now > t0 + cap:
                trace(f"steady dom capped at {cap}ms (still mutating); proceeding")
                return
            try:
                mut = tab.evaluate("window.__mr ? window.__mr.mutTs : 0", timeout=10) or 0
            except TabError:
                mut = 0
            if mut and now - max(mut, t0) >= quiet:
                trace(f"steady dom: quiet {quiet}ms after {now - t0}ms")
                return
            time.sleep(0.1)
    raise TabError(f"unknown steady type: {kind}")


def run_reflex(name, args):
    """Returns (ok, line). One bus line out, the tab always closed, the store always updated."""
    reflex = load_reflex(name)
    if reflex is None:
        return False, f"magi-reflex {name} FAILED: no such reflex"
    phases = reflex.get("phases") or []
    if not phases:
        return False, f"magi-reflex {name} FAILED: reflex has no phases"
    t0 = time.time()
    url, _ = resolve_endpoint()
    trace("connecting", url)
    cdp = CDP(url)
    tab = None
    try:
        tab = Tab(cdp)
        if reflex.get("entry_url"):
            tab.navigate(reflex["entry_url"])
        for phase in phases:
            pname = phase.get("name", "?")
            log(f"phase {pname}")
            trace("phase", pname, "start")
            wid = tab.world()
            res = tab.evaluate(wrap(phase["reflex_script"], args),
                               timeout=(phase.get("timeout_ms", REFLEX_EVAL_TIMEOUT_MS) + 10000) / 1000, world_id=wid)
            trace("phase", pname, "reflex ->", trunc(res, 200))
            steady_wait(tab, phase.get("steady"))
            vres = tab.evaluate(wrap(phase["verify_script"], args),
                                timeout=(phase.get("verify_timeout_ms", VERIFY_EVAL_TIMEOUT_MS) + 10000) / 1000, world_id=wid)
            ok, why = judge(vres)
            trace("phase", pname, "verify ->", trunc(vres, 200))
            if not ok:
                raise TabError(f"verify: {why or 'failed'}")
            phase["_ok"] = True
        elapsed = int((time.time() - t0) * 1000)
        record(reflex, ok=True)
        return True, f"magi-reflex {name} ok in {elapsed} ms ({len(phases)} phases)"
    except (TabError, TimeoutError, ConnectionError) as e:
        why = str(e).split("\n")[0][:200]
        failed = next((p.get("name", "?") for p in phases if not p.get("_ok")), "?")
        record(reflex, ok=False)
        return False, f"magi-reflex {name} FAILED at {failed}: {why}"
    finally:
        if tab:
            tab.close()


# ---------------- store ----------------
def definition(reflex):
    """The durable part of a reflex: volatile keys (last_ok, fail_count, _ok) never sync."""
    out = {k: reflex.get(k) for k in ("name", "description", "entry_url", "args_schema") if reflex.get(k) is not None}
    out["phases"] = [{k: v for k, v in p.items() if not k.startswith("_") and k not in ("last_ok", "fail_count")}
                     for p in reflex.get("phases") or []]
    return out


def record(reflex, ok):
    """Update last_ok/fail_count in the live store; sync the definition to the notes repo."""
    try:
        p = STORE / f"{reflex['name']}.json"
        live = json.loads(p.read_text()) if p.exists() else reflex
        if ok:
            live["last_ok"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        live["fail_count"] = 0 if ok else int(live.get("fail_count") or 0) + 1
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(live, indent=2) + "\n")
        tmp.replace(p)
        trace("store updated", str(p))
        # synced copy: the definition only, so the notes repo stays quiet run-to-run
        if SYNC.parent.exists():
            sp = SYNC / f"{reflex['name']}.json"
            want = json.dumps(definition(live), indent=2) + "\n"
            have = json.dumps(definition(json.loads(sp.read_text())), indent=2) + "\n" if sp.exists() else None
            if want != have:
                SYNC.mkdir(parents=True, exist_ok=True)
                sp.write_text(want)
                trace("synced definition to", str(sp))
    except Exception as e:
        log("store update failed:", e)


def load_reflex(name):
    p = STORE / f"{name}.json"
    if p.exists():
        return json.loads(p.read_text())
    b = BUNDLED / f"{name}.json"
    if b.exists():  # seed the live store from the bundled copy on first use
        reflex = json.loads(b.read_text())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(reflex, indent=2) + "\n")
        log(f"seeded {name} into the live store from {b}")
        return reflex
    return None


def bus_emit(text):
    exe = shutil.which("orch-bus") or str(pathlib.Path(__file__).resolve().parent.parent / "bin" / "orch-bus")
    try:
        r = subprocess.run([exe, "emit", "magi-reflex", text[:BUS_TEXT]], timeout=15)
        trace("bus emit:", text[:BUS_TEXT], "->", r.returncode)
    except Exception as e:
        log("orch-bus emit failed:", e)


# ---------------- dogma: the steady-state calibration harness ----------------
# Instrumented trials: one fresh own tab per trial. Every request timestamp (CDP events) and
# every mutation-batch timestamp (an in-page observer) is collected for a horizon after an
# action, plus the moment a post-action marker first appears (a 50 ms in-page poll). All three
# candidate detectors are then computed offline from the same event stream:
#   net-quiesce(N)  fires at the first T with no request in (T-N, T]
#   dom-stable(N)   the same on the mutation stream
#   fixed(D)        fires at t0 + D, whatever the page is doing
# A config is "safe" on a trial when it fires not before the marker (30 ms clock tolerance)
# and inside the horizon ("capped" otherwise).
DOGMA_PAGES = {
    "wa": {
        "url": "https://web.whatsapp.com/",
        "marker": "!!document.querySelector('#pane-side [data-testid=\"message-yourself-row\"]')",
        "act": {
            "click": "#pane-side [data-testid='message-yourself-row']",
            "marker": "!!(document.querySelector('#main footer [contenteditable=\"true\"]') || /\\(You\\)/.test((document.querySelector('#main header') || {}).innerText || ''))",
        },
    },
    "gh": {
        "url": "https://github.com/",
        "marker": "!!(document.querySelector('main') || document.querySelector('#dashboard') || document.querySelector('h1'))",
        "act": {
            "click": "header details summary",
            "marker": "!!document.querySelector('header details[open]')",
        },
    },
    "hn": {
        "url": "https://news.ycombinator.com/",
        "marker": "document.querySelectorAll('.titleline').length > 5",
    },
}
DOGMA_CONFIGS = [("net", 300), ("net", 500), ("net", 700), ("net", 1000),
                 ("dom", 400), ("dom", 700), ("dom", 1000),
                 ("fixed", 1500), ("fixed", 3000), ("fixed", 5000)]
DOGMA_HORIZON_MS = 25000
MARKER_SETUP = """
;(function(){
  var w = window.__mr;
  setInterval(function(){ if (!w.markerTs && (%MARKER%)) w.markerTs = Date.now(); }, 50);
  setInterval(function(){ if (w.watch2 && !w.marker2Ts && (%MARKER2%)) w.marker2Ts = Date.now(); }, 50);
})();
"""
LOAD_READ = ("(function(){var w=window.__mr;return {mut:w.muts.slice(),bridge:Date.now(),"
             "markerTs:w.markerTs};})()")
# The in-page action: click the selector, poll the post-action marker, retry the whole
# snapshot (fresh query each attempt) up to 3 times — the 24 Nov fumble lesson, applied.
ACT_RETRY = """
;(async function(){
  var w = window.__mr; w.watch2 = true; w.actT0 = Date.now();
  var m2 = new Function('return (' + %MARKER2% + ')');
  var done = function(){ try { return m2(); } catch (e) { return false; } };
  for (var attempt = 0; attempt < 3 && !done(); attempt++) {
    var el = null;
    for (var i = 0; i < 60 && !el; i++) {
      var cand = document.querySelector(%SEL%);
      if (cand) { var r0 = cand.getBoundingClientRect(); if (r0.width) el = cand; }
      if (!el) await new Promise(function(s){ setTimeout(s, 150); });
    }
    if (!el) break;
    el.scrollIntoView({block: 'center'});
    await new Promise(function(s){ setTimeout(s, 250); });
    var r = el.getBoundingClientRect();
    await __refAct({do: 'click', x: r.left + r.width/2, y: r.top + r.height/2});
    for (var j = 0; j < 33 && !done(); j++) await new Promise(function(s){ setTimeout(s, 150); });
  }
  w.actDone = done();
  return {actT0: w.actT0, endTs: Date.now(), done: w.actDone};
})()
"""
ACT_READ = ("(function(){var w=window.__mr;return {mut:w.muts.filter(function(m){return m>=(w.actT0||0)}),"
            "bridge:Date.now(),markerTs:w.marker2Ts,done:w.actDone};})()")


def _steady_at(kind, n, t0, events):
    """First moment T >= t0 with no event in (T-n, T] (events time-ordered)."""
    if kind == "fixed":
        return t0 + n
    t = t0 + n
    for e in events:
        if e > t - n:
            t = e + n
    return t


def dogma(pages, trials, save=None):
    url, _ = resolve_endpoint()
    log("dogma: measuring steady-state detectors on", ",".join(pages), f"({trials} trials each)")
    cdp = CDP(url)
    data = []
    for key in pages:
        page = DOGMA_PAGES.get(key)
        if not page:
            log(f"unknown page {key}; skipping")
            continue
        for i in range(trials):
            tab = None
            try:
                tab = Tab(cdp)
                setup = MARKER_SETUP.replace("%MARKER%", page["marker"]).replace(
                    "%MARKER2%", (page.get("act") or {}).get("marker", "false"))
                tab.navigate(page["url"])
                tab.world(setup)
                t0 = int(time.time() * 1000)
                tab.last_req_ms = t0
                reqs = []

                def on_evt(m, tab=tab, reqs=reqs, t0=t0):
                    if m.get("sessionId") == tab.session and m["method"] == "Network.requestWillBeSent":
                        reqs.append(int(time.time() * 1000))
                cdp.on(on_evt)
                time.sleep(DOGMA_HORIZON_MS / 1000)
                load = tab.evaluate(LOAD_READ, timeout=15) or {}
                py_bridge = int(time.time() * 1000)
                act = None
                if page.get("act") and load.get("markerTs"):
                    try:
                        expr = (ACT_RETRY
                                .replace("%MARKER2%", json.dumps(page["act"]["marker"]))
                                .replace("%SEL%", json.dumps(page["act"]["click"])))
                        r = tab.evaluate(expr, timeout=90) or {}
                        py_end = int(time.time() * 1000)
                        t0a = py_end - (int(r.get("endTs") or 0) - int(r.get("actT0") or 0))
                        if r.get("done"):
                            time.sleep(DOGMA_HORIZON_MS / 1000)
                            a = tab.evaluate(ACT_READ, timeout=15) or {}
                            act = {"t0": t0a, "reqs": [q for q in reqs if q >= t0a - 1500],
                                   "muts": a.get("mut") or [], "markerTs": a.get("markerTs") or 0,
                                   "bridge": a.get("bridge") or 0,
                                   "py_bridge": int(time.time() * 1000)}
                    except TabError as e:
                        log("act failed:", str(e).split("\n")[0])
                data.append({"page": key, "trial": i, "t0": t0,
                             "reqs": [q for q in reqs if q >= t0], "muts": load.get("mut") or [],
                             "markerTs": load.get("markerTs") or 0, "bridge": load.get("bridge") or 0,
                             "py_bridge": py_bridge, "act": act})
                log(f"  {key} #{i + 1}: load marker {'ok' if load.get('markerTs') else 'MISSING'}"
                    + (f", act {'ok' if act and act['markerTs'] else ('marker-missing' if act else 'no effect')}"
                       if page.get("act") else ""))
            except Exception as e:
                log(f"  {key} #{i + 1} failed: {str(e).split(chr(10))[0]}")
            finally:
                if tab:
                    tab.close()
    if save:
        pathlib.Path(save).write_text(json.dumps(data, indent=1))
        log("raw trials saved to", save)
    print(_dogma_report(data, "load"))
    print(_dogma_report(data, "act"))
    return data


def _dogma_rows(data, win):
    """One row per (trial, config): when the config would have fired vs. when content was real."""
    rows = []
    for d in data:
        w = d if win == "load" else d.get("act")
        if not w or not w.get("markerTs"):
            continue
        off = w.get("py_bridge", 0) - w.get("bridge", 0)  # page clock -> runner clock
        marker = w["markerTs"] + off
        muts = [m + off for m in w.get("muts", [])]
        for kind, n in DOGMA_CONFIGS:
            sat = _steady_at(kind, n, w["t0"], w["reqs"] if kind == "net" else muts)
            rows.append({"page": d["page"], "kind": f"{kind}:{n}", "latency": sat - w["t0"],
                         "lag": sat - marker, "safe": sat >= marker - 30,
                         "capped": sat > w["t0"] + DOGMA_HORIZON_MS})
    return rows


def _dogma_report(data, win):
    rows = _dogma_rows(data, win)
    head = f"\n== steady-state detector measurement — {win} window (post-{'load' if win == 'load' else 'action'}) =="
    if not rows:
        return head + "\n(no usable trials)"
    out = [head,
           f"{'config':<12}{'safe':>6}{'capped':>8}{'n':>4}{'med latency':>13}{'med lag':>9}   safe-by-page"]
    for kind, n in DOGMA_CONFIGS:
        tag = f"{kind}:{n}"
        rr = [r for r in rows if r["kind"] == tag]
        if not rr:
            continue
        good = [r for r in rr if r["safe"] and not r["capped"]]
        lat = sorted(r["latency"] for r in rr)
        lag = sorted(r["lag"] for r in rr)
        bypage = " ".join(f"{p}:{sum(1 for r in rr if r['page'] == p and r['safe'] and not r['capped'])}"
                          f"/{sum(1 for r in rr if r['page'] == p)}" for p in sorted({r['page'] for r in rr}))
        out.append(f"{tag:<12}{len(good):>5}/{len(rr):<2}{sum(1 for r in rr if r['capped']):>8}{len(rr):>4}"
                   f"{lat[len(lat) // 2]:>11}ms{lag[len(lag) // 2]:>7}ms   {bypage}")
    return "\n".join(out)


# ---------------- CLI ----------------
def cmd_ls():
    print(f"live store: {STORE}")
    seen = set()
    if STORE.exists():
        for p in sorted(STORE.glob("*.json")):
            try:
                r = json.loads(p.read_text())
            except Exception as e:
                print(f"{p.name}: unreadable ({e})")
                continue
            seen.add(r.get("name", p.stem))
            print(f"{r.get('name', p.stem):<16} {len(r.get('phases') or [])} phases  "
                  f"last_ok={r.get('last_ok') or '-':<20} fails={r.get('fail_count') or 0}  {r.get('description', '')[:60]}")
    if BUNDLED.exists():
        for p in sorted(BUNDLED.glob("*.json")):
            name = p.stem
            if name in seen:
                continue
            try:
                r = json.loads(p.read_text())
                print(f"{name:<16} {len(r.get('phases') or [])} phases  (bundled; seeds on first use)  {r.get('description', '')[:60]}")
            except Exception:
                pass


def cmd_show(name):
    r = load_reflex(name)
    if not r:
        raise SystemExit(f"magi-reflex: no reflex {name}")
    print(json.dumps(r, indent=2))


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="magi-reflex", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ls", help="list reflexes")
    p = sub.add_parser("show", help="print one reflex")
    p.add_argument("name")
    p = sub.add_parser("run", help="run a reflex in its own tab")
    p.add_argument("name")
    p.add_argument("--args", default="{}", help="JSON object passed to every script")
    p.add_argument("--trace", action="store_true", help="log every CDP frame (the zero-model-calls proof)")
    d = sub.add_parser("dogma", help="measure steady-state detectors on live pages")
    d.add_argument("-p", "--pages", default="wa,gh,hn")
    d.add_argument("-n", "--trials", type=int, default=3)
    d.add_argument("--save", default=None, help="write raw trial JSON here")
    a = ap.parse_args()

    if a.cmd in (None, "ls"):
        return cmd_ls()
    if a.cmd == "show":
        return cmd_show(a.name)
    if a.cmd == "dogma":
        return dogma([x.strip() for x in a.pages.split(",") if x.strip()], a.trials, a.save)
    if a.cmd == "run":
        if a.trace:
            add_trace()
            trace("magi-reflex run", a.name, "args", trunc(a.args))
            trace("replay path: this process opens one websocket to 127.0.0.1:%d, evaluates the"
                  " scripts below, reads/writes local files, and emits one bus line at the end."
                  " Nothing else happens; every frame is logged here." % PORT)
        try:
            args = json.loads(a.args)
        except Exception as e:
            raise SystemExit(f"magi-reflex: --args is not JSON: {e}")
        ok, line = run_reflex(a.name, args)
        log(line)
        bus_emit(line)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
