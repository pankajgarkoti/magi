// @match   /^https:\/\/.*/
// @source  unread
// @purpose Any tab: tell the bus when the unread count in a page title goes up, e.g. "(3) Inbox"
//
// Read-only. Many web apps (mail, chat, forums) put an unread count in the tab title. This probe
// watches document.title and emits when that number rises. The first title it sees is the baseline.
// The event carries the site's host and the count, not the title text, so subjects and names stay
// out of the bus. Narrow @match to the sites you care about.
let last = null, emitted = 0;
const count = () => {
  const m = document.title.match(/^\s*[(\[](\d+)\+?[)\]]/);
  return m ? parseInt(m[1], 10) : 0;
};
const check = () => {
  const n = count();
  if (last !== null && n > last) {
    emit({ text: `${location.host}: ${n} unread (+${n - last})`, key: `${location.host}|${n}|${Math.floor(Date.now() / 60000)}`, data: { host: location.host, unread: n } });
    emitted++;
  }
  last = n;
};
let obs = null;
ready(() => {
  check();
  const t = document.querySelector("title") || document.head;
  obs = new MutationObserver(check);
  if (t) obs.observe(t, { subtree: true, childList: true, characterData: true });
});
const timer = setInterval(check, 5000); // some apps replace the <title> node instead of editing it
return { stop() { obs && obs.disconnect(); clearInterval(timer); }, status() { return { unread: last, emitted }; } };
