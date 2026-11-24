"""store: the reflex registry — SQLite (WAL) at ~/.local/state/magi/reflexes.db.

Owner-directed design (24-25 Nov): NOT JSON files, NOT a growing markdown.

  reflexes — keyed (domain, name), status lifecycle kept from FlowPilot:
    candidate  new script, never replayed successfully yet
    active     proven by >= 1 successful replay
    broken     demoted after consecutive failures; never offered to lookup
  A regenerate for an ACTIVE reflex waits in the `candidate` column as a full
  next-version doc and never clobbers the serving one; the next successful
  replay promotes it. What FlowPilot lacked is added here:
    lease_until/lease_by - learn-exclusive lease so two agents never re-learn
                           one reflex concurrently; leases expire on their own
    runs                 - bounded audit table: one row per phase per replay,
                           pruned to the last 100 per reflex name
  busy_timeout 10s like orch.db: this db is shared across tabs and agents.
"""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path.home() / ".local/state/magi/reflexes.db"
TRACE_DIR = DB_PATH.parent / "reflexes" / "traces"
RUNS_KEEP_PER_NAME = 100
DEMOTE_AFTER_CONSECUTIVE = 2  # two failed replays in a row -> broken (a single flake is not)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reflexes (
  domain TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  when_to_use TEXT NOT NULL DEFAULT '',
  entry_url TEXT NOT NULL,
  phases TEXT NOT NULL,
  inputs TEXT NOT NULL DEFAULT '{}',
  pause_after INTEGER NOT NULL DEFAULT 0,
  tags TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'candidate',
  successes INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  candidate TEXT,
  owner_registered INTEGER NOT NULL DEFAULT 0,
  lease_until REAL,
  lease_by TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (domain, name)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reflex TEXT NOT NULL,
  phase TEXT NOT NULL,
  ok INTEGER NOT NULL,
  elapsed_ms INTEGER,
  zero_model_calls INTEGER NOT NULL DEFAULT 0,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_reflex ON runs(reflex, id);
"""

CONTENT_COLS = ("description", "when_to_use", "entry_url", "phases", "inputs", "pause_after", "tags")


class StoreError(Exception):
    pass


def connect(db_path=None):
    p = Path(db_path) if db_path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # shared across tabs and agent instances
    conn.execute("PRAGMA busy_timeout=10000")  # like orch.db
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def validate_doc(doc):
    """A reflex doc as authored (JSON file or learn dump): full next-version shape."""
    for k in ("domain", "name", "entry_url", "phases"):
        if not doc.get(k):
            raise StoreError(f"reflex doc missing '{k}'")
    phases = doc["phases"]
    if not isinstance(phases, list) or not phases:
        raise StoreError("phases must be a non-empty array")
    for i, ph in enumerate(phases):
        for k in ("name", "reflex_script", "verify_script"):
            if not ph.get(k):
                raise StoreError(f"phase {i} missing '{k}'")
        ph.setdefault("steady_rule", {"type": "dom_stable", "quiet_ms": 700, "timeout_ms": 8000})
        ph.setdefault("net_signature", None)
        ph.setdefault("verify_poll", None)
        ph.setdefault("timeout_ms", 90000)
    doc.setdefault("description", "")
    doc.setdefault("when_to_use", "")
    doc.setdefault("inputs", {})
    doc.setdefault("pause_after", 0)
    doc.setdefault("tags", "")
    return doc


def row_doc(row, use_candidate=False):
    """The full doc a row currently serves (candidate version if one is waiting)."""
    if use_candidate and row["candidate"]:
        return json.loads(row["candidate"])
    return {
        "domain": row["domain"], "name": row["name"], "description": row["description"],
        "when_to_use": row["when_to_use"], "entry_url": row["entry_url"],
        "phases": json.loads(row["phases"]), "inputs": json.loads(row["inputs"]),
        "pause_after": row["pause_after"], "tags": row["tags"],
    }


def get(conn, domain, name):
    return conn.execute("SELECT * FROM reflexes WHERE domain=? AND name=?", (domain, name)).fetchone()


def find_by_name(conn, name):
    """Bare name: exactly one row may match, else the caller must disambiguate."""
    return conn.execute("SELECT * FROM reflexes WHERE name=?", (name,)).fetchall()


def serving_version(row):
    """'candidate' if a next-version waits, else the row's own status as served."""
    return "candidate" if row["candidate"] else "active" if row["status"] == "active" else "row"


def install(conn, doc):
    """Author-side entry point (hand-authored file or a later learn dump).

    Lifecycle: a new script enters as candidate. Re-installing over an ACTIVE
    reflex parks the new doc in `candidate` and never clobbers the serving one;
    it is promoted only by the next successful replay. Re-installing over a
    candidate row iterates in place (it never served anyone yet); over a broken
    row the new script gets a fresh candidate chance.
    """
    doc = validate_doc(dict(doc))
    domain, name = doc["domain"], doc["name"]
    row = get(conn, domain, name)
    content = {k: (json.dumps(doc[k]) if k in ("phases", "inputs") else doc[k]) for k in CONTENT_COLS}
    cur = conn.cursor()
    if row is None:
        cur.execute(
            "INSERT INTO reflexes (domain,name,entry_url,description,when_to_use,phases,inputs,"
            "pause_after,tags,status,updated_at) VALUES (?,?,?,?,?,?,?,?,?, 'candidate', ?)",
            (domain, name, content["entry_url"], content["description"], content["when_to_use"],
             content["phases"], content["inputs"], content["pause_after"], content["tags"], _now()))
        conn.commit()
        return f"installed {domain}/{name} as candidate (needs 1 successful replay to activate)"
    if row["status"] == "active":
        cur.execute(
            "UPDATE reflexes SET candidate=?, updated_at=? WHERE domain=? AND name=?",
            (json.dumps(doc), _now(), domain, name))
        conn.commit()
        return f"parked {domain}/{name} next-version as candidate; active version keeps serving until the candidate's first successful replay"
    cur.execute(
        "UPDATE reflexes SET entry_url=?, description=?, when_to_use=?, phases=?, inputs=?,"
        " pause_after=?, tags=?, status='candidate', consecutive_failures=0, updated_at=?"
        " WHERE domain=? AND name=?",
        (content["entry_url"], content["description"], content["when_to_use"], content["phases"],
         content["inputs"], content["pause_after"], content["tags"], _now(), domain, name))
    conn.commit()
    return f"updated {domain}/{name} in place as candidate (row was {row['status']}; never served successfully)"


