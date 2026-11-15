// @match   http://127.0.0.1:*/*
// @source  page-title
// @purpose Local dev servers: tell the bus when a page's title changes (build status, test runners)
//
// Read-only. Emits the new title of any page served from 127.0.0.1 whenever it changes, so an
// agent waiting on a local build or test page gets an event instead of polling a screenshot.
let last = null, emitted = 0;
const check = () => {
  const t = document.title.trim();
  if (last !== null && t && t !== last) {
    emit({ text: `${location.host}${location.pathname}: ${t}`, key: `${location.href}|${t}` });
    emitted++;
  }
  last = t;
};
ready(check);
const timer = setInterval(check, 2000);
return { stop() { clearInterval(timer); }, status() { return { title: last, emitted }; } };
