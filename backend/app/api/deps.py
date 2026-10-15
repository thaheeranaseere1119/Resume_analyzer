"""Request dependencies: DB session, authenticated user, CSRF enforcement."""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..core.db import get_db, utcnow
from ..core.errors import AppError
from ..core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, csrf_matches, decode_access_token
from ..models import AuthSession, User

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(ACCESS_COOKIE), True


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token, via_cookie = _token(request)
    data = decode_access_token(token) if token else None
    if not data:
        raise AppError(401, "unauthenticated", "Please sign in.")
    # Cookie-authenticated state changes require the double-submit CSRF token.
    if via_cookie and request.method in UNSAFE and not csrf_matches(request.cookies.get(CSRF_COOKIE),
                                                                    request.headers.get(CSRF_HEADER)):
        raise AppError(403, "csrf_failed", "Security token missing or invalid. Refresh the page and try again.")
    try:
        sid, uid = uuid.UUID(data["sid"]), uuid.UUID(data["sub"])
    except ValueError:
        raise AppError(401, "unauthenticated", "Please sign in.")
    session = db.get(AuthSession, sid)
    if session is None or session.revoked_at is not None or session.user_id != uid or session.expires_at < utcnow():
        raise AppError(401, "session_revoked", "Your session has ended. Please sign in again.")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise AppError(401, "unauthenticated", "Please sign in.")
    request.state.user_id = user.id
    request.state.session_id = sid
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise AppError(403, "forbidden", "Administrator access required.")
    return user
