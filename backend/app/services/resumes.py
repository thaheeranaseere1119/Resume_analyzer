"""Resume library: upload, parsing into versioned structure, corrections, deletion, previews."""
from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..core.db import utcnow
from ..core.errors import AppError, NotFound
from ..models import Resume, ResumeBlock, ResumeChange, ResumeSection, ResumeUpload, ResumeVersion, User
from ..parsing.structure import ParsedResume, align_stable_ids, build_structure, parse_document
from ..parsing.validation import validate_upload
from . import audit, storage

log = logging.getLogger("app.resumes")


# ------------------------------------------------------------------ ownership
def get_owned_resume(db: Session, user: User, resume_id: uuid.UUID, *, lock: bool = False) -> Resume:
    q = select(Resume).where(Resume.id == resume_id, Resume.user_id == user.id)
    if lock:
        q = q.with_for_update()
    r = db.scalar(q)
    if r is None:
        raise NotFound("Resume")
    return r


def get_owned_version(db: Session, user: User, resume_id: uuid.UUID, version_id: uuid.UUID) -> ResumeVersion:
    v = db.scalar(select(ResumeVersion).join(Resume, Resume.id == ResumeVersion.resume_id).where(ResumeVersion.id == version_id,
                                                           ResumeVersion.resume_id == resume_id,
                                                           Resume.user_id == user.id)
                  .options(selectinload(ResumeVersion.sections), selectinload(ResumeVersion.blocks)))
    if v is None:
        raise NotFound("Resume version")
    return v


def current_version(db: Session, resume: Resume) -> ResumeVersion:
    if resume.current_version_id is None:
        raise AppError(409, "resume_not_ready", "This resume is still being processed.")
    return db.scalar(select(ResumeVersion).where(ResumeVersion.id == resume.current_version_id)
                     .options(selectinload(ResumeVersion.sections), selectinload(ResumeVersion.blocks)))


# ------------------------------------------------------------------ upload
def create_from_upload(db: Session, user: User, data: bytes, filename: str, mime: str | None, title: str | None,
                       request: Request) -> Resume:
    v = validate_upload(data, filename, mime)
    key, sha = storage.put(user.id, v.data, v.file_format)
    resume = Resume(user_id=user.id, title=(title or v.filename.rsplit(".", 1)[0])[:200].strip() or "Resume",
                    status="processing")
    db.add(resume)
    db.flush()
    db.add(ResumeUpload(resume_id=resume.id, storage_key=key, original_filename=v.filename, mime_type=v.mime_type,
                        file_format=v.file_format, size_bytes=len(v.data), sha256=sha, page_count=v.page_count,
                        scanned=v.needs_ocr, status="pending"))
    audit.record(db, "resume.uploaded", user_id=user.id, request=request, resource_type="resume",
                 resource_id=resume.id, size=len(v.data), fmt=v.file_format, needs_ocr=v.needs_ocr)
    db.commit()
    return resume


def process_upload(db: Session, upload_id: uuid.UUID) -> None:
    """Parse an uploaded file into version 1. Runs in the Celery worker."""
    up = db.get(ResumeUpload, upload_id)
    if up is None:
        return
    resume = db.get(Resume, up.resume_id)
    up.status = "processing"
    db.commit()
    try:
        data = storage.get(up.storage_key)
        model, _layout = parse_document(data, up.file_format, date.today())
        parsed = build_structure(model)
        if not parsed.blocks:
            raise ValueError("no text could be extracted")
        up.scanned, up.ocr_confidence, up.page_count = parsed.scanned, parsed.ocr_confidence, parsed.page_count
        version = persist_version(db, resume, parsed, document_key=up.storage_key, document_format=up.file_format,
                                  sha256=up.sha256, parent=None, source="upload", label="Original upload",
                                  created_by=resume.user_id)
        resume.current_version_id = version.id
        resume.status, up.status = "ready", "ready"
        db.commit()
    except Exception as exc:  # parsing failures are reported, never fatal
        db.rollback()
        log.exception("resume parsing failed")
        up = db.get(ResumeUpload, upload_id)
        resume = db.get(Resume, up.resume_id)
        up.status, resume.status = "failed", "failed"
        up.error = ("We could not read text from this file. If it is a scan, make sure it is clear and upright; "
                    "otherwise export it again from your editor.") if "no text" in str(exc) or "tesseract" in str(exc).lower() \
            else "The document could not be processed."
        db.commit()


def persist_version(db: Session, resume: Resume, parsed: ParsedResume, *, document_key: str, document_format: str,
                    sha256: str, parent: ResumeVersion | None, source: str, label: str, created_by: uuid.UUID | None,
                    hints: dict[int, uuid.UUID] | None = None, profile: dict | None = None,
                    job_description_id: uuid.UUID | None = None) -> ResumeVersion:
    number = (db.scalar(select(func.max(ResumeVersion.version_number)).where(ResumeVersion.resume_id == resume.id)) or 0) + 1
    prev = [(b.stable_id, b.text) for b in parent.blocks] if parent is not None else []
    stable = align_stable_ids(prev, [b.text for b in parsed.blocks], hints)
    version = ResumeVersion(resume_id=resume.id, parent_id=parent.id if parent else None, version_number=number,
                            label=label[:200], source=source, document_key=document_key,
                            document_format=document_format, sha256=sha256, page_count=parsed.page_count,
                            parse_warnings=parsed.warnings, profile=profile or parsed.profile, created_by=created_by,
                            created_at=utcnow(), job_description_id=job_description_id or
                            (parent.job_description_id if parent else None))
    db.add(version)
    db.flush()
    section_rows = []
    for i, s in enumerate(parsed.sections):
        row = ResumeSection(version_id=version.id, section_type=s.section_type, heading=s.heading, position=i,
                            page=s.page, confidence=s.confidence, warnings=s.warnings)
        db.add(row)
        section_rows.append(row)
    db.flush()
    for pos, (b, sid) in enumerate(zip(parsed.blocks, stable)):
        db.add(ResumeBlock(version_id=version.id, section_id=section_rows[b.section_index].id, stable_id=sid,
                           position=pos, kind=b.kind, text=b.text, doc_ref=b.doc_ref, entry_key=b.entry_key,
                           page=b.page, char_start=b.char_start, char_end=b.char_end, confidence=b.confidence))
    db.flush()
    db.refresh(version)
    return version


