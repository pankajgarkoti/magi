#!/usr/bin/env node
// browser-bus: run small "probe" scripts inside browser tabs (the agent Chromium that chrome-debug
// starts on 127.0.0.1:9222) and turn what they see into orch-bus events. Talks to the browser over
// CDP only; opens no listener of its own.
//
//   browser-bus daemon                 run (systemd user unit browser-bus.service)
//   browser-bus status                 connection, attached tabs, recent events
//   browser-bus probe ls               probes on disk and where they are running
//   browser-bus probe add FILE [NAME]  install a probe (.js or .json) + register it in the orch ledger
//   browser-bus probe examples         the example probes that ship with MAGI
//   browser-bus probe rm NAME          remove it (+ end its ledger watch)
//   browser-bus probe test NAME        ask the daemon for the probe's live status in every matching tab
//   browser-bus send-self "[orch] …"   type one "[orch] …" message into the WhatsApp self-chat (orch-bus alerts)
//
// Probe = ~/.config/browser-bus/probes/NAME.js with a header:
//   // @match   https://example.com/*        glob, or /regex/
//   // @source  test-probe                   allowed bus source(s), comma separated; "chat-*" = prefix
//   // @purpose one line for the ledger
// The rest of the file is a function body run in an isolated world of the page (shares the DOM, not
// the page's JS globals). It gets `emit({text, source?, key?, data?})`, `log(...)` and `ready(fn)`,
// and may return a cleanup function or {stop(), status()}.
// A NAME.json probe has the same fields: {match, sources, purpose, js | js_file}.
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";

const HOME = os.homedir();
const CONF = process.env.BROWSER_BUS_PROBES || path.join(HOME, ".config/browser-bus/probes");
const STATE = path.join(HOME, ".local/state/browser-bus");
const CTL = path.join(STATE, "ctl");
const STATUS = path.join(STATE, "status.json");
const SIDECAR = path.join(STATE, "events.jsonl");
const which = (cmd) => {
  for (const d of [path.join(HOME, ".local/bin"), ...(process.env.PATH || "").split(":"), "/usr/bin"]) {
    const f = path.join(d, cmd);
    try { fs.accessSync(f, fs.constants.X_OK); return f; } catch {}
  }
  return path.join(path.dirname(new URL(import.meta.url).pathname), "../bin", cmd);
};
const ORCH_BUS = which("orch-bus");
const ORCH = which("orch");
const EXAMPLES = path.join(path.dirname(new URL(import.meta.url).pathname), "../browser-bus/examples");
const PORT = Number(process.env.BROWSER_BUS_PORT || 9222);
const ACTIVE_PORT_FILES = (process.env.BROWSER_BUS_ACTIVE_PORT_FILES ||
  ["chromium-agent", "chromium", "google-chrome", "google-chrome-beta"].map((d) => path.join(HOME, ".config", d, "DevToolsActivePort")).join(":")).split(":");
const WORLD = "browser-bus";
const MAX_PAYLOAD = 8192, BUS_TEXT = 280;
const RATE_BURST = 30, RATE_REFILL_MS = 4000; // per probe: 30 at once, then 15/min
const WARN_AFTER_MS = 60_000;

