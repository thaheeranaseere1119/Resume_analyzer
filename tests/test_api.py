"""HTTP API smoke tests through FastAPI's TestClient."""
from fastapi.testclient import TestClient

from careercopilot.api import app

from conftest import fixture_bytes


def test_full_http_flow(data_dir, jd_text):
    c = TestClient(app)
    r = c.post("/api/sessions", files={"file": ("classic.docx", fixture_bytes("classic.docx"))}, data={"jd_text": jd_text})
    assert r.status_code == 200, r.text
    s = r.json()
    sid = s["session_id"]
    sql = next(x for x in s["recommendations"] if x["keyword"] == "SQL")
    base = f"/api/sessions/{sid}/recommendations/{sql['id']}"
    assert c.post(base + "/simulate", json={"selected": 0}).json()["score"]["points"] > 0
    assert "html" in c.post(base + "/preview", json={"selected": 0}).json()
    assert c.post(base + "/apply", json={"approved": False, "version": 0}).status_code == 403
    r = c.post(base + "/apply", json={"approved": True, "version": 0, "override": {"selected": 0}})
    assert r.status_code == 200 and r.json()["comparison"]["match"]
    assert c.get(f"/api/sessions/{sid}/download").headers["content-disposition"].endswith('_v1.docx"')
    assert c.post(f"/api/sessions/{sid}/undo").json()["head"] == 0
    assert c.post(f"/api/sessions/{sid}/restore", json={"version": 1}).json()["head"] == 1
    reply = c.post(f"/api/sessions/{sid}/chat", json={"message": "what is my score?"}).json()["reply"]
    assert "/100" in reply and "not a prediction of interview" in reply


def test_rejects_bad_uploads(data_dir, jd_text):
    c = TestClient(app)
    r = c.post("/api/sessions", files={"file": ("cv.txt", b"hello")}, data={"jd_text": jd_text})
    assert r.status_code == 400
    r = c.post("/api/sessions", files={"file": ("cv.docx", fixture_bytes("classic.docx"))}, data={"jd_text": "short"})
    assert r.status_code == 400
