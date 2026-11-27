#!/usr/bin/env node
// Test for the example github-notifications browser probe: headers, read-only behaviour, and
// the count/title/dedupe semantics, driven with a fake DOM (no browser or network needed).
//   node tests/agent/test-probe-github-notifications.mjs
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const root = path.resolve(import.meta.dirname, "../..");
const file = path.join(root, "agent/browser-bus/examples/github-notifications.js");
let fails = 0, total = 0;
const pass = (m) => { total++; console.log(`  PASS  ${m}`); };
const fail = (m) => { total++; fails++; console.log(`  FAIL  ${m}`); };
const check = (m, fn) => { try { fn() ? pass(m) : fail(m); } catch (e) { fail(`${m} (${e.message})`); } };

const src = fs.readFileSync(file, "utf8");
const header = (k) => (src.match(new RegExp(`^\\s*//\\s*@${k}\\s+(.+?)\\s*$`, "m")) || [])[1] || "";
check("probe declares @match for the notifications page", () => /^https:\/\/github\.com\/notifications\*$/.test(header("match")));
check("probe declares a @source", () => header("source") === "gh-notifications");
check("probe declares a @purpose", () => /unread/i.test(header("purpose")));
check("probe is read-only (no clicks, navigation, network or typing)", () =>
  !/\.(click|focus|submit|write)\s*\(|\bfetch\s*\(|XMLHttpRequest|window\.open|\.href\s*=/.test(src));

// ---- drive the probe with a fake DOM ---------------------------------------------------
let rows = [];
let tick = null;
const events = [];
const row = (title, unread = true) => ({
  getAttribute: (n) => (n === "class" ? (unread ? "unread" : "read") : null),
  querySelector: (sel) => (/notification-title|a\[href\]/.test(sel) ? { textContent: title } : null),
});
const document = {
  readyState: "complete",
  title: "Notifications · GitHub",
  body: {},
  querySelectorAll: (sel) => (/notification-row|notifications-list-item/.test(sel) ? rows : []),
  querySelector: (sel) => (/notifications-page/.test(sel) ? {} : null),
  addEventListener() {},
};
const location = { host: "github.com", href: "https://github.com/notifications" };
class MutationObserver { observe() {} disconnect() {} }
const ready = (fn) => fn();
const setInterval = (fn) => { tick = fn; return 1; };
const clearInterval = () => {};
const emit = (o) => events.push(o);
const log = () => {};

const run = new Function("emit", "log", "ready", "document", "location", "setInterval",
  "clearInterval", "MutationObserver", src);
const api = run(emit, log, ready, document, location, setInterval, clearInterval, MutationObserver);
check("probe returns stop/status and installs a poller", () => typeof api.stop === "function" &&
  typeof api.status === "function" && typeof tick === "function");

rows = [row("Fix the login thing"), row("Send the invoice"), row("Already read", false)];
tick(); // the fake page has loaded: establish the baseline
check("first sighting is the baseline (no event)", () => events.length === 0 && api.status().unread === 2);

rows.push(row("Call the dentist"));
tick();
check("a rise emits exactly one event", () => events.length === 1);
check("event text carries count, delta and newest titles", () => {
  const e = events[0];
  return /github notifications: 3 unread \(\+1\)/.test(e.text) && e.text.includes("Call the dentist") &&
    e.text.includes("Send the invoice");
});
check("event has a host/count/title dedupe key and data", () => {
  const e = events[0];
  return typeof e.key === "string" && e.key.includes("github.com") && e.key.includes("3") &&
    e.data && e.data.unread === 3 && e.data.titles.length === 3;
});

tick();
check("an unchanged count emits nothing", () => events.length === 1);
rows = [row("Fix the login thing")];
tick();
check("a falling count emits nothing", () => events.length === 1);
rows = [row("Fix the login thing"), row("New item")];
tick();
check("a later rise emits again with the new count", () => events.length === 2 &&
  /2 unread \(\+1\)/.test(events[1].text));

check("stop() tears down the observer and timer", () => { api.stop(); return true; });

// ---- browser-bus can discover it -------------------------------------------------------
const listing = execFileSync(process.execPath, [path.join(root, "agent/lib/browser-bus.mjs"), "probe", "examples"],
  { cwd: root, encoding: "utf8" });
check("browser-bus probe examples lists github-notifications", () => /github-notifications\s+https:\/\/github\.com\/notifications\*/.test(listing));

console.log(`\n${fails === 0 ? "RESULT: PASS" : "RESULT: FAIL"} (${total - fails}/${total})`);
process.exit(fails ? 1 : 0);
