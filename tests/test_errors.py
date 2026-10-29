"""Exception handling: bad uploads, bad requests and unexpected crashes return clear JSON errors."""
import io

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from careercopilot import api
from careercopilot.service import Session, WorkflowError

from conftest import fixture_bytes

JD = "We need a backend engineer with Python, SQL and Docker experience to build REST APIs."


@pytest.fixture
def client(data_dir):
    return TestClient(api.app, raise_server_exceptions=False)


def _upload(client, data, name):
    return client.post("/api/sessions", files={"file": (name, data)}, data={"jd_text": JD})


@pytest.mark.parametrize("data,name,msg", [
    (b"", "cv.pdf", "empty"),
    (b"hello world", "cv.pdf", "not a valid PDF"),
    (b"\xd0\xcf\x11\xe0 old word", "cv.docx", "not a valid Word document"),
    (b"%PDF-1.7 garbage", "cv.pdf", "damaged"),
    (b"data", "cv.txt", ".docx or .pdf"),
])
def test_bad_uploads_give_friendly_errors(client, data, name, msg):
    r = _upload(client, data, name)
    assert 400 <= r.status_code < 500 and msg in r.json()["error"]


def test_password_protected_pdf(client):
    d = fitz.open()
    d.new_page().insert_text((50, 60), "Asha Rao")
    buf = io.BytesIO()
    d.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    r = _upload(client, buf.getvalue(), "cv.pdf")
    assert r.status_code == 400 and "password" in r.json()["error"]


def test_blank_pdf_has_no_text(client):
    d = fitz.open()
    d.new_page()
    r = _upload(client, d.tobytes(), "cv.pdf")
    assert r.status_code == 400 and "No readable text" in r.json()["error"]


def test_unknown_and_malicious_session_ids(client):
    assert client.get("/api/sessions/doesnotexist1").status_code == 404
    assert client.get("/api/sessions/..%2F..%2Fetc").status_code == 404


def test_validation_errors_are_readable(client):
    sid = _upload(client, fixture_bytes("classic.docx"), "classic.docx").json()["session_id"]
    rid = client.get(f"/api/sessions/{sid}").json()["recommendations"][0]["id"]
    r = client.post(f"/api/sessions/{sid}/recommendations/{rid}/answer", json={"text": "hi"})
    assert r.status_code == 422 and r.json()["error"].startswith("Invalid request") and "yes" in r.json()["error"]


def test_unexpected_crash_returns_error_id_and_no_traceback(client, monkeypatch):
    sid = _upload(client, fixture_bytes("classic.docx"), "classic.docx").json()["session_id"]
    monkeypatch.setattr(Session, "summary", lambda self: 1 / 0)
    r = client.get(f"/api/sessions/{sid}")
    body = r.json()
    assert r.status_code == 500 and body["error_id"] and "ZeroDivision" not in body["error"]


def test_corrupt_session_file(data_dir):
    s = Session.create(fixture_bytes("classic.docx"), "classic.docx", JD)
    from careercopilot import db
    db.conn().execute("UPDATE sessions SET state = '{not json' WHERE id = ?", (s.sid,))
    with pytest.raises(WorkflowError) as e:
        Session(s.sid)
    assert "damaged" in str(e.value)
