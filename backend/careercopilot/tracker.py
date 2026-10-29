"""Job application tracker (single local user, SQLite tables `jobs` and `job_status_history`)."""
from __future__ import annotations

import secrets
import time

from . import db

STATUSES = ["saved", "applied", "interview", "offer", "rejected"]
FIELDS = db.JOB_FIELDS


class TrackerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean(data: dict) -> dict:
    out = {}
    for k in FIELDS:
        if k in data and data[k] is not None:
            v = str(data[k]).strip()
            out[k] = v[:4000] if k == "notes" else v[:300]
    return out


def list_jobs() -> list[dict]:
    return db.list_jobs()


def create_job(data: dict) -> dict:
    fields = _clean(data)
    if not fields.get("company") and not fields.get("title"):
        raise TrackerError("Add at least a company or a job title")
    status = data.get("status", "saved")
    if status not in STATUSES:
        raise TrackerError("Unknown status")
    now = time.time()
    job = {"id": secrets.token_hex(5), **{k: "" for k in FIELDS}, **fields, "status": status,
           "created": now, "updated": now, "history": [{"status": status, "at": now}]}
    db.insert_job(job)
    return job


def update_job(job_id: str, data: dict) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise TrackerError("Job not found", 404)
    fields = _clean(data)
    status = data.get("status")
    new_status = None
    if status and status != job["status"]:
        if status not in STATUSES:
            raise TrackerError("Unknown status")
        new_status = status
        if status == "applied" and not (fields.get("applied_on") or job.get("applied_on")):
            fields["applied_on"] = time.strftime("%Y-%m-%d")
    db.update_job(job_id, fields, new_status, time.time())
    return db.get_job(job_id)


def delete_job(job_id: str) -> dict:
    if not db.delete_job(job_id):
        raise TrackerError("Job not found", 404)
    return {"deleted": job_id}


def job_for_session(session_id: str) -> dict | None:
    return next((j for j in list_jobs() if j.get("session_id") == session_id), None)