const log = (...a) => console.log(new Date().toTimeString().slice(0, 8), ...a);
// The daemon's own notices (WARN, reconnected): internal events.
const bus = (source, text, key) => {
  const args = ["emit", source, text];
  if (key) args.push("--key", key);
  const r = spawnSync(ORCH_BUS, args, { timeout: 10_000 });
  if (r.status !== 0) log("orch-bus emit failed", r.status, String(r.stderr || "").slice(0, 200));
};
// Probe events: text written by other people. Sent with the probe token (only browser-bus may use
// wa-*/tg-* sources) and the FULL text on stdin, so orch-bus's guard scans everything before it caps,
// frames and, if needed, quarantines it. Returns the stored event (trust, risk, framed text).
let PROBE_TOKEN = null;
const probeToken = () => {
  if (PROBE_TOKEN) return PROBE_TOKEN;
  const r = spawnSync(ORCH_BUS, ["token"], { timeout: 10_000 });
  try { PROBE_TOKEN = fs.readFileSync(String(r.stdout).trim(), "utf8").trim(); } catch { PROBE_TOKEN = null; }
  return PROBE_TOKEN;
};
const probeBus = (source, text, key) => {
  const args = ["emit", source, "--stdin", "--probe", "--json"];
  if (key) args.push("--key", key);
  const r = spawnSync(ORCH_BUS, args, { input: text, timeout: 20_000, env: { ...process.env, ORCH_BUS_PROBE_TOKEN: probeToken() || "" } });
  if (r.status !== 0) log("orch-bus emit failed", r.status, String(r.stderr || "").slice(0, 200));
  try { return JSON.parse(String(r.stdout).trim().split("\n").pop()); } catch { return null; }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------- probes ----------------
function globToRegex(g) {
  if (g.length > 2 && g.startsWith("/") && g.endsWith("/")) return g.slice(1, -1);
  return "^" + g.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$";
}

function parseProbe(file) {
  const raw = fs.readFileSync(file, "utf8");
  let p;
  if (file.endsWith(".json")) {
    const j = JSON.parse(raw);
    const js = j.js ?? fs.readFileSync(path.resolve(path.dirname(file), j.js_file), "utf8");
    p = { name: j.name, match: j.match ?? j.url_match, sources: [].concat(j.sources ?? j.source ?? []), purpose: j.purpose || "", js };
  } else {
    const meta = {};
    for (const m of raw.matchAll(/^\s*\/\/\s*@(\w+)\s+(.+?)\s*$/gm)) meta[m[1]] = meta[m[1]] ? meta[m[1]] + "," + m[2] : m[2];
    p = { name: meta.name, match: meta.match, sources: (meta.source || "").split(",").map((s) => s.trim()).filter(Boolean), purpose: meta.purpose || "", js: raw };
  }
  p.name = p.name || path.basename(file).replace(/\.(js|json)$/, "");
  if (!/^[\w.-]+$/.test(p.name)) throw new Error(`bad probe name ${p.name}`);
  if (!p.match) throw new Error(`${p.name}: no @match`);
  if (!p.sources.length) p.sources = [p.name];
  p.re = new RegExp(globToRegex(p.match));
  p.hash = crypto.createHash("sha1").update(JSON.stringify([p.match, p.sources, p.js])).digest("hex").slice(0, 12);
  p.file = file;
  return p;
}

function loadProbes() {
  const out = new Map();
  if (!fs.existsSync(CONF)) return out;
  for (const f of fs.readdirSync(CONF).sort()) {
    if (!/\.(js|json)$/.test(f)) continue;
    const full = path.join(CONF, f);
    if (f.endsWith(".js") && fs.existsSync(full.replace(/\.js$/, ".json"))) continue; // js_file of a .json probe
    try {
      const p = parseProbe(full);
      out.set(p.name, p);
    } catch (e) {
      log("probe load error", f, e.message);
    }
  }
  return out;
}

function sourceAllowed(p, s) {
  return p.sources.some((a) => (a.endsWith("*") ? s.startsWith(a.slice(0, -1)) : s === a));
}

// The code that actually runs in the page (isolated world). Idempotent per (name, hash).
function wrap(p) {
  return `(() => {
  const NAME = ${JSON.stringify(p.name)}, HASH = ${JSON.stringify(p.hash)}, RE = new RegExp(${JSON.stringify(p.re.source)});
  if (window.top !== window || !RE.test(location.href)) return "skip";
  const reg = (window.__bb = window.__bb || {});
  const old = reg[NAME];
  if (old && old.hash === HASH) return "already";
  if (old) { try { old.stop && old.stop(); } catch (e) {} }
  const send = (o) => { try { __busEmit(JSON.stringify(Object.assign({ probe: NAME }, o))); } catch (e) {} };
  const emit = (o) => send(typeof o === "string" ? { text: o } : o);
  const log = (...a) => send({ _log: a.map(String).join(" ") });
  const ready = (fn) => document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", fn, { once: true }) : fn();
  let api = {};
  try {
    const r = (function (emit, log, ready) {
${p.js}
    })(emit, log, ready);
    api = typeof r === "function" ? { stop: r } : (r || {});
  } catch (e) { send({ _error: String(e && e.stack || e) }); }
  reg[NAME] = Object.assign({ hash: HASH, started: Date.now() }, api);
  return "started";
})()`;
}

// ---------------- CDP ----------------
class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(typeof ev.data === "string" ? ev.data : Buffer.from(ev.data).toString());
      if (m.id && this.pending.has(m.id)) {
        const { res, rej } = this.pending.get(m.id);
        this.pending.delete(m.id);
        m.error ? rej(new Error(m.error.message)) : res(m.result);
      } else if (m.method) for (const h of this.handlers) h(m);
    });
    ws.addEventListener("close", () => { for (const { rej } of this.pending.values()) rej(new Error("closed")); this.pending.clear(); });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    const msg = { id, method, params };
    if (sessionId) msg.sessionId = sessionId;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify(msg));
      setTimeout(() => { if (this.pending.delete(id)) rej(new Error(`timeout ${method}`)); }, 30_000);
    });
  }
  on(h) { this.handlers.push(h); }
}