# ------------------------------------------------------------------ corrections
def correct_version(db: Session, user: User, resume_id: uuid.UUID, expected_version_id: uuid.UUID,
                    block_changes: dict[uuid.UUID, dict], profile: dict | None, request: Request) -> ResumeVersion:
    """Apply parser corrections as a new version. The document file is unchanged; the structured
    representation (used for analysis and template exports) records the corrected values."""
    resume = get_owned_resume(db, user, resume_id, lock=True)
    if resume.current_version_id != expected_version_id:
        raise AppError(409, "stale_version", "The resume changed since you opened it. Reload and try again.")
    cur = current_version(db, resume)
    number = (db.scalar(select(func.max(ResumeVersion.version_number)).where(ResumeVersion.resume_id == resume.id)) or 0) + 1
    new = ResumeVersion(resume_id=resume.id, parent_id=cur.id, version_number=number,
                        label="Corrected extracted content", source="correction", document_key=cur.document_key,
                        document_format=cur.document_format, sha256=cur.sha256, page_count=cur.page_count,
                        parse_warnings=cur.parse_warnings, profile={**cur.profile, **(profile or {})},
                        created_by=user.id, created_at=utcnow(), job_description_id=cur.job_description_id)
    db.add(new)
    db.flush()
    sec_map = {}
    sections_by_type = {}
    for s in cur.sections:
        ns = ResumeSection(version_id=new.id, section_type=s.section_type, heading=s.heading, position=s.position,
                           page=s.page, confidence=s.confidence, warnings=s.warnings)
        db.add(ns)
        sec_map[s.id] = ns
        sections_by_type.setdefault(s.section_type, ns)
    db.flush()
    unknown = set(block_changes) - {b.stable_id for b in cur.blocks}
    if unknown:
        raise NotFound("Block")
    for b in cur.blocks:
        ch = block_changes.get(b.stable_id, {})
        section = sec_map.get(b.section_id)
        if ch.get("section_type") and ch["section_type"] != (section.section_type if section else None):
            section = sections_by_type.get(ch["section_type"])
            if section is None:
                section = ResumeSection(version_id=new.id, section_type=ch["section_type"], heading="",
                                        position=len(sec_map) + len(sections_by_type), confidence=1.0, warnings=[])
                db.add(section)
                db.flush()
                sections_by_type[ch["section_type"]] = section
        db.add(ResumeBlock(version_id=new.id, section_id=section.id if section else None, stable_id=b.stable_id,
                           position=b.position, kind=ch.get("kind", b.kind), text=ch.get("text", b.text),
                           doc_ref=b.doc_ref, entry_key=b.entry_key, page=b.page, char_start=b.char_start,
                           char_end=b.char_end, confidence=1.0 if ch else b.confidence,
                           user_corrected=bool(ch) or b.user_corrected))
        if ch.get("text") and ch["text"] != b.text:
            db.add(ResumeChange(resume_id=resume.id, from_version_id=cur.id, to_version_id=new.id, action="correction",
                                block_stable_id=b.stable_id, before_text=b.text, after_text=ch["text"],
                                user_id=user.id, created_at=utcnow()))
    resume.current_version_id = new.id
    audit.record(db, "resume.corrected", user_id=user.id, request=request, resource_type="resume",
                 resource_id=resume.id, blocks=len(block_changes), profile=bool(profile))
    db.commit()
    return get_owned_version(db, user, resume.id, new.id)


# ------------------------------------------------------------------ deletion
def delete_resume(db: Session, user: User, resume_id: uuid.UUID, request: Request) -> None:
    resume = get_owned_resume(db, user, resume_id, lock=True)
    keys = {u.storage_key for u in resume.uploads} | {v.document_key for v in resume.versions}
    from ..models import Export
    keys |= set(db.scalars(select(Export.storage_key).join(ResumeVersion, ResumeVersion.id == Export.resume_version_id)
                           .where(ResumeVersion.resume_id == resume.id, Export.storage_key.is_not(None))))
    resume.current_version_id = None
    db.flush()
    db.delete(resume)
    audit.record(db, "resume.deleted", user_id=user.id, request=request, resource_type="resume", resource_id=resume_id)
    db.commit()
    for k in keys:
        try:
            storage.delete(k)
        except (OSError, ValueError):
            log.warning("could not delete stored file")


# ------------------------------------------------------------------ previews
def render_preview(version: ResumeVersion, highlight_refs: list | None = None) -> dict:
    from careercopilot.preview import docx_preview, pdf_preview
    data = storage.get(version.document_key)
    if version.document_format == "docx":
        return {"format": "docx", "html": docx_preview(data, highlight_refs or []),
                "fidelity": "HTML approximation of the Word layout. Download the file for exact rendering."}
    return {"format": "pdf", "pages": pdf_preview(data, highlight_refs or [], dpi=100),
            "fidelity": "Exact rendering of the PDF pages."}
