"""SQLite storage for sessions, resume versions, history events and the job tracker.

Database file: <DATA_DIR>/careercopilot.db (Python's built-in sqlite3, nothing to install).
Resume documents themselves stay on disk as immutable files; the database stores their
metadata (file name, parent version, SHA-256) so integrity can be checked on every read.

Older installs kept the same data in JSON files (session.json, manifest.json, tracker.json);
those are imported automatically the first time the database is opened.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from . import config

log = logging.getLogger("careercopilot")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    filename      TEXT,
    ext           TEXT,
    company       TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT '',
    jd_text       TEXT,
    methodology   TEXT,
    weights       TEXT,                    -- JSON object, frozen per session
    as_of         TEXT,
    created       REAL,
    last_score    REAL,
    head_version  INTEGER NOT NULL DEFAULT 0,
    state         TEXT NOT NULL DEFAULT '{}'   -- JSON: answers, denials, confirmations, edits, letters, draft
);

CREATE TABLE IF NOT EXISTS versions (
    session_id  TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    id          INTEGER NOT NULL,
    file        TEXT    NOT NULL,
    parent      INTEGER,
    created     REAL    NOT NULL,
    label       TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    change      TEXT,                      -- JSON description of the edit
    PRIMARY KEY (session_id, id)
);

CREATE TABLE IF NOT EXISTS events (
    session_id  TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    type        TEXT    NOT NULL,          -- upload | apply | undo | restore
    at          REAL    NOT NULL,
    data        TEXT    NOT NULL,          -- JSON (version / from / to)
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    company     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    applied_on  TEXT NOT NULL DEFAULT '',
    deadline    TEXT NOT NULL DEFAULT '',
    salary      TEXT NOT NULL DEFAULT '',
    contact     TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL CHECK (status IN ('saved','applied','interview','offer','rejected')),
    created     REAL NOT NULL,
    updated     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS job_status_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status  TEXT NOT NULL,
    at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS job_search_cache (
    key         TEXT PRIMARY KEY,          -- normalised query | country | pages
    fetched_at  REAL NOT NULL,
    payload     TEXT NOT NULL              -- JSON list of normalised postings
);

CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_updated     ON jobs(updated DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_session     ON jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_history_job      ON job_status_history(job_id);
"""

SESSION_COLUMNS = ("company", "role", "jd_text", "methodology", "weights", "as_of", "created", "last_score")

_local = threading.local()
_init_lock = threading.Lock()
_initialised: set[str] = set()


class DatabaseError(Exception):
    """The database could not be read or written."""


def db_path() -> Path:
    return Path(config.DATA_DIR) / "careercopilot.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit; explicit transactions below
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")      # readers never block the writer
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def conn() -> sqlite3.Connection:
    """One connection per thread per database file (tests switch DATA_DIR between runs)."""
    path = db_path()
    key = str(path)
    cons = getattr(_local, "cons", None)
    if cons is None:
        cons = _local.cons = {}
    con = cons.get(key)
    if con is None:
        try:
            con = cons[key] = _connect(path)
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not open the database at {path}: {exc}") from exc
    if key not in _initialised:
        with _init_lock:
            if key not in _initialised:
                _init(con)
                _initialised.add(key)
    return con


