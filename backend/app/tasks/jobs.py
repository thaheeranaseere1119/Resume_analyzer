"""Background tasks. Each task opens its own DB session and records status on the owning row."""
from __future__ import annotations

import uuid

from ..core.db import SessionLocal
from .celery_app import celery


@celery.task(name="ats.ping")
def ping() -> str:
    return "pong"


@celery.task(name="ats.parse_resume", bind=True, max_retries=0)
def parse_resume(self, upload_id: str) -> str:
    from ..services.resumes import process_upload
    with SessionLocal() as db:
        process_upload(db, uuid.UUID(upload_id))
    return upload_id


def enqueue(task, *args) -> str:
    """Dispatch to the worker; falls back to in-process execution if the broker is unreachable."""
    try:
        return task.delay(*args).id
    except Exception:
        task.apply(args=args)
        return "inline"