async function resolveEndpoint() {
  try { // (b) fixed --remote-debugging-port: answers /json/version
    const r = await fetch(`http://127.0.0.1:${PORT}/json/version`, { signal: AbortSignal.timeout(1500) });
    if (r.ok) return { url: (await r.json()).webSocketDebuggerUrl, mode: "port" };
  } catch {}
  for (const f of ACTIVE_PORT_FILES) { // (a) Chrome's built-in remote-debugging toggle (prompts on connect)
    try {
      const [port, p] = fs.readFileSync(f, "utf8").trim().split("\n");
      if (port && p) return { url: `ws://127.0.0.1:${port}${p}`, mode: "autoconnect" };
    } catch {}
  }
  return null;
}

function openWs(url, timeoutMs) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(url);
    const t = setTimeout(() => { try { ws.close(); } catch {} rej(new Error("connect timeout")); }, timeoutMs);
    ws.addEventListener("open", () => { clearTimeout(t); res(ws); }, { once: true });
    ws.addEventListener("error", (e) => { clearTimeout(t); rej(new Error(e.message || "ws error")); }, { once: true });
  });
}

// ---------------- daemon ----------------
class Daemon {
  constructor() {
    this.probes = loadProbes();
    this.targets = new Map(); // targetId -> {info, sessionId, probes: Map(name -> {id, hash}), ctx, attaching}
    this.sessions = new Map(); // sessionId -> targetId
    this.buckets = new Map(); this.seen = new Map(); this.recent = []; this.counts = {};
    this.dropWarned = new Map(); this.errWarned = new Set(); this.ledgerOk = new Map();
    this.cdp = null; this.mode = null; this.connectedAt = null;
  }

  writeStatus() {
    const s = {
      pid: process.pid, mode: this.mode, connected: !!this.cdp, connectedAt: this.connectedAt, updated: new Date().toISOString(),
      probes: [...this.probes.values()].map((p) => ({ name: p.name, match: p.match, sources: p.sources, hash: p.hash, emitted: this.counts[p.name] || 0 })),
      tabs: [...this.targets.entries()].filter(([, t]) => t.sessionId).map(([id, t]) => ({ id, url: t.info.url, title: t.info.title, probes: [...t.probes.keys()] })),
      recent: this.recent.slice(-20),
    };
    fs.mkdirSync(STATE, { recursive: true });
    fs.writeFileSync(STATUS + ".tmp", JSON.stringify(s, null, 2));
    fs.renameSync(STATUS + ".tmp", STATUS);
  }

  async run() {
    fs.mkdirSync(CTL, { recursive: true });
    fs.mkdirSync(CONF, { recursive: true });
    if (!probeToken()) log("WARN no orch-bus probe token; probe events will be marked unverified");
    this.watchProbes();
    this.watchCtl();
    let delay = 5000, downSince = Date.now(), warned = false;
    for (;;) {
      this.writeStatus();
      const ep = await resolveEndpoint();
      let err = `no browser debugging endpoint (port ${PORT} / DevToolsActivePort); start it with chrome-debug`;
      if (ep) {
        try {
          log("connecting", ep.mode, ep.url);
          const ws = await openWs(ep.url, ep.mode === "autoconnect" ? 120_000 : 10_000);
          if (warned) bus("browser-bus", `reconnected to the browser (${ep.mode})`);
          warned = false; delay = 5000;
          await this.session(ws, ep.mode); // returns when the socket closes
          log("disconnected");
          downSince = Date.now();
          continue;
        } catch (e) { err = `${ep.mode}: ${e.message}`; }
      }
      if (!warned && Date.now() - downSince > WARN_AFTER_MS) {
        bus("browser-bus", `WARN cannot reach the browser (${err}); retrying with backoff`);
        warned = true;
      }
      log("retry in", delay / 1000, "s:", err);
      await sleep(delay);
      delay = Math.min(delay * 2, ep && ep.mode === "autoconnect" ? 600_000 : 30_000); // port mode: one loopback request per try
    }
  }

