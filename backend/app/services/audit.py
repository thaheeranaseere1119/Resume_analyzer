from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from ..core.db import utcnow
from ..core.logging import request_id_var
from ..core.ratelimit import client_ip
from ..core.security import hash_ip
from ..models import AuditEvent


def record(db: Session, action: str, *, user_id: uuid.UUID | None = None, request: Request | None = None,
           resource_type: str | None = None, resource_id: object | None = None, **meta) -> None:
    """Append an audit event. `meta` must contain identifiers/counts only, never document text."""
    db.add(AuditEvent(user_id=user_id, action=action, resource_type=resource_type,
                      resource_id=str(resource_id) if resource_id is not None else None,
                      ip_hash=hash_ip(client_ip(request)) if request is not None else None,
                      request_id=request_id_var.get(), meta=meta, created_at=utcnow()))
