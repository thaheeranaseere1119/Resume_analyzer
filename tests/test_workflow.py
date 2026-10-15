"""End-to-end approval workflow on DOCX and PDF."""
import hashlib
import io
import os
import stat
import zipfile

import pymupdf as fitz
import pytest
from docx import Document

from careercopilot.service import Session, WorkflowError, parse_document
from careercopilot.scoring import score_resume

from conftest import fixture_bytes

FILES = ["classic.docx", "single_column.pdf", "two_column.pdf", "two_column_table.docx"]


def _new(data_dir, name, jd_text):
    return Session.create(fixture_bytes(name), name, jd_text)


def _rec(s, keyword):
    return next(r for r in s.recommendations() if r["keyword"] == keyword)


@pytest.mark.parametrize("name", FILES)
def test_prediction_equals_independent_recalculation(data_dir, jd_text, name):
    s = _new(data_dir, name, jd_text)
    sql = _rec(s, "SQL")
    opt = sql["options"][0]
    assert opt["factual"]["status"] == "evidence" and opt["can_apply"]
    predicted = opt["score"]["after"]
    res = s.apply(sql["id"], None, approved=True, version=s.store.head)
    c = res["comparison"]
    assert c["predicted_after"] == predicted
    # Independent recalculation: fresh session object, fresh parse of the saved bytes.
    s2 = Session(s.sid)
    saved = s2.store.read()
    m, _ = parse_document(saved, s2.ext, s2.as_of)
    assert score_resume(m, s2.jd, s2.weights).overall == c["actual_after"] == predicted
    assert c["match"]


@pytest.mark.parametrize("name", ["classic.docx", "single_column.pdf"])
def test_confirmed_multi_step_flow_and_undo_restore(data_dir, jd_text, name):
    s = _new(data_dir, name, jd_text)
    original = s.store.read(0)
    d = _rec(s, "Docker")
    assert d["status"] == "needs_clarification" and not d["options"][0]["can_apply"]
    with pytest.raises(WorkflowError):  # unanswered → never applied
        s.apply(d["id"], None, approved=True, version=s.store.head)
    assert s.store.head == 0
    s.answer(d["id"], True, "")
    r1 = s.apply(d["id"], None, approved=True, version=s.store.head)
    assert r1["comparison"]["match"]
    j = _rec(s, "Java")
    s.answer(j["id"], True, "i built a java spring boot service for inventory sync with the warehouse API")
    j = _rec(s, "Java")
    assert j["options"][0]["proposed_text"] == "Built a Java Spring Boot service for inventory sync with the warehouse API."
    r2 = s.apply(j["id"], None, approved=True, version=s.store.head)
    assert r2["comparison"]["match"] and s.store.head == 2
    v1 = s.store.read(1)
    assert s.undo()["head"] == 1 and s.store.read() == v1
    assert s.undo()["head"] == 0 and s.store.read() == original
    with pytest.raises(WorkflowError):
        s.undo()
    s.restore(2)
    assert s.store.head == 2
    # The original upload is immutable and byte-identical.
    orig_path = s.root / f"original{s.ext}"
    assert not (os.stat(orig_path).st_mode & stat.S_IWUSR)
    assert orig_path.read_bytes() == fixture_bytes(name)


def test_rejection_never_modifies_document(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    before = hashlib.sha256(s.store.read()).hexdigest()
    sql = _rec(s, "SQL")
    s.reject(sql["id"])
    assert _rec(s, "SQL")["status"] == "rejected"
    with pytest.raises(WorkflowError) as e:
        s.apply(sql["id"], None, approved=True, version=s.store.head)
    assert e.value.status == 409
    assert s.store.head == 0 and hashlib.sha256(s.store.read()).hexdigest() == before
    assert len(s.store.manifest["versions"]) == 1


def test_apply_requires_explicit_approval(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    sql = _rec(s, "SQL")
    with pytest.raises(WorkflowError) as e:
        s.apply(sql["id"], None, approved=False, version=0)
    assert e.value.status == 403 and s.store.head == 0


def test_stale_version_refused(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    sql = _rec(s, "SQL")
    s.apply(sql["id"], None, approved=True, version=0)
    aws = _rec(s, "AWS")
    with pytest.raises(WorkflowError) as e:
        s.apply(aws["id"], None, approved=True, version=0)
    assert e.value.status == 409


def test_edit_introducing_unconfirmed_skill_is_blocked(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    sql = _rec(s, "SQL")
    opt = s.edit_suggestion(sql["id"], {"selected": 0, "text": "Designed PostgreSQL (SQL) schemas on Kubernetes, cutting query time by 40%."})
    assert opt["factual"]["status"] == "clarification" and not opt["can_apply"]
    with pytest.raises(WorkflowError):
        s.apply(sql["id"], {"selected": 0, "text": opt["proposed_text"]}, approved=True, version=0)
    assert s.store.head == 0


def test_denied_skill_is_unsupported_with_learning_project(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    k = _rec(s, "Kubernetes")
    r = s.answer(k["id"], False)
    assert r["status"] == "unsupported" and r["learning_project"]
    assert r["options"] == []


def test_zero_point_option_is_shown_as_zero(data_dir, jd_text):
    s = _new(data_dir, "classic.docx", jd_text)
    sql = _rec(s, "SQL")
    skills = next(o for o in sql["options"] if o["strategy"] == "skills_line")
    # Moving the same text to the Python skills line (already demonstrated) yields no new coverage.
    py_line = next(b for b in s.current()[1].blocks if b.kind == "skills_line" and "Python" in b.text)
    opt = s.simulate(sql["id"], {"selected": sql["options"].index(skills), "block_id": py_line.id,
                                 "text": py_line.body + ", Python"})
    assert opt["score"]["points"] <= 0.0


def test_preview_does_not_save(data_dir, jd_text):
    s = _new(data_dir, "single_column.pdf", jd_text)
    sql = _rec(s, "SQL")
    p = s.preview(sql["id"], None)
    assert p["format"] == "pdf" and p["pages"][0].startswith("data:image/png;base64,")
    assert s.store.head == 0 and len(s.store.manifest["versions"]) == 1


@pytest.mark.parametrize("name", ["classic.docx", "single_column.pdf"])
def test_exported_documents_are_valid(data_dir, jd_text, name):
    s = _new(data_dir, name, jd_text)
    sql = _rec(s, "SQL")
    s.apply(sql["id"], None, approved=True, version=0)
    data, fname = s.download(None)
    assert fname.endswith(f"_v1{s.ext}")
    if s.ext == ".docx":
        Document(io.BytesIO(data))
        assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None
    else:
        d = fitz.open(stream=data, filetype="pdf")
        assert len(d) >= 1 and "PostgreSQL (SQL)" in d[0].get_text()
