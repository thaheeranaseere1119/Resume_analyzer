"""Registration, login with lockout, rotating refresh tokens, password reset, account deletion."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.db import utcnow
from ..core.errors import AppError
from ..core.security import (ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, create_access_token, hash_ip, hash_password,
                             hash_token, needs_rehash, new_csrf_token, new_refresh_token, verify_password)
from ..core.ratelimit import client_ip
from ..models import AuthSession, PasswordResetToken, User, UserProfile
from . import audit, storage

# Constant-time-ish dummy hash so unknown emails take as long as wrong passwords.
_DUMMY_HASH = hash_password("dummy-password-for-timing-1")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def register(db: Session, email: str, password: str, full_name: str | None, request: Request) -> User:
    email = normalize_email(email)
    if db.scalar(select(User).where(User.email == email)):
        raise AppError(409, "email_taken", "An account with this email already exists.")
    user = User(email=email, password_hash=hash_password(password))
    user.profile = UserProfile(full_name=(full_name or "").strip() or None, target_roles=[], preferred_locations=[])
    db.add(user)
    db.flush()
    audit.record(db, "auth.register", user_id=user.id, request=request)
    return user


def authenticate(db: Session, email: str, password: str, request: Request) -> User:
    s = get_settings()
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, _DUMMY_HASH)
        audit.record(db, "auth.login_failed", request=request, reason="unknown_email")
        db.commit()
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")
    if user.locked_until and user.locked_until > utcnow():
        audit.record(db, "auth.login_locked", user_id=user.id, request=request)
        db.commit()
        raise AppError(423, "account_locked", "Too many failed attempts. Try again in a few minutes.")
    if not user.is_active or not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= s.login_max_attempts:
            user.locked_until = utcnow() + timedelta(minutes=s.login_lock_minutes)
            user.failed_logins = 0
        audit.record(db, "auth.login_failed", user_id=user.id, request=request, reason="bad_password")
        db.commit()
        raise AppError(401, "invalid_credentials", "Email or password is incorrect.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, utcnow()
    return user


def start_session(db: Session, user: User, request: Request, response: Response) -> str:
    s = get_settings()
    raw, digest = new_refresh_token()
    session = AuthSession(user_id=user.id, refresh_token_hash=digest,
                          expires_at=utcnow() + timedelta(days=s.refresh_token_days),
                          user_agent=(request.headers.get("user-agent") or "")[:300],
                          ip_hash=hash_ip(client_ip(request)), last_used_at=utcnow())
    db.add(session)
    db.flush()
    csrf = new_csrf_token()
    _set_cookies(response, create_access_token(user.id, session.id), raw, csrf)
    audit.record(db, "auth.login", user_id=user.id, request=request, resource_type="session", resource_id=session.id)
    return csrf


def refresh(db: Session, raw: str | None, request: Request, response: Response) -> tuple[User, str]:
    if not raw:
        raise AppError(401, "unauthenticated", "Please sign in.")
    digest_in = hash_token(raw)
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == digest_in).with_for_update())
    reused = False
    if session is None:
        session = db.scalar(select(AuthSession).where(AuthSession.previous_token_hash == digest_in))
        reused = session is not None
        if session is None:
            raise AppError(401, "unauthenticated", "Please sign in.")
    if reused or session.revoked_at is not None:
        # A revoked token was replayed: assume theft and revoke every session of this user.
        db.execute(update(AuthSession).where(AuthSession.user_id == session.user_id, AuthSession.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))
        audit.record(db, "auth.refresh_reuse_detected", user_id=session.user_id, request=request)
        db.commit()
        raise AppError(401, "session_revoked", "Your session has ended. Please sign in again.")
    if session.expires_at < utcnow():
        raise AppError(401, "session_expired", "Your session has expired. Please sign in again.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AppError(401, "unauthenticated", "Please sign in.")
    new_raw, digest = new_refresh_token()   # rotation
    session.previous_token_hash = session.refresh_token_hash
    session.refresh_token_hash, session.last_used_at = digest, utcnow()
    csrf = new_csrf_token()
    _set_cookies(response, create_access_token(user.id, session.id), new_raw, csrf)
    return user, csrf


def logout(db: Session, session_id: uuid.UUID | None, response: Response) -> None:
    if session_id:
        s = db.get(AuthSession, session_id)
        if s and s.revoked_at is None:
            s.revoked_at = utcnow()
    clear_cookies(response)


def revoke_all(db: Session, user_id: uuid.UUID, except_session: uuid.UUID | None = None) -> int:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_session:
        q = q.where(AuthSession.id != except_session)
    return db.execute(q.values(revoked_at=utcnow())).rowcount or 0


def _set_cookies(response: Response, access: str, refresh_raw: str, csrf: str) -> None:
    s = get_settings()
    common = {"secure": s.cookie_secure, "samesite": "lax", "domain": s.cookie_domain}
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, path="/", max_age=s.access_token_minutes * 60, **common)
    response.set_cookie(REFRESH_COOKIE, refresh_raw, httponly=True, path="/api/v1/auth",
                        max_age=s.refresh_token_days * 86400, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, path="/", max_age=s.refresh_token_days * 86400, **common)


def clear_cookies(response: Response) -> None:
    s = get_settings()
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/v1/auth"), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path, domain=s.cookie_domain)


# ---------------------------------------------------------------- password reset
def request_password_reset(db: Session, email: str, request: Request) -> str | None:
    """Creates a reset token and emails it when SMTP is configured. Always returns the same
    response to the caller so account existence is not revealed. Returns the raw token for tests."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        return None
    raw, digest = new_refresh_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=digest, expires_at=utcnow() + timedelta(minutes=30)))
    audit.record(db, "auth.password_reset_requested", user_id=user.id, request=request)
    from .email import send_password_reset
    send_password_reset(user.email, raw)
    return raw


def confirm_password_reset(db: Session, raw: str, new_password: str, request: Request) -> None:
    tok = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw)))
    if tok is None or tok.used_at is not None or tok.expires_at < utcnow():
        raise AppError(400, "invalid_token", "This reset link is invalid or has expired.")
    user = db.get(User, tok.user_id)
    user.password_hash = hash_password(new_password)
    tok.used_at = utcnow()
    revoke_all(db, user.id)
    audit.record(db, "auth.password_reset", user_id=user.id, request=request)


def delete_account(db: Session, user: User, password: str, request: Request, response: Response) -> None:
    if not verify_password(password, user.password_hash):
        raise AppError(403, "invalid_credentials", "Password is incorrect.")
    uid = user.id
    audit.record(db, "account.deleted", user_id=None, request=request, deleted_user=str(uid))
    db.delete(user)  # cascades to resumes, versions, analyses, letters, tracker data
    db.flush()
    storage.delete_user_files(uid)
    clear_cookies(response)