@contextmanager
def tx():
    """A write transaction: everything inside commits together or not at all."""
    c = conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def _init(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    _import_legacy_json(con)


# --------------------------------------------------------------------------- sessions

def _row_to_state(row: sqlite3.Row) -> dict:
    state = json.loads(row["state"] or "{}")
    for k in SESSION_COLUMNS:
        if row[k] is not None:
            state[k] = json.loads(row[k]) if k == "weights" else row[k]
    return state


def get_session_state(sid: str) -> dict | None:
    row = conn().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None or row["jd_text"] is None:
        return None
    return _row_to_state(row)


def save_session_state(sid: str, state: dict, c: sqlite3.Connection | None = None):
    cols = {k: (json.dumps(state[k]) if k == "weights" else state.get(k)) for k in SESSION_COLUMNS}
    cols["company"] = cols["company"] or ""
    cols["role"] = cols["role"] or ""
    rest = json.dumps({k: v for k, v in state.items() if k not in SESSION_COLUMNS}, default=str)
    sql = (f"INSERT INTO sessions (id, {', '.join(SESSION_COLUMNS)}, state) VALUES (?, {', '.join('?' * len(SESSION_COLUMNS))}, ?) "
           f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in SESSION_COLUMNS)}, state = excluded.state")
    args = (sid, *cols.values(), rest)
    if c is not None:
        c.execute(sql, args)
    else:
        with tx() as t:
            t.execute(sql, args)


def list_sessions() -> list[dict]:
    rows = conn().execute("""
        SELECT s.id, s.filename, s.company, s.role, s.created, s.head_version, s.last_score, s.state,
               (SELECT COUNT(*) FROM versions v WHERE v.session_id = s.id) AS n_versions
        FROM sessions s WHERE s.jd_text IS NOT NULL ORDER BY s.created DESC""").fetchall()
    return [{"session_id": r["id"], "filename": r["filename"], "company": r["company"], "role": r["role"],
             "created": r["created"], "head": r["head_version"], "versions": r["n_versions"],
             "letters": len(json.loads(r["state"] or "{}").get("letters", [])), "score": r["last_score"]}
            for r in rows]


# --------------------------------------------------------------------------- versions

def load_manifest(sid: str) -> dict | None:
    c = conn()
    row = c.execute("SELECT filename, ext, head_version FROM sessions WHERE id = ?", (sid,)).fetchone()
    if row is None or row["ext"] is None:
        return None
    versions = [{"id": v["id"], "file": v["file"], "parent": v["parent"], "created": v["created"],
                 "label": v["label"], "sha256": v["sha256"], "change": json.loads(v["change"]) if v["change"] else None}
                for v in c.execute("SELECT * FROM versions WHERE session_id = ? ORDER BY id", (sid,))]
    events = [{"type": e["type"], "at": e["at"], **json.loads(e["data"])}
              for e in c.execute("SELECT * FROM events WHERE session_id = ? ORDER BY seq", (sid,))]
    return {"filename": row["filename"], "ext": row["ext"], "head": row["head_version"],
            "versions": versions, "events": events}


def save_manifest(sid: str, manifest: dict, c: sqlite3.Connection | None = None):
    def write(t: sqlite3.Connection):
        t.execute("INSERT INTO sessions (id, filename, ext, head_version) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(id) DO UPDATE SET filename = excluded.filename, ext = excluded.ext, "
                  "head_version = excluded.head_version",
                  (sid, manifest["filename"], manifest["ext"], manifest["head"]))
        t.executemany(
            "INSERT INTO versions (session_id, id, file, parent, created, label, sha256, change) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(session_id, id) DO UPDATE SET change = excluded.change",
            [(sid, v["id"], v["file"], v["parent"], v["created"], v["label"], v["sha256"],
              json.dumps(v["change"], default=str) if v.get("change") is not None else None)
             for v in manifest["versions"]])
        t.executemany(
            "INSERT OR IGNORE INTO events (session_id, seq, type, at, data) VALUES (?, ?, ?, ?, ?)",
            [(sid, i, e["type"], e["at"], json.dumps({k: v for k, v in e.items() if k not in ("type", "at")}))
             for i, e in enumerate(manifest["events"])])
    if c is not None:
        write(c)
    else:
        with tx() as t:
            write(t)


# --------------------------------------------------------------------------- tracker

JOB_FIELDS = ("company", "title", "url", "location", "notes", "applied_on", "deadline", "salary", "contact",
              "session_id")


def _job(c: sqlite3.Connection, row: sqlite3.Row) -> dict:
    job = dict(row)
    job["history"] = [{"status": h["status"], "at": h["at"]} for h in
                      c.execute("SELECT status, at FROM job_status_history WHERE job_id = ? ORDER BY id", (row["id"],))]
    return job


