// @match   https://github.com/notifications*
// @source  gh-notifications
// @purpose GitHub notifications (example, not installed by default): report when the unread count goes up, with the newest titles
//
// Read-only. Watches the notifications page only: never clicks a row (which would mark it read),
// never opens a notification and never types. The first sighting of the page is the baseline.
// GitHub changes its markup often; if the selectors below stop matching, the count falls back to
// the unread number in the page title and the probe stays quiet rather than guessing.
// Install: browser-bus probe add /usr/share/magi/agent/browser-bus/examples/github-notifications.js
const ROW_SEL = '[data-testid="notification-row"], .js-notification-row, .notifications-list-item';
const TITLE_SEL = '[data-testid="notification-title"], .notification-list-item-title, .js-notification-title';
let last = null, emitted = 0;

const rows = () => Array.from(document.querySelectorAll(ROW_SEL));
const clean = (s) => (s || "").replace(/[‪-‮⁦-⁩]/g, "").replace(/\s+/g, " ").trim();

function unread(rows) {
  return rows.filter((r) => /\bunread\b/i.test(String(r.getAttribute("class") || "")) ||
    r.getAttribute("data-unread") === "true" ||
    r.querySelector(".unread") !== null);
}

function title(row) {
  const el = row.querySelector(TITLE_SEL) || row.querySelector("a[href]");
  return clean(el && el.textContent);
}

function snapshot() {
  const all = rows();
  const fresh = unread(all);
  // no rows yet (still loading) means "nothing heard": keep the baseline, don't re-emit a rise
  if (!all.length) return null;
  const m = document.title.match(/^\s*[(\[](\d+)\+?[)\]]/);
  return {
    count: fresh.length || (m ? parseInt(m[1], 10) : 0),
    titles: fresh.slice(0, 3).map(title).filter(Boolean),
  };
}

function check() {
  const now = snapshot();
  if (!now) return;
  if (last !== null && now.count > last) {
    emit({
      text: `github notifications: ${now.count} unread (+${now.count - last})` +
        ` - newest: ${now.titles.join(" | ") || "(titles unavailable)"}`,
      key: `${location.host}|notifications|${now.count}|${now.titles.join("|")}|` +
        Math.floor(Date.now() / 60000),
      data: { host: location.host, unread: now.count, titles: now.titles },
    });
    emitted++;
  }
  last = now.count;
}

let obs = null;
ready(() => {
  check();
  const target = document.querySelector('[data-testid="notifications-page"]') || document.body;
  if (target) {
    obs = new MutationObserver(check);
    obs.observe(target, { subtree: true, childList: true, characterData: true, attributes: true });
  }
});
const timer = setInterval(check, 5000); // some apps replace the row nodes instead of editing them
return {
  stop() { clearInterval(timer); if (obs) obs.disconnect(); },
  status() { return { unread: last, emitted }; },
};