  async session(ws, mode) {
    const cdp = (this.cdp = new CDP(ws));
    this.mode = mode; this.connectedAt = new Date().toISOString();
    const closed = new Promise((r) => ws.addEventListener("close", r, { once: true }));
    cdp.on((m) => this.onEvent(m).catch((e) => log("event error", m.method, e.message)));
    await cdp.send("Target.setDiscoverTargets", { discover: true }); // replays targetCreated for existing tabs
    this.writeStatus();
    await closed;
    this.cdp = null; this.targets.clear(); this.sessions.clear(); this.mode = null;
    this.writeStatus();
  }

  matching(url) {
    return [...this.probes.values()].filter((p) => p.re.test(url || ""));
  }

  async onEvent(m) {
    const p = m.params || {};
    switch (m.method) {
      case "Target.targetCreated":
      case "Target.targetInfoChanged": {
        const info = p.targetInfo;
        if (info.type !== "page") return;
        const t = this.targets.get(info.targetId) || { info, sessionId: null, probes: new Map(), ctx: null };
        t.info = info;
        this.targets.set(info.targetId, t);
        return this.sync(info.targetId);
      }
      case "Target.targetDestroyed":
      case "Target.detachedFromTarget": {
        const id = p.targetId || this.sessions.get(p.sessionId);
        const t = this.targets.get(id);
        if (t) { this.sessions.delete(t.sessionId); if (m.method === "Target.targetDestroyed") this.targets.delete(id); else { t.sessionId = null; t.probes.clear(); t.ctx = null; } }
        return this.writeStatus();
      }
      case "Runtime.executionContextCreated": {
        const t = this.targets.get(this.sessions.get(m.sessionId));
        const c = p.context;
        if (t && c.name === WORLD && c.auxData?.frameId === t.info.targetId) t.ctx = c.id;
        return;
      }
      case "Runtime.executionContextsCleared": {
        const t = this.targets.get(this.sessions.get(m.sessionId));
        if (t) t.ctx = null;
        return;
      }
      case "Page.loadEventFired": {
        // A tab created and navigated at once can load its page between the probe's first
        // evaluation (still about:blank -> "skip") and the on-new-document hook. Re-running the
        // probe after each load closes that gap; it is a no-op where the probe already runs.
        const t = this.targets.get(this.sessions.get(m.sessionId));
        if (!t) return;
        for (const name of t.probes.keys()) {
          const pr = this.probes.get(name);
          if (pr) await this.evalWorld(t, wrap(pr)).then((r) => r === "started" && log("injected", name, "into", t.info.url, "after load")).catch(() => {});
        }
        return;
      }
      case "Runtime.bindingCalled":
        if (p.name === "__busEmit") return this.onEmit(m.sessionId, p);
    }
  }

