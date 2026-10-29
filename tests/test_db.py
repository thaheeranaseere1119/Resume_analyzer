"""SQLite storage: sessions, versions, events, tracker, and import of old JSON files."""
import json

from careercopilot import db, tracker
from careercopilot.service import Session

from conftest import fixture_bytes

JD = "We need a backend engineer with Python, SQL and Docker experience to build REST APIs."


def test_tables_exist(data_dir):
    names = {r[0] for r in db.conn().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"sessions", "versions", "events", "jobs", "job_status_history"} <= names
    assert (data_dir / "careercopilot.db").exists()


def test_session_and_versions_round_trip(data_dir, jd_text):
    s = Session.create(fixture_bytes("classic.docx"), "classic.docx", jd_text, "Acme", "Backend")
    sql = next(r for r in s.recommendations() if r["keyword"] == "SQL")
    s.apply(sql["id"], None, approved=True, version=0)
    s.undo()
    c = db.conn()
    row = c.execute("SELECT company, role, head_version, ext FROM sessions WHERE id = ?", (s.sid,)).fetchone()
    assert tuple(row) == ("Acme", "Backend", 0, ".docx")
    assert c.execute("SELECT COUNT(*) FROM versions WHERE session_id = ?", (s.sid,)).fetchone()[0] == 2
    assert [r[0] for r in c.execute("SELECT type FROM events WHERE session_id = ? ORDER BY seq", (s.sid,))] == \
        ["upload", "apply", "undo"]
    again = Session(s.sid)
    assert again.store.head == 0 and len(again.store.manifest["versions"]) == 2
    assert Session.list_all()[0]["session_id"] == s.sid
    assert not (s.root / "session.json").exists() and not (s.root / "manifest.json").exists()


def test_tracker_in_db_with_history(data_dir):
    j = tracker.create_job({"company": "Acme", "title": "Backend Engineer"})
    j = tracker.update_job(j["id"], {"status": "applied", "notes": "Referred by Sam"})
    assert j["status"] == "applied" and j["applied_on"] and [h["status"] for h in j["history"]] == ["saved", "applied"]
    assert tracker.list_jobs()[0]["notes"] == "Referred by Sam"
    tracker.delete_job(j["id"])
    assert db.conn().execute("SELECT COUNT(*) FROM job_status_history").fetchone()[0] == 0  # cascade


def test_old_json_files_are_imported(data_dir):
    root = data_dir / "sessions" / "legacyAbc123"
    (root / "versions").mkdir(parents=True)
    (root / "original.docx").write_bytes(fixture_bytes("classic.docx"))
    import hashlib
    sha = hashlib.sha256(fixture_bytes("classic.docx")).hexdigest()
    (root / "manifest.json").write_text(json.dumps({"filename": "cv.docx", "ext": ".docx", "head": 0,
        "versions": [{"id": 0, "file": "original.docx", "parent": None, "created": 1, "label": "Original upload",
                      "sha256": sha, "change": None}], "events": [{"type": "upload", "at": 1, "version": 0}]}))
    (root / "session.json").write_text(json.dumps({"jd_text": JD, "weights": {"keyword_coverage": 1.0},
        "methodology": "x", "as_of": "2026-10-15", "created": 1, "confirmations": {}, "denials": ["aws"],
        "answers": {}, "rejected": {}, "edited": {}, "letters": [], "company": "Old Co", "role": "Dev"}))
    (data_dir / "tracker.json").write_text(json.dumps([{"id": "j1", "company": "Old Co", "title": "Dev",
        "status": "applied", "created": 1, "updated": 2, "history": [{"status": "applied", "at": 1}]}]))
    s = Session("legacyAbc123")
    assert s.state["company"] == "Old Co" and s.state["denials"] == ["aws"] and s.store.read()
    assert tracker.list_jobs()[0]["id"] == "j1"
    assert (data_dir / "tracker.json.imported").exists()
