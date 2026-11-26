"""signature: the per-phase network fingerprint used for drift detection.

FlowPilot's idea, kept but made narrow (the 25 Nov findings are explicit: an
unfiltered ordered hash of a page's traffic is pure noise — one app entry fired
376 XHR/fetch requests, and ids change every load). So a phase declares a
positive `net_filter` — URL substrings or globs for the calls that actually
matter — and only those calls are fingerprinted. A phase with `net_filter: []`
expects no matching call at all.

The fingerprint is an ordered sha256 over `METHOD /normalized/path` lines:

  - only Document/XHR/Fetch requests count (not scripts, images, websockets);
  - the query string and fragment are dropped;
  - opaque path segments (numbers, UUIDs, long hex/base64-ish ids) become `:id`,
    so a fresh upload id does not look like drift.

`net_signature` is null until `magi-reflex seal` captures one from a verified
replay. Once present, every replay compares it; a mismatch is STALE.
"""
import fnmatch
import hashlib
import re
from urllib.parse import urlsplit

# API-ish requests only. A form POST/navigation is Document; XHR/fetch are the
# app's own calls. Everything else (Script, Stylesheet, Image, Font, Media,
# WebSocket, ...) is page furniture and would only add noise.
COUNTED_TYPES = frozenset({"Document", "XHR", "Fetch"})

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX = re.compile(r"^[0-9a-fA-F]{8,}$")
# base64url-ish / opaque ids: long, and not a plain dictionary word (lowercase
# only words of this length are usually slugs, so require a digit or a case mix)
_OPAQUE = re.compile(r"^(?=.*[0-9A-Z_-])[A-Za-z0-9_-]{20,}$")


def normalize_path(url):
    """A stable path for one request: opaque segments -> :id, no query/fragment."""
    try:
        path = urlsplit(url or "").path or "/"
    except ValueError:
        path = "/"
    out = []
    for seg in path.split("/"):
        if not seg:
            continue
        if seg.isdigit() or _UUID.match(seg) or _HEX.match(seg) or _OPAQUE.match(seg):
            out.append(":id")
        else:
            out.append(seg)
    return "/" + "/".join(out) if out else "/"


def normalize_request(method, url):
    return f"{(method or 'GET').upper()} {normalize_path(url)}"


def matches(url, patterns):
    """URL substring or shell-glob match against the phase's positive filter."""
    if not patterns:
        return False
    url = url or ""
    return any(p and (p in url or fnmatch.fnmatchcase(url, p)) for p in patterns)


def _hash(entries):
    return "sha256:" + hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _fmt(entries):
    if not entries:
        return "(none)"
    shown = ", ".join(entries[:6])
    return shown + (f" … (+{len(entries) - 6})" if len(entries) > 6 else "")


def observe(requests, patterns):
    """The ordered signature of the matching requests in this phase's window.

    `requests` are the runner's recorded {method,url,type} dicts in arrival order.
    Returns {hash, count, entries}; entries is bounded so a noisy page cannot
    grow the stored doc without limit (the hash still covers every match).
    """
    entries = []
    for r in requests or []:
        if (r.get("type") or "") not in COUNTED_TYPES:
            continue
        url = r.get("url") or ""
        if not matches(url, patterns):
            continue
        entries.append(normalize_request(r.get("method"), url))
    return {"hash": _hash(entries), "count": len(entries), "entries": entries[:40]}


def compare(expected, observed):
    """None when the replay's traffic matches the sealed signature, else a why."""
    if not isinstance(expected, dict):
        return "stored net_signature is not an object"
    want = expected.get("hash")
    if not want:
        return "stored net_signature has no hash"
    want_n = expected.get("count")
    got_n = observed.get("count")
    if want_n is not None and want_n != got_n:
        return (f"net signature drift: expected {want_n} call(s) [{_fmt(expected.get('entries'))}], "
                f"observed {got_n} [{_fmt(observed.get('entries'))}]")
    if want != observed.get("hash"):
        return (f"net signature drift: call order/identity changed "
                f"(expected {want[:15]}…, observed {str(observed.get('hash'))[:15]}…; "
                f"observed [{_fmt(observed.get('entries'))}])")
    return None


def check_phase(phase, requests):
    """Runner gate: (observed, why). No filter -> (None, None); no sealed sig -> no drift check."""
    patterns = phase.get("net_filter")
    if patterns is None:
        return None, None
    observed = observe(requests, patterns)
    if phase.get("net_signature") is not None:
        return observed, compare(phase["net_signature"], observed)
    return observed, None