  // Bring one tab in line with the probe registry: attach / inject / remove / detach.
  async sync(targetId) {
    const t = this.targets.get(targetId);
    if (!t || !this.cdp || t.busy) { if (t) t.again = true; return; }
    t.busy = true;
    try {
      do {
        t.again = false;
        const want = this.matching(t.info.url);
        if (!want.length && !t.sessionId) break;
        if (!t.sessionId) {
          const { sessionId } = await this.cdp.send("Target.attachToTarget", { targetId, flatten: true });
          t.sessionId = sessionId; this.sessions.set(sessionId, targetId);
          await this.cdp.send("Runtime.addBinding", { name: "__busEmit", executionContextName: WORLD }, sessionId);
          await this.cdp.send("Page.enable", {}, sessionId);
          await this.cdp.send("Runtime.enable", {}, sessionId);
          log("attached", t.info.url);
        }
        const s = t.sessionId;
        for (const [name, inj] of [...t.probes]) {
          const p = this.probes.get(name);
          if (p && p.hash === inj.hash && want.includes(p)) continue;
          await this.cdp.send("Page.removeScriptToEvaluateOnNewDocument", { identifier: inj.id }, s).catch(() => {});
          await this.evalWorld(t, `(() => { const r = (window.__bb || {})[${JSON.stringify(name)}]; if (r) { try { r.stop && r.stop(); } catch (e) {} delete window.__bb[${JSON.stringify(name)}]; } })()`).catch(() => {});
          t.probes.delete(name);
          log("removed", name, "from", t.info.url);
        }
        if (!want.length) {
          await this.cdp.send("Target.detachFromTarget", { sessionId: s }).catch(() => {});
          this.sessions.delete(s); t.sessionId = null; t.ctx = null;
          log("detached", t.info.url);
          break;
        }
        for (const p of want) {
          if (t.probes.has(p.name)) {
            await this.evalWorld(t, wrap(p)).catch(() => {}); // re-run after navigation if needed; no-op when present
            continue;
          }
          const src = wrap(p);
          const { identifier } = await this.cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: src, worldName: WORLD }, s);
          t.probes.set(p.name, { id: identifier, hash: p.hash });
          const r = await this.evalWorld(t, src).catch((e) => e.message);
          log("injected", p.name, "into", t.info.url, "->", r);
        }
      } while (t.again);
    } catch (e) {
      log("sync error", t.info.url, e.message);
    } finally {
      t.busy = false;
      this.writeStatus();
    }
  }

  async evalWorld(t, expression) {
    if (!t.sessionId) throw new Error("not attached");
    if (!t.ctx) {
      const r = await this.cdp.send("Page.createIsolatedWorld", { frameId: t.info.targetId, worldName: WORLD }, t.sessionId);
      t.ctx = r.executionContextId;
    }
    const r = await this.cdp.send("Runtime.evaluate", { expression, contextId: t.ctx, returnByValue: true, awaitPromise: true }, t.sessionId);
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  }

  onEmit(sessionId, { payload }) {
    const t = this.targets.get(this.sessions.get(sessionId));
    if (!t) return;
    if (typeof payload !== "string" || payload.length > MAX_PAYLOAD) return log("drop: payload too large", payload?.length);
    let o;
    try { o = JSON.parse(payload); } catch { return log("drop: bad json"); }
    const p = this.probes.get(o.probe);
    if (!p || !t.probes.has(p.name)) return log("drop: unknown probe", o.probe);
    if (o._log) return log(`[${p.name}]`, String(o._log).slice(0, 500));
    if (o._error) {
      log(`[${p.name}] ERROR`, String(o._error).slice(0, 1000));
      if (!this.errWarned.has(p.hash)) { this.errWarned.add(p.hash); bus("browser-bus", `WARN probe ${p.name} threw: ${String(o._error).split("\n")[0].slice(0, 200)}`); }
      return;
    }
    const source = String(o.source || p.sources[0]);
    if (!/^[\w.:-]{1,40}$/.test(source) || !sourceAllowed(p, source)) return log("drop: source not allowed", p.name, source);
    const text = String(o.text ?? "").replace(/\s+/g, " ").trim();
    if (!text) return log("drop: empty text");
    const key = o.key ? `bb:${p.name}:${String(o.key).slice(0, 200)}` : null;
    if (key) {
      if (this.seen.has(key)) return;
      this.seen.set(key, 1);
      if (this.seen.size > 5000) this.seen.delete(this.seen.keys().next().value);
    }
    // token bucket per probe
    const now = Date.now();
    const b = this.buckets.get(p.name) || { tokens: RATE_BURST, at: now };
    b.tokens = Math.min(RATE_BURST, b.tokens + (now - b.at) / RATE_REFILL_MS); b.at = now;
    this.buckets.set(p.name, b);
    if (b.tokens < 1) {
      log("drop: rate limit", p.name);
      if (now - (this.dropWarned.get(p.name) || 0) > 3600_000) { this.dropWarned.set(p.name, now); bus("browser-bus", `WARN probe ${p.name} hit its rate limit; dropping events`); }
      return;
    }
    b.tokens -= 1;
    const ev = probeBus(source, text.slice(0, MAX_PAYLOAD), key);
    const short = ev?.text || (text.length > BUS_TEXT ? text.slice(0, BUS_TEXT - 1) + "…" : text);
    // the sidecar keeps the raw text for debugging; it is untrusted data (never feed it to an LLM bare)
    const rec = { at: new Date().toISOString(), probe: p.name, source, text, key, url: t.info.url, trust: ev?.trust, risk: ev?.risk, reasons: ev?.reasons, qid: ev?.qid, data: o.data ?? null };
    fs.appendFileSync(SIDECAR, JSON.stringify(rec) + "\n");
    this.recent.push({ at: rec.at, source, text: short });
    if (this.recent.length > 50) this.recent.shift();
    this.counts[p.name] = (this.counts[p.name] || 0) + 1;
    log("emit", source, short);
    if (now - (this.ledgerOk.get(p.name) || 0) > 600_000) {
      this.ledgerOk.set(p.name, now);
      spawnSync(ORCH, ["watch", "ok", `probe:${p.name}`], { timeout: 10_000 });
    }
    this.writeStatus();
  }

  // Type one "[orch] …" line into the WhatsApp self-chat ("Message yourself" row) and send it.
  // Used by orch-bus for SECURITY alerts, so it must not depend on the bus. Checks the open chat is
  // the self-chat right before typing and again right before Enter; never touches another chat's box.
  async sendSelf(text) {
    text = String(text || "").replace(/\s+/g, " ").trim().slice(0, 700);
    if (!text.startsWith("[orch] ")) return { ok: false, error: "only '[orch] …' messages may be sent" };
    if (!this.cdp) return { ok: false, error: "not connected to Chrome" };
    const t = [...this.targets.values()].find((x) => /^https:\/\/web\.whatsapp\.com\//.test(x.info.url));
    if (!t) return { ok: false, error: "no WhatsApp Web tab" };
    if (!t.sessionId) {
      const { sessionId } = await this.cdp.send("Target.attachToTarget", { targetId: t.info.targetId, flatten: true });
      t.sessionId = sessionId; this.sessions.set(sessionId, t.info.targetId);
    }
    const s = t.sessionId;
    const ev = async (expr) => (await this.cdp.send("Runtime.evaluate", { expression: expr, returnByValue: true }, s)).result.value;
    const isSelf = `(() => { const h = document.querySelector('#main header'); if (!h) return false;
      const sel = document.querySelector('#pane-side [data-testid="message-yourself-row"]');
      const title = sel && sel.closest('[role="row"]')?.querySelector('[title]')?.getAttribute('title');
      const ht = h.innerText || ''; return /\\(You\\)|Message yourself/.test(ht) || (!!title && ht.split(/\\n/).some((l) => l.trim() === title)); })()`;
    if (!(await ev(isSelf))) {
      const pos = await ev(`(() => { const sel = document.querySelector('#pane-side [data-testid="message-yourself-row"]');
        const row = sel && sel.closest('[role="row"]'); if (!row) return null; row.scrollIntoView({ block: 'center' });
        const r = row.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
      if (!pos) return { ok: false, error: "self-chat row not in the chat list" };
      for (const type of ["mouseMoved", "mousePressed", "mouseReleased"])
        await this.cdp.send("Input.dispatchMouseEvent", { type, x: pos.x, y: pos.y, button: "left", clickCount: 1 }, s);
      for (let i = 0; i < 60 && !(await ev(isSelf)); i++) await sleep(250);
      if (!(await ev(isSelf))) return { ok: false, error: "could not open the self-chat" };
    }
    const box = `document.querySelector('#main footer [contenteditable="true"]')`;
    const draft = await ev(`(() => { const b = ${box}; if (!b) return null; b.focus(); return (b.innerText || '').trim(); })()`);
    if (draft === null) return { ok: false, error: "no message box in the self-chat" };
    if (draft) return { ok: false, error: "the self-chat has an unsent draft; not typing over it" };
    await this.cdp.send("Input.insertText", { text }, s);
    await sleep(300);
    const typed = await ev(`(${box}?.innerText || '').trim()`);
    if (!(await ev(isSelf)) || typed.replace(/\s+/g, " ") !== text) {
      await ev(`(() => { const b = ${box}; if (b) { b.focus(); document.execCommand('selectAll'); document.execCommand('delete'); } })()`);
      return { ok: false, error: "open chat changed or text mismatch before send; cleared" };
    }
    for (const type of ["keyDown", "keyUp"])
      await this.cdp.send("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, ...(type === "keyDown" ? { text: "\r" } : {}) }, s);
    await sleep(800);
    const left = await ev(`(${box}?.innerText || '').trim()`);
    return left ? { ok: false, error: "Enter did not send (text still in the box)" } : { ok: true };
  }

  watchProbes() {
    let timer = null;
    const reload = () => {
      this.probes = loadProbes();
      log("probes:", [...this.probes.keys()].join(", ") || "(none)");
      for (const id of this.targets.keys()) this.sync(id);
      this.writeStatus();
    };
    fs.watch(CONF, () => { clearTimeout(timer); timer = setTimeout(reload, 300); });
    log("probes:", [...this.probes.keys()].join(", ") || "(none)");
  }

  // File-based control (no sockets): CLI writes ctl/req-ID.json, daemon answers ctl/res-ID.json.
  watchCtl() {
    const handle = async (f) => {
      const full = path.join(CTL, f);
      let req;
      try { req = JSON.parse(fs.readFileSync(full, "utf8")); fs.unlinkSync(full); } catch { return; }
      const out = { tabs: [] };
      if (req.cmd === "send-self") Object.assign(out, await this.sendSelf(req.text).catch((e) => ({ ok: false, error: e.message })));
      if (req.cmd === "test") {
        for (const t of this.targets.values()) {
          if (!t.probes.has(req.name)) continue;
          const expr = `(() => { const r = (window.__bb || {})[${JSON.stringify(req.name)}]; if (!r) return { running: false };
            let s = null; try { s = r.status ? r.status() : null; } catch (e) { s = { error: String(e) }; }
            return { running: true, hash: r.hash, since: new Date(r.started).toISOString(), status: s }; })()`;
          out.tabs.push({ url: t.info.url, result: await this.evalWorld(t, expr).catch((e) => ({ error: e.message })) });
        }
        out.connected = !!this.cdp; out.mode = this.mode; out.loaded = this.probes.has(req.name);
      }
      const res = path.join(CTL, f.replace(/^req-/, "res-"));
      fs.writeFileSync(res + ".tmp", JSON.stringify(out, null, 2));
      fs.renameSync(res + ".tmp", res);
    };
    fs.watch(CTL, (_, f) => { if (f && f.startsWith("req-") && f.endsWith(".json")) setTimeout(() => handle(f), 50); });
  }
}

// ---------------- CLI ----------------
function readStatus() {
  try { return JSON.parse(fs.readFileSync(STATUS, "utf8")); } catch { return null; }
}
function daemonAlive(s) {
  try { return s && process.kill(s.pid, 0); } catch { return false; }
}

async function main() {
  const [cmd, sub, ...rest] = process.argv.slice(2);
  if (cmd === "daemon") return new Daemon().run();
  if (cmd === "status" || (cmd === "probe" && sub === "ls")) {
    const s = readStatus();
    const alive = daemonAlive(s);
    if (cmd === "status") console.log(`daemon: ${alive ? `running (pid ${s.pid})` : "NOT running"}; browser: ${s?.connected ? `connected via ${s.mode} since ${s.connectedAt}` : "not connected"}`);
    const probes = loadProbes();
    for (const p of probes.values()) {
      const tabs = (s?.tabs || []).filter((t) => t.probes.includes(p.name));
      const em = s?.probes?.find((x) => x.name === p.name)?.emitted || 0;
      console.log(`${p.name.padEnd(14)} ${p.match.padEnd(32)} -> ${p.sources.join(",").padEnd(20)} tabs:${tabs.length} emitted:${em}  ${p.purpose}`);
      for (const t of tabs) console.log(`    ${t.url}`);
    }
    if (!probes.size) console.log("(no probes in " + CONF + ")");
    if (cmd === "status") for (const r of s?.recent || []) console.log(`  ${r.at.slice(11, 19)} ${r.source}: ${r.text}`);
    return;
  }
  if (cmd === "probe" && sub === "examples") {
    for (const f of fs.existsSync(EXAMPLES) ? fs.readdirSync(EXAMPLES).sort() : []) {
      if (!/\.(js|json)$/.test(f)) continue;
      const p = parseProbe(path.join(EXAMPLES, f));
      console.log(`${p.name.padEnd(16)} ${p.match.padEnd(30)} ${p.purpose}`);
      console.log(`    browser-bus probe add ${path.join(EXAMPLES, f)}`);
    }
    return;
  }
  if (cmd === "probe" && sub === "add") {
    const [file, nameArg] = rest;
    if (!file) throw new Error("usage: browser-bus probe add FILE [NAME]");
    const ext = path.extname(file);
    const tmp = parseProbe(path.resolve(file));
    const name = nameArg || tmp.name;
    fs.mkdirSync(CONF, { recursive: true });
    const dest = path.join(CONF, name + ext);
    if (ext === ".json") {
      const j = JSON.parse(fs.readFileSync(file, "utf8"));
      if (!j.js && j.js_file) { j.js = fs.readFileSync(path.resolve(path.dirname(file), j.js_file), "utf8"); delete j.js_file; }
      j.name = name;
      fs.writeFileSync(dest, JSON.stringify(j, null, 2));
    } else fs.copyFileSync(file, dest);
    const p = parseProbe(dest);
    spawnSync(ORCH, ["watch", "add", `probe:${p.name}`, "probe", p.purpose || `browser-bus probe ${p.name}`, "--cmd", p.match], { timeout: 10_000 });
    console.log(`added ${p.name} (${p.match} -> ${p.sources.join(",")}); daemon hot-reloads`);
    return;
  }
  if (cmd === "probe" && sub === "rm") {
    const [name] = rest;
    let n = 0;
    for (const ext of [".js", ".json"]) { const f = path.join(CONF, name + ext); if (fs.existsSync(f)) { fs.unlinkSync(f); n++; } }
    if (!n) throw new Error(`no probe ${name}`);
    spawnSync(ORCH, ["watch", "end", `probe:${name}`], { timeout: 10_000 });
    console.log(`removed ${name}`);
    return;
  }
  if (cmd === "send-self") {
    const text = [sub, ...rest].join(" ");
    if (!daemonAlive(readStatus())) throw new Error("daemon not running (systemctl --user status browser-bus)");
    const id = crypto.randomBytes(4).toString("hex");
    fs.mkdirSync(CTL, { recursive: true });
    fs.writeFileSync(path.join(CTL, `req-${id}.json`), JSON.stringify({ cmd: "send-self", text }));
    const res = path.join(CTL, `res-${id}.json`);
    for (let i = 0; i < 600; i++) {
      if (fs.existsSync(res)) {
        const r = JSON.parse(fs.readFileSync(res, "utf8")); fs.unlinkSync(res);
        console.log(new Date().toISOString(), "send-self", r.ok ? "sent" : `FAILED: ${r.error}`);
        process.exit(r.ok ? 0 : 1);
      }
      await sleep(100);
    }
    throw new Error("daemon did not answer in 60 s");
  }
  if (cmd === "probe" && sub === "test") {
    const [name] = rest;
    if (!daemonAlive(readStatus())) throw new Error("daemon not running (systemctl --user status browser-bus)");
    const id = crypto.randomBytes(4).toString("hex");
    fs.mkdirSync(CTL, { recursive: true });
    fs.writeFileSync(path.join(CTL, `req-${id}.json`), JSON.stringify({ cmd: "test", name }));
    const res = path.join(CTL, `res-${id}.json`);
    for (let i = 0; i < 100; i++) {
      if (fs.existsSync(res)) { console.log(fs.readFileSync(res, "utf8")); fs.unlinkSync(res); return; }
      await sleep(100);
    }
    throw new Error("daemon did not answer in 10 s");
  }
  console.log(fs.readFileSync(new URL(import.meta.url), "utf8").split("\n").slice(1, 12).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
  process.exit(cmd ? 2 : 0);
}

main().catch((e) => { console.error("browser-bus:", e.message); process.exit(1); });
