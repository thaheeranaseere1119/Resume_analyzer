"""FastAPI application: REST endpoints + static frontend."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, tracker
from .config import COMPONENT_LABELS
from .service import Session, WorkflowError
from .tracker import TrackerError

FRONTEND = Path(__file__).resolve().parents[2] / "legacy_frontend"
MAX_UPLOAD = 15 * 1024 * 1024

app = FastAPI(title="CareerCopilot AI", version="1.0")


@app.exception_handler(WorkflowError)
async def _wf_error(_: Request, exc: WorkflowError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


class Override(BaseModel):
    selected: int | None = None
    mode: str | None = None
    block_id: str | None = None
    text: str | None = None


class ApplyBody(BaseModel):
    override: Override | None = None
    approved: bool = False
    version: int | None = None


class AnswerBody(BaseModel):
    yes: bool
    text: str = ""
    anchor_id: str | None = None


class RestoreBody(BaseModel):
    version: int


class ChatBody(BaseModel):
    message: str


def _ov(o: Override | None) -> dict | None:
    return {k: v for k, v in o.model_dump().items() if v is not None} if o else None


@app.exception_handler(TrackerError)
async def _tr_error(_: Request, exc: TrackerError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


@app.get("/api/meta")
def meta():
    return {"llm": {"configured": llm.configured(), "model": llm.MODEL}, "statuses": tracker.STATUSES}


@app.get("/api/sessions")
def list_sessions():
    return Session.list_all()


@app.post("/api/sessions")
async def create_session(file: UploadFile = File(...), jd_text: str = Form(...), company: str = Form(""),
                         role: str = Form(""), url: str = Form(""), track: bool = Form(True)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise WorkflowError("File is larger than 15 MB")
    s = Session.create(data, file.filename or "resume", jd_text, company, role)
    if track:
        tracker.create_job({"company": company, "title": s.state["role"] or "Untitled role", "url": url,
                            "session_id": s.sid, "status": "saved"})
    return s.summary()


class SampleBody(BaseModel):
    format: str = "docx"


SAMPLES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


@app.post("/api/sample")
def sample(body: SampleBody):
    name = {"docx": "classic.docx", "pdf": "single_column.pdf", "two_column": "two_column.pdf"}.get(body.format, "classic.docx")
    s = Session.create((SAMPLES / name).read_bytes(), name, (SAMPLES / "job_description.txt").read_text(),
                       "Northwind Commerce", "Senior Backend Engineer")
    tracker.create_job({"company": "Northwind Commerce", "title": "Senior Backend Engineer", "session_id": s.sid,
                        "status": "saved", "location": "Remote"})
    return s.summary()


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return Session(sid).summary()


@app.post("/api/sessions/{sid}/recommendations/{rid}/simulate")
def simulate(sid: str, rid: str, body: Override):
    return Session(sid).simulate(rid, _ov(body))


@app.post("/api/sessions/{sid}/recommendations/{rid}/edit")
def edit(sid: str, rid: str, body: Override):
    return Session(sid).edit_suggestion(rid, _ov(body))


@app.post("/api/sessions/{sid}/recommendations/{rid}/preview")
def preview(sid: str, rid: str, body: Override):
    return Session(sid).preview(rid, _ov(body))


@app.post("/api/sessions/{sid}/recommendations/{rid}/apply")
def apply(sid: str, rid: str, body: ApplyBody):
    return Session(sid).apply(rid, _ov(body.override), body.approved, body.version)


@app.post("/api/sessions/{sid}/recommendations/{rid}/reject")
def reject(sid: str, rid: str):
    return Session(sid).reject(rid)


@app.post("/api/sessions/{sid}/recommendations/{rid}/unreject")
def unreject(sid: str, rid: str):
    return Session(sid).restore_rejected(rid)


@app.post("/api/sessions/{sid}/recommendations/{rid}/answer")
def answer(sid: str, rid: str, body: AnswerBody):
    return Session(sid).answer(rid, body.yes, body.text, body.anchor_id)


@app.post("/api/sessions/{sid}/recommendations/{rid}/claude")
def claude_rewrite(sid: str, rid: str, body: Override):
    return Session(sid).claude_rewrite(rid, _ov(body))


class LetterBody(BaseModel):
    tone: str = "professional"
    length: str = "standard"
    hiring_manager: str = ""
    notes: str = ""


class LetterEdit(BaseModel):
    greeting: str
    paragraphs: list[str]
    closing: str


@app.get("/api/sessions/{sid}/letters")
def letters(sid: str):
    return Session(sid).letters()


@app.post("/api/sessions/{sid}/letters")
def create_letter(sid: str, body: LetterBody):
    if body.tone not in ("professional", "warm", "confident", "concise") or body.length not in ("short", "standard"):
        raise WorkflowError("Unknown tone or length")
    return Session(sid).create_letter(tone=body.tone, length=body.length, hiring_manager=body.hiring_manager[:120],
                                      notes=body.notes[:3000])


@app.put("/api/sessions/{sid}/letters/{lid}")
def update_letter(sid: str, lid: str, body: LetterEdit):
    return Session(sid).update_letter(lid, body.greeting, body.paragraphs, body.closing)


@app.delete("/api/sessions/{sid}/letters/{lid}")
def delete_letter(sid: str, lid: str):
    return Session(sid).delete_letter(lid)


@app.get("/api/sessions/{sid}/letters/{lid}/download")
def download_letter(sid: str, lid: str, format: str = "docx"):
    data, name, mt = Session(sid).export_letter(lid, "txt" if format == "txt" else "docx")
    return Response(data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class JobBody(BaseModel):
    company: str | None = None
    title: str | None = None
    url: str | None = None
    location: str | None = None
    notes: str | None = None
    applied_on: str | None = None
    deadline: str | None = None
    salary: str | None = None
    contact: str | None = None
    session_id: str | None = None
    status: str | None = None


@app.get("/api/jobs")
def jobs():
    sessions = {s["session_id"]: s for s in Session.list_all()}
    out = []
    for j in tracker.list_jobs():
        s = sessions.get(j.get("session_id") or "")
        out.append({**j, "session": s})
    return out


@app.post("/api/jobs")
def create_job(body: JobBody):
    return tracker.create_job({k: v for k, v in body.model_dump().items() if v is not None})


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: str, body: JobBody):
    return tracker.update_job(job_id, {k: v for k, v in body.model_dump().items() if v is not None})


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    return tracker.delete_job(job_id)


@app.get("/api/sessions/{sid}/format-audit")
def format_audit(sid: str):
    return Session(sid).format_audit()


@app.get("/api/sessions/{sid}/ats-explained")
def ats_explained(sid: str):
    return Session(sid).ats_explained()


@app.get("/api/sessions/{sid}/eligible-jobs")
def eligible_jobs(sid: str):
    return Session(sid).eligible_jobs()


class EditorBody(BaseModel):
    draft: dict


class ImproveBody(BaseModel):
    text: str
    kind: str = "paragraph"


@app.get("/api/sessions/{sid}/editor")
def editor_get(sid: str):
    return Session(sid).editor_get()


@app.put("/api/sessions/{sid}/editor")
def editor_save(sid: str, body: EditorBody):
    return Session(sid).editor_save(body.draft)


@app.post("/api/sessions/{sid}/editor/reset")
def editor_reset(sid: str):
    return Session(sid).editor_reset()


@app.post("/api/sessions/{sid}/editor/improve")
def editor_improve(sid: str, body: ImproveBody):
    return Session(sid).editor_improve(body.text, body.kind)


@app.post("/api/sessions/{sid}/editor/apply-to-original")
def editor_apply(sid: str):
    return Session(sid).editor_apply_to_original()


@app.get("/api/sessions/{sid}/editor/export")
def editor_export(sid: str, format: str = "docx"):
    data, name, mt = Session(sid).editor_export("pdf" if format == "pdf" else "docx")
    return Response(data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/sessions/{sid}/undo")
def undo(sid: str):
    return Session(sid).undo()


@app.post("/api/sessions/{sid}/restore")
def restore(sid: str, body: RestoreBody):
    return Session(sid).restore(body.version)


@app.get("/api/sessions/{sid}/document")
def document(sid: str, version: int | None = None):
    return Session(sid).document(version)


@app.get("/api/sessions/{sid}/download")
def download(sid: str, version: int | None = None):
    data, name = Session(sid).download(version)
    mt = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if name.endswith(".docx")
          else "application/pdf")
    return Response(data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/sessions/{sid}/chat")
def chat(sid: str, body: ChatBody):
    """Deterministic conversational router. It explains data the engine computed;
    it never invents scores or edits the document."""
    s = Session(sid)
    msg = body.message.lower().strip()
    summ = s.summary()
    sc = summ["score"]
    if re.search(r"\b(score|how am i|how do i|rating|match)\b", msg):
        parts = [f"Your current compatibility score is **{sc['overall']}/100** (methodology {sc['methodology']})."]
        for c in sc["components"].values():
            if c["applicable"]:
                parts.append(f"- {c['label']}: {round(100 * c['score'])}/100 (weight {round(100 * c['weight'])}%)")
        parts.append("This is a keyword and evidence compatibility estimate, not a prediction of interview or hiring odds.")
        return {"reply": "\n".join(parts)}
    if re.search(r"missing|gap|remaining|left|unmet", msg):
        rem = summ["remaining"]
        if not rem:
            return {"reply": "Every requirement I can detect in the job description is met."}
        lines = [f"- {r['name']} ({r['importance']}, {r['status']})" for r in rem]
        return {"reply": "Requirements not yet fully satisfied:\n" + "\n".join(lines)}
    if re.search(r"\bundo\b", msg):
        return {"reply": "Use **Undo** on the last applied change in the change log, or restore any version from the history.",
                "action": "show_history"}
    if re.search(r"download|export", msg):
        return {"reply": "Use **Download** above the document to get the current version.", "action": "download"}
    if re.search(r"weight|calculat|method|how.*work", msg):
        w = ", ".join(f"{COMPONENT_LABELS[k]} {round(100 * v)}%" for k, v in sc["weights"].items())
        return {"reply": f"Overall score = sum of each component (0–1) × its weight, scaled to 100. Weights: {w}. "
                         "A skill earns credit once, at its strongest evidence: demonstrated in experience (100%), "
                         "summary or tech line (70%), implied by a related demonstrated skill (50%) or skills list only (35%). "
                         "Repeating a keyword never adds points."}
    return {"reply": "I can explain your **score**, list **missing** requirements, or walk you through each recommendation "
                     "card below. Approve a card with *Add to Existing Resume* or *Replace Existing Text*; nothing changes "
                     "until you do."}


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