def list_jobs() -> list[dict]:
    c = conn()
    return [_job(c, r) for r in c.execute("SELECT * FROM jobs ORDER BY updated DESC")]


def get_job(job_id: str) -> dict | None:
    c = conn()
    row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job(c, row) if row else None


def insert_job(job: dict, c: sqlite3.Connection | None = None):
    def write(t):
        cols = ("id", *JOB_FIELDS, "status", "created", "updated")
        t.execute(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                  tuple(job.get(k, "") or ("" if k in JOB_FIELDS else job.get(k)) for k in cols))
        t.executemany("INSERT INTO job_status_history (job_id, status, at) VALUES (?, ?, ?)",
                      [(job["id"], h["status"], h["at"]) for h in job.get("history", [])])
    if c is not None:
        write(c)
    else:
        with tx() as t:
            write(t)


def update_job(job_id: str, fields: dict, new_status: str | None, at: float):
    with tx() as t:
        sets = {**fields, "updated": at}
        if new_status:
            sets["status"] = new_status
            t.execute("INSERT INTO job_status_history (job_id, status, at) VALUES (?, ?, ?)", (job_id, new_status, at))
        t.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), job_id))


def delete_job(job_id: str) -> bool:
    with tx() as t:
        return t.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0


# --------------------------------------------------------------------------- API response cache

def cache_get(table: str, key: str, ttl: float):
    assert table in ("job_search_cache",)
    import time
    row = conn().execute(f"SELECT fetched_at, payload FROM {table} WHERE key = ?", (key,)).fetchone()
    if row is None or time.time() - row["fetched_at"] > ttl:
        return None
    return json.loads(row["payload"])


def cache_put(table: str, key: str, value):
    assert table in ("job_search_cache",)
    import time
    with tx() as t:
        t.execute(f"INSERT INTO {table} (key, fetched_at, payload) VALUES (?, ?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at, payload = excluded.payload",
                  (key, time.time(), json.dumps(value)))
        t.execute(f"DELETE FROM {table} WHERE fetched_at < ?", (time.time() - 7 * 86400,))


# --------------------------------------------------------------------------- one-time import of JSON files

def _import_legacy_json(con: sqlite3.Connection):
    data = Path(config.DATA_DIR)
    imported = 0
    for sj in sorted((data / "sessions").glob("*/session.json")) if (data / "sessions").exists() else []:
        sid = sj.parent.name
        if con.execute("SELECT 1 FROM sessions WHERE id = ? AND jd_text IS NOT NULL", (sid,)).fetchone():
            continue
        try:
            state = json.loads(sj.read_text())
            manifest = json.loads((sj.parent / "manifest.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Skipping unreadable legacy session %s: %s", sid, exc)
            continue
        con.execute("BEGIN IMMEDIATE")
        try:
            save_manifest(sid, manifest, con)
            save_session_state(sid, state, con)
            con.execute("COMMIT")
            imported += 1
        except sqlite3.Error as exc:
            con.execute("ROLLBACK")
            log.warning("Could not import legacy session %s: %s", sid, exc)
    tj = data / "tracker.json"
    if tj.exists():
        try:
            jobs = json.loads(tj.read_text())
            con.execute("BEGIN IMMEDIATE")
            for j in jobs:
                if not con.execute("SELECT 1 FROM jobs WHERE id = ?", (j["id"],)).fetchone():
                    insert_job(j, con)
            con.execute("COMMIT")
            tj.rename(tj.with_suffix(".json.imported"))
        except (OSError, json.JSONDecodeError, sqlite3.Error, KeyError) as exc:
            if con.in_transaction:
                con.execute("ROLLBACK")
            log.warning("Could not import tracker.json: %s", exc)
    if imported:
        log.info("Imported %d session(s) from JSON files into %s", imported, db_path())
