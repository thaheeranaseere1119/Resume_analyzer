from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.db import get_db
from ...core.errors import AppError
from ...core.ratelimit import limiter
from ...models import Resume, ResumeUpload, ResumeVersion, User
from ...schemas.resume import (CorrectionsIn, ProfilePatch, RenameIn, ResumeDetail, ResumeSummary, UploadOut,
                               VersionOut, VersionSummary)
from ...services import audit, resumes as svc, storage
from ...tasks.jobs import enqueue, parse_resume
from ..deps import get_current_user

router = APIRouter(prefix="/resumes", tags=["resumes"])
MIME = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _summary(db: Session, r: Resume) -> ResumeSummary:
    up = r.uploads[0] if r.uploads else None
    n = db.scalar(select(func.count()).select_from(ResumeVersion).where(ResumeVersion.resume_id == r.id)) or 0
    return ResumeSummary.model_validate(r).model_copy(update={
        "version_count": n, "file_format": up.file_format if up else None,
        "original_filename": up.original_filename if up else None})


@router.post("/upload", response_model=ResumeSummary, status_code=202,
             dependencies=[Depends(limiter("upload", "rate_limit_upload_per_hour", 3600))])
async def upload(request: Request, file: UploadFile = File(...), title: str | None = Form(default=None),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "file_too_large", f"The file is larger than {get_settings().max_upload_mb} MB.")
    resume = svc.create_from_upload(db, user, data, file.filename or "resume", file.content_type, title, request)
    up = resume.uploads[0]
    up.task_id = enqueue(parse_resume, str(up.id))
    db.commit()
    db.refresh(resume)
    return _summary(db, resume)


@router.get("", response_model=list[ResumeSummary])
def list_resumes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Resume).where(Resume.user_id == user.id).order_by(Resume.updated_at.desc())).all()
    return [_summary(db, r) for r in rows]


@router.get("/{resume_id}", response_model=ResumeDetail)
def get_resume(resume_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = svc.get_owned_resume(db, user, resume_id)
    out = _summary(db, r).model_dump()
    out["upload"] = UploadOut.model_validate(r.uploads[0]) if r.uploads else None
    out["current_version"] = VersionOut.model_validate(svc.current_version(db, r)) if r.current_version_id else None
    return out


@router.patch("/{resume_id}", response_model=ResumeSummary)
def rename(resume_id: uuid.UUID, body: RenameIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = svc.get_owned_resume(db, user, resume_id)
    r.title = body.title.strip()
    db.commit()
    return _summary(db, r)


@router.get("/{resume_id}/status", response_model=UploadOut)
def processing_status(resume_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = svc.get_owned_resume(db, user, resume_id)
    return r.uploads[0]


@router.get("/{resume_id}/versions", response_model=list[VersionSummary])
def versions(resume_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = svc.get_owned_resume(db, user, resume_id)
    return sorted(r.versions, key=lambda v: -v.version_number)


@router.get("/{resume_id}/versions/{version_id}", response_model=VersionOut)
def version(resume_id: uuid.UUID, version_id: uuid.UUID, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    return svc.get_owned_version(db, user, resume_id, version_id)


@router.get("/{resume_id}/versions/{version_id}/preview")
def version_preview(resume_id: uuid.UUID, version_id: uuid.UUID, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return svc.render_preview(svc.get_owned_version(db, user, resume_id, version_id))


@router.get("/{resume_id}/versions/{version_id}/document")
def version_document(resume_id: uuid.UUID, version_id: uuid.UUID, request: Request,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_version(db, user, resume_id, version_id)
    r = svc.get_owned_resume(db, user, resume_id)
    audit.record(db, "resume.document_downloaded", user_id=user.id, request=request, resource_type="resume_version",
                 resource_id=v.id)
    db.commit()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in r.title)[:60] or "resume"
    return Response(storage.get(v.document_key), media_type=MIME[v.document_format],
                    headers={"Content-Disposition": f'attachment; filename="{safe}_v{v.version_number}.{v.document_format}"'})


@router.get("/{resume_id}/original")
def original(resume_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    r = svc.get_owned_resume(db, user, resume_id)
    up: ResumeUpload = r.uploads[0]
    audit.record(db, "resume.original_downloaded", user_id=user.id, request=request, resource_type="resume",
                 resource_id=r.id)
    db.commit()
    return Response(storage.get(up.storage_key), media_type=MIME[up.file_format],
                    headers={"Content-Disposition": f'attachment; filename="original.{up.file_format}"'})


@router.patch("/{resume_id}/profile", response_model=VersionOut)
def correct_profile(resume_id: uuid.UUID, body: ProfilePatch, request: Request,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fields = body.model_dump(exclude_unset=True, exclude={"expected_version_id"})
    if not fields:
        raise AppError(400, "nothing_to_change", "No profile fields were provided.")
    return svc.correct_version(db, user, resume_id, body.expected_version_id, {}, fields, request)


@router.post("/{resume_id}/corrections", response_model=VersionOut)
def correct_blocks(resume_id: uuid.UUID, body: CorrectionsIn, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    changes = {c.stable_id: c.model_dump(exclude_unset=True, exclude={"stable_id"}) for c in body.blocks}
    return svc.correct_version(db, user, resume_id, body.expected_version_id, changes, None, request)


@router.delete("/{resume_id}", status_code=204)
def delete(resume_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    svc.delete_resume(db, user, resume_id, request)
    return Response(status_code=204)
