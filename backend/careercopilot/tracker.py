"""Job application tracker (single local user, JSON file store)."""
from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from . import config

STATUSES = ["saved", "applied", "interview", "offer", "rejected"]
FIELDS = ("company", "title", "url", "location", "notes", "applied_on", "deadline", "salary", "contact", "session_id")
_lock = threading.Lock()


class TrackerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _path() -> Path:
    return Path(config.DATA_DIR) / "tracker.json"


def _load() -> list[dict]:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else []


def _save(jobs: list[dict]):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=2))
    tmp.replace(p)


def _clean(data: dict) -> dict:
    out = {}
    for k in FIELDS:
        if k in data and data[k] is not None:
            v = str(data[k]).strip()
            out[k] = v[:4000] if k == "notes" else v[:300]
    return out


def list_jobs() -> list[dict]:
    with _lock:
        return sorted(_load(), key=lambda j: -j["updated"])


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
    with _lock:
        jobs = _load()
        jobs.append(job)
        _save(jobs)
    return job


def update_job(job_id: str, data: dict) -> dict:
    with _lock:
        jobs = _load()
        job = next((j for j in jobs if j["id"] == job_id), None)
        if job is None:
            raise TrackerError("Job not found", 404)
        job.update(_clean(data))
        status = data.get("status")
        if status and status != job["status"]:
            if status not in STATUSES:
                raise TrackerError("Unknown status")
            job["status"] = status
            job["history"].append({"status": status, "at": time.time()})
            if status == "applied" and not job.get("applied_on"):
                job["applied_on"] = time.strftime("%Y-%m-%d")
        job["updated"] = time.time()
        _save(jobs)
        return job


def delete_job(job_id: str) -> dict:
    with _lock:
        jobs = _load()
        keep = [j for j in jobs if j["id"] != job_id]
        if len(keep) == len(jobs):
            raise TrackerError("Job not found", 404)
        _save(keep)
    return {"deleted": job_id}


def job_for_session(session_id: str) -> dict | None:
    return next((j for j in list_jobs() if j.get("session_id") == session_id), None)
