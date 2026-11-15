// @match   https://web.whatsapp.com/*
// @source  chat-*
// @purpose WhatsApp Web (example, not installed by default): notes you send yourself, and chats you list in WATCH
//
// Read-only. Watches the chat list only (never opens a chat, never clicks, never types), so nothing
// is marked read. The first sighting of a row is the baseline; later changes are new messages.
// Message text goes to the bus, and from there to your orchestrator agent and its provider: list only
// chats you are fine sharing. WhatsApp changes its markup often; the selectors below may need updating.
// Install: browser-bus probe add /usr/share/magi/agent/browser-bus/examples/whatsapp-self.js
const WATCH = {};            // chat title -> bus source, e.g. { "Family": "chat-family" }
const SELF = "chat-self";    // your "Message yourself" chat: notes you leave for the orchestrator

const base = new Map();
let scans = 0, emitted = 0, seeded = false;
const q = (el, s) => el.querySelector(s);
const clean = (s) => (s || "").replace(/[‪-‮⁦-⁩]/g, "").trim();

function readRow(r) {
  const title = clean(q(r, '[data-testid="cell-frame-title"] [title]')?.getAttribute("title"));
  const st = q(r, '[data-testid="last-msg-status"]');
  if (!title || !st) return null;
  return {
    title,
    text: clean(st.getAttribute("title")),
    sender: clean(q(st, ':scope > div > div > span[dir="auto"]')?.textContent),
    self: !!q(r, '[data-testid="message-yourself-row"]'),
    ts: clean(q(r, '[data-testid="chat-list-timestamp"]')?.textContent),
  };
}

function scan() {
  const side = document.querySelector("#pane-side");
  if (!side) return;
  scans++;
  for (const r of side.querySelectorAll('[role="row"]')) {
    const m = readRow(r);
    if (!m) continue;
    const prev = base.get(m.title);
    base.set(m.title, m);
    if (!prev || !seeded || (m.text === prev.text && m.ts === prev.ts)) continue;
    if (/^(typing|recording audio)…?$/i.test(m.text)) continue;
    const source = m.self ? SELF : WATCH[m.title];
    if (!source || (!m.self && m.sender === "You")) continue;
    const who = m.self ? "" : `${m.title} · ${m.sender ? m.sender + ": " : ""}`;
    emit({ source, text: `${who}${m.text.slice(0, 280)}`, key: [m.title, m.ts, m.text].join("|") });
    emitted++;
  }
  if (!seeded && base.size) { seeded = true; log(`baseline: ${base.size} chats`); }
}

let obs = null;
const timer = setInterval(() => {
  const side = document.querySelector("#pane-side");
  if (side && !obs) { obs = new MutationObserver(scan); obs.observe(side, { subtree: true, childList: true, characterData: true, attributes: true }); }
  scan();
}, 3000);
return { stop() { clearInterval(timer); obs && obs.disconnect(); }, status() { return { chats: base.size, scans, emitted, seeded }; } };
