"""Phase 2: resume upload, validation, parsing, structured versions, corrections, authorization."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from conftest import FIXTURES

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload(api, name, data=None, mime=None, title=None):
    data = data if data is not None else (FIXTURES / name).read_bytes()
    mime = mime or (DOCX_MIME if name.endswith(".docx") else "application/pdf")
    files = {"file": (name, data, mime)}
    return api.post("/resumes/upload", files=files, data={"title": title} if title else None)


@pytest.mark.parametrize("name", ["classic.docx", "single_column.pdf", "two_column.pdf"])
def test_upload_parses_into_structured_version(user, name):
    r = upload(user, name)
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    d = user.get(f"/resumes/{rid}").json()
    assert d["status"] == "ready" and d["upload"]["status"] == "ready"
    v = d["current_version"]
    assert v["version_number"] == 1 and v["source"] == "upload"
    types = {s["section_type"] for s in v["sections"]}
    assert {"experience", "skills", "education"} <= types
    assert v["profile"]["email"] == "jordan.lee@example.com"
    assert len({b["stable_id"] for b in v["blocks"]}) == len(v["blocks"])
    texts = " ".join(b["text"] for b in v["blocks"])
    assert "Containerized three legacy services" in texts
    assert all(b["char_end"] > b["char_start"] for b in v["blocks"])


def test_scanned_pdf_uses_ocr_with_warning(user):
    rid = upload(user, "scanned.pdf").json()["id"]
    d = user.get(f"/resumes/{rid}").json()
    assert d["upload"]["scanned"] and d["upload"]["ocr_confidence"] > 60
    codes = {w["code"] for w in d["current_version"]["parse_warnings"]}
    assert "ocr_used" in codes
    assert all(b["confidence"] < 1 for b in d["current_version"]["blocks"])


@pytest.mark.parametrize("name,data,mime,status,code", [
    ("resume.txt", b"hello", "text/plain", 415, "unsupported_format"),
    ("fake.pdf", b"not a pdf at all", "application/pdf", 415, "invalid_signature"),
    ("fake.docx", b"%PDF-1.4 something", DOCX_MIME, 415, "invalid_signature"),
    ("empty.pdf", b"", "application/pdf", 400, "empty_file"),
    ("broken.docx", b"PK\x03\x04garbage-zip-data", DOCX_MIME, 400, "corrupt_document"),
    ("wrongmime.pdf", None, "image/png", 415, "mime_mismatch"),
])
def test_invalid_uploads_rejected(user, name, data, mime, status, code):
    if data is None:
        data = (FIXTURES / "single_column.pdf").read_bytes()
    r = upload(user, name, data, mime)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_oversized_upload(user, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = upload(user, "classic.docx")
    assert r.status_code == 413


def test_encrypted_pdf_rejected(user):
    import pymupdf as fitz
    doc = fitz.open(FIXTURES / "single_column.pdf")
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    r = upload(user, "locked.pdf", data)
    assert r.status_code == 400 and r.json()["error"]["code"] == "encrypted_document"


def test_macro_docx_rejected(user):
    src = (FIXTURES / "classic.docx").read_bytes()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(src)) as zin, zipfile.ZipFile(out, "w") as zout:
        for i in zin.infolist():
            zout.writestr(i, zin.read(i.filename))
        zout.writestr("word/vbaProject.bin", b"\x00macro")
    r = upload(user, "macro.docx", out.getvalue())
    assert r.status_code == 415 and r.json()["error"]["code"] == "macros_not_allowed"


def test_other_user_cannot_access_resume(user, other_user):
    rid = upload(user, "classic.docx").json()["id"]
    vid = user.get(f"/resumes/{rid}").json()["current_version_id"]
    for path in (f"/resumes/{rid}", f"/resumes/{rid}/versions", f"/resumes/{rid}/versions/{vid}",
                 f"/resumes/{rid}/original", f"/resumes/{rid}/versions/{vid}/document",
                 f"/resumes/{rid}/versions/{vid}/preview"):
        assert other_user.get(path).status_code == 404, path
    assert other_user.delete(f"/resumes/{rid}").status_code == 404
    assert other_user.post(f"/resumes/{rid}/corrections", json={
        "expected_version_id": vid, "blocks": [{"stable_id": vid, "text": "x"}]}).status_code == 404
    assert other_user.get("/resumes").json() == []
    assert user.get(f"/resumes/{rid}").status_code == 200


def test_unauthenticated_requests_rejected(client):
    assert client.get("/api/v1/resumes").status_code == 401
    assert client.post("/api/v1/resumes/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")}).status_code == 401


def test_corrections_create_new_version_and_keep_original(user):
    rid = upload(user, "classic.docx").json()["id"]
    d = user.get(f"/resumes/{rid}").json()
    v1 = d["current_version"]
    target = next(b for b in v1["blocks"] if "Containerized" in b["text"])
    r = user.post(f"/resumes/{rid}/corrections", json={"expected_version_id": v1["id"], "blocks": [
        {"stable_id": target["stable_id"], "text": "Containerized three legacy services.", "kind": "bullet"}]})
    assert r.status_code == 200, r.text
    v2 = r.json()
    assert v2["version_number"] == 2 and v2["source"] == "correction" and v2["parent_id"] == v1["id"]
    b2 = next(b for b in v2["blocks"] if b["stable_id"] == target["stable_id"])
    assert b2["text"] == "Containerized three legacy services." and b2["user_corrected"]
    assert [b["stable_id"] for b in v2["blocks"]] == [b["stable_id"] for b in v1["blocks"]]
    # stale correction is refused
    r = user.post(f"/resumes/{rid}/corrections", json={"expected_version_id": v1["id"], "blocks": [
        {"stable_id": target["stable_id"], "text": "again"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_version"
    # profile correction
    r = user.patch(f"/resumes/{rid}/profile", json={"expected_version_id": v2["id"], "location": "Chennai, India"})
    assert r.status_code == 200 and r.json()["profile"]["location"] == "Chennai, India"
    # immutable original is byte-identical and still downloadable
    orig = user.get(f"/resumes/{rid}/original")
    assert hashlib.sha256(orig.content).hexdigest() == hashlib.sha256((FIXTURES / "classic.docx").read_bytes()).hexdigest()
    assert len(user.get(f"/resumes/{rid}/versions").json()) == 3


def test_preview_and_delete_removes_files(user):
    rid = upload(user, "single_column.pdf").json()["id"]
    d = user.get(f"/resumes/{rid}").json()
    p = user.get(f"/resumes/{rid}/versions/{d['current_version_id']}/preview").json()
    assert p["format"] == "pdf" and p["pages"][0].startswith("data:image/png;base64,")
    from app.core.config import get_settings
    mine = Path(get_settings().storage_dir) / "u" / user.get("/auth/me").json()["id"]
    assert any(mine.rglob("*.pdf"))
    assert user.delete(f"/resumes/{rid}").status_code == 204
    assert user.get(f"/resumes/{rid}").status_code == 404
    assert not any(mine.rglob("*.pdf"))
