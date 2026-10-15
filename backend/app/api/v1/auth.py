from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.db import get_db, utcnow
from ...core.errors import AppError, NotFound
from ...core.ratelimit import limiter
from ...core.security import CSRF_COOKIE, REFRESH_COOKIE, hash_password, new_csrf_token, verify_password
from ...models import AuthSession, User
from ...schemas.auth import (AuthOut, ChangePasswordIn, DeleteAccountIn, LoginIn, PasswordResetConfirmIn,
                             PasswordResetRequestIn, ProfileOut, ProfileUpdate, RegisterIn, SessionOut, UserOut)
from ...services import audit
from ...services import auth as svc
from ...services import email as email_svc
from ..deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])
account = APIRouter(prefix="/account", tags=["account"])
auth_limit = Depends(limiter("auth", "rate_limit_auth_per_minute", 60))


def _auth_out(user: User, csrf: str) -> AuthOut:
    return AuthOut(user=UserOut.model_validate(user), csrf_token=csrf,
                   access_expires_in=get_settings().access_token_minutes * 60)


@router.post("/register", response_model=AuthOut, status_code=201, dependencies=[auth_limit])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = svc.register(db, body.email, body.password, body.full_name, request)
    csrf = svc.start_session(db, user, request, response)
    db.commit()
    db.refresh(user)
    return _auth_out(user, csrf)


@router.post("/login", response_model=AuthOut, dependencies=[auth_limit])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = svc.authenticate(db, body.email, body.password, request)
    csrf = svc.start_session(db, user, request, response)
    db.commit()
    return _auth_out(user, csrf)


@router.post("/refresh", response_model=AuthOut, dependencies=[auth_limit])
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    user, csrf = svc.refresh(db, request.cookies.get(REFRESH_COOKIE), request, response)
    db.commit()
    return _auth_out(user, csrf)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.logout(db, request.state.session_id, response)
    audit.record(db, "auth.logout", user_id=user.id, request=request)
    db.commit()
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/csrf")
def csrf(request: Request, response: Response, user: User = Depends(get_current_user)):
    """Re-issue the CSRF cookie (e.g. after the browser cleared it)."""
    token = request.cookies.get(CSRF_COOKIE) or new_csrf_token()
    s = get_settings()
    response.set_cookie(CSRF_COOKIE, token, httponly=False, path="/", secure=s.cookie_secure, samesite="lax",
                        domain=s.cookie_domain, max_age=s.refresh_token_days * 86400)
    return {"csrf_token": token}


@router.post("/password-reset/request", status_code=202, dependencies=[auth_limit])
def password_reset_request(body: PasswordResetRequestIn, request: Request, db: Session = Depends(get_db)):
    if not email_svc.configured():
        raise AppError(503, "email_not_configured",
                       "Password reset by email is not configured on this server. Contact the administrator.")
    svc.request_password_reset(db, body.email, request)
    db.commit()
    return {"message": "If an account exists for that email, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=204, dependencies=[auth_limit])
def password_reset_confirm(body: PasswordResetConfirmIn, request: Request, db: Session = Depends(get_db)):
    svc.confirm_password_reset(db, body.token, body.new_password, request)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- account
@account.get("/profile", response_model=ProfileOut)
def get_profile(user: User = Depends(get_current_user)):
    return user.profile


@account.patch("/profile", response_model=ProfileOut)
def update_profile(body: ProfileUpdate, request: Request, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    prof = user.profile
    data = body.model_dump(exclude_unset=True)
    if "llm_consent" in data:
        prof.llm_consent_at = utcnow() if data["llm_consent"] else None
        audit.record(db, "privacy.llm_consent", user_id=user.id, request=request, granted=bool(data["llm_consent"]))
    for k, v in data.items():
        setattr(prof, k, [x.strip()[:100] for x in v if x.strip()] if isinstance(v, list) else v)
    db.commit()
    return prof


@account.post("/password", status_code=204)
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise AppError(403, "invalid_credentials", "Current password is incorrect.")
    user.password_hash = hash_password(body.new_password)
    svc.revoke_all(db, user.id, except_session=request.state.session_id)
    audit.record(db, "auth.password_changed", user_id=user.id, request=request)
    db.commit()
    return Response(status_code=204)


@account.get("/sessions", response_model=list[SessionOut])
def sessions(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None),
                                                AuthSession.expires_at > utcnow())
                      .order_by(AuthSession.created_at.desc())).all()
    return [SessionOut.model_validate(r).model_copy(update={"current": r.id == request.state.session_id}) for r in rows]


@account.delete("/sessions/{session_id}", status_code=204)
def revoke_session(session_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    s = db.get(AuthSession, session_id)
    if s is None or s.user_id != user.id:
        raise NotFound("Session")
    s.revoked_at = utcnow()
    audit.record(db, "auth.session_revoked", user_id=user.id, request=request, resource_type="session",
                 resource_id=session_id)
    db.commit()
    return Response(status_code=204)


@account.delete("", status_code=204)
def delete_account(body: DeleteAccountIn, request: Request, response: Response,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.delete_account(db, user, body.password, request, response)
    db.commit()
    response.status_code = 204
    return response