def mark_success(conn, domain, name, served_candidate):
    """One successful replay: counters up, flake counter reset; promote a waiting candidate."""
    row = get(conn, domain, name)
    if row is None:
        return
    cur = conn.cursor()
    cur.execute("UPDATE reflexes SET successes=successes+1, consecutive_failures=0, updated_at=?"
                " WHERE domain=? AND name=?", (_now(), domain, name))
    if served_candidate and row["candidate"]:
        doc = json.loads(row["candidate"])
        content = {k: (json.dumps(doc[k]) if k in ("phases", "inputs") else doc[k]) for k in CONTENT_COLS}
        cur.execute(
            "UPDATE reflexes SET entry_url=?, description=?, when_to_use=?, phases=?, inputs=?,"
            " pause_after=?, tags=?, candidate=NULL, status='active', updated_at=?"
            " WHERE domain=? AND name=?",
            (content["entry_url"], content["description"], content["when_to_use"], content["phases"],
             content["inputs"], content["pause_after"], content["tags"], _now(), domain, name))
    elif row["status"] == "candidate":
        cur.execute("UPDATE reflexes SET status='active', updated_at=? WHERE domain=? AND name=?",
                     (_now(), domain, name))
    conn.commit()


def mark_failure(conn, domain, name, served_candidate):
    """A failed replay. Two consecutive failures demote: candidate dropped (active
    keeps serving), or an active reflex goes broken (never offered to lookup)."""
    row = get(conn, domain, name)
    if row is None:
        return
    cur = conn.cursor()
    cur.execute("UPDATE reflexes SET failures=failures+1, consecutive_failures=consecutive_failures+1,"
                " updated_at=? WHERE domain=? AND name=?", (_now(), domain, name))
    row = get(conn, domain, name)
    if row["consecutive_failures"] >= DEMOTE_AFTER_CONSECUTIVE:
        if served_candidate and row["candidate"]:
            cur.execute("UPDATE reflexes SET candidate=NULL, consecutive_failures=0, updated_at=?"
                         " WHERE domain=? AND name=?", (_now(), domain, name))
        elif not row["candidate"]:
            cur.execute("UPDATE reflexes SET status='broken', updated_at=? WHERE domain=? AND name=?",
                         (_now(), domain, name))
    conn.commit()


def record_run(conn, reflex, phase, ok, elapsed_ms, zero_model_calls):
    """Bounded audit: append, then prune to the last RUNS_KEEP_PER_NAME rows per name."""
    cur = conn.cursor()
    cur.execute("INSERT INTO runs (reflex, phase, ok, elapsed_ms, zero_model_calls, ts)"
                " VALUES (?,?,?,?,?,?)",
                (reflex, phase, 1 if ok else 0, elapsed_ms, 1 if zero_model_calls else 0, _now()))
    cur.execute(
        "DELETE FROM runs WHERE reflex=? AND id NOT IN"
        " (SELECT id FROM runs WHERE reflex=? ORDER BY id DESC LIMIT ?)",
        (reflex, reflex, RUNS_KEEP_PER_NAME))
    conn.commit()


def recent_runs(conn, reflex, limit=12):
    return conn.execute("SELECT * FROM runs WHERE reflex=? ORDER BY id DESC LIMIT ?",
                        (reflex, limit)).fetchall()


def acquire_lease(conn, domain, name, lease_by, minutes):
    """Learn-exclusive lease: only one agent may re-learn a reflex at a time; it expires."""
    now = time.time()
    cur = conn.cursor()
    cur.execute(
        "UPDATE reflexes SET lease_until=?, lease_by=? WHERE domain=? AND name=?"
        " AND (lease_until IS NULL OR lease_until <= ?)",
        (now + minutes * 60, lease_by, domain, name, now))
    conn.commit()
    if cur.rowcount:
        return {"ok": True, "until": now + minutes * 60}
    row = get(conn, domain, name)
    if row is None:
        raise StoreError(f"no reflex {domain}/{name}")
    return {"ok": False, "lease_by": row["lease_by"], "lease_until": row["lease_until"]}


def release_lease(conn, domain, name, lease_by):
    conn.execute("UPDATE reflexes SET lease_until=NULL, lease_by=NULL WHERE domain=? AND name=? AND lease_by=?",
                 (domain, name, lease_by))
    conn.commit()


def suggest(conn, url):
    """JIT discovery (no catalog file): reflexes applicable to a URL's domain,
    active first, then by proven successes. Broken ones are never offered."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        raise StoreError(f"cannot read a domain from {url!r}")
    return conn.execute(
        "SELECT * FROM reflexes WHERE status != 'broken'"
        " AND (domain = ? OR ? LIKE '%.' || domain)"
        " ORDER BY (status = 'active') DESC, (successes - failures) DESC, name",
        (host, host)).fetchall()
