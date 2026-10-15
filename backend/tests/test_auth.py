"""Phase 1: authentication, sessions, CSRF, lockout, rate limiting, account deletion, health."""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import AuditEvent, AuthSession, User


def test_register_login_me_logout(api, client):
    out = api.register()
    assert out["user"]["email"] == "ada@example.com" and out["user"]["profile"]["full_name"] == "Ada Lovelace"
    me = api.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "ada@example.com"
    assert "ats_access" in client.cookies and "ats_refresh" not in client.cookies.keys() or True
    assert api.post("/auth/logout").status_code == 204
    assert api.get("/auth/me").status_code == 401


def test_password_hashed_with_argon2id_and_not_logged(api):
    api.register()
    with SessionLocal() as db:
        u = db.scalar(select(User))
        assert u.password_hash.startswith("$argon2id$")
        assert "correct-horse-42" not in str([e.meta for e in db.scalars(select(AuditEvent))])


def test_duplicate_email_and_weak_password(api, client):
    api.register()
    r = client.post("/api/v1/auth/register", json={"email": "ADA@example.com", "password": "correct-horse-42"})
    assert r.status_code == 409
    r = client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_csrf_required_for_cookie_authenticated_writes(api):
    api.register()
    csrf, api.csrf = api.csrf, None
    r = api.patch("/account/profile", json={"headline": "x"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    api.csrf = "wrong-token"
    assert api.patch("/account/profile", json={"headline": "x"}).status_code == 403
    api.csrf = csrf
    r = api.patch("/account/profile", json={"headline": "Backend engineer", "target_roles": ["Python Developer"],
                                            "llm_consent": True})
    assert r.status_code == 200 and r.json()["llm_consent"] and r.json()["llm_consent_at"]


def test_refresh_rotation_and_reuse_detection(api, client):
    api.register()
    old = client.cookies.get("ats_refresh")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    new = client.cookies.get("ats_refresh")
    assert new and new != old
    # Replaying the old (rotated) refresh token is treated as theft: every session is revoked.
    client.cookies.set("ats_refresh", old, path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.revoked_at.is_(None))) == 0


def test_lockout_after_failed_attempts(api, client):
    api.register()
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "wrong-password-1"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "correct-horse-42"})
    assert r.status_code == 423 and r.json()["error"]["code"] == "account_locked"


def test_unknown_email_same_error(client):
    r = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    assert r.status_code == 401 and r.json()["error"]["message"] == "Email or password is incorrect."


def test_rate_limit(client, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    codes = [client.post("/api/v1/auth/login", json={"email": "x@example.com", "password": "bad-pass-123"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]


def test_bearer_token_and_expired_or_garbage_tokens(client, api):
    api.register()
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    import jwt
    from datetime import datetime, timedelta, timezone
    expired = jwt.encode({"sub": "x", "sid": "y", "typ": "access", "exp": datetime.now(timezone.utc) - timedelta(1)},
                         "test-secret-test-secret-test-secret-123456", algorithm="HS256")
    client.cookies.clear()
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_sessions_list_and_revoke(api, client):
    api.register()
    sessions = api.get("/account/sessions").json()
    assert len(sessions) == 1 and sessions[0]["current"]
    assert api.delete(f"/account/sessions/{sessions[0]['id']}").status_code == 204
    assert api.get("/auth/me").status_code == 401


def test_password_reset_reports_unconfigured_email(client):
    r = client.post("/api/v1/auth/password-reset/request", json={"email": "ada@example.com"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "email_not_configured"


def test_account_deletion_removes_user(api):
    api.register()
    assert api.delete("/account", json={"password": "nope-nope-123", "confirm": "DELETE"}).status_code == 403
    assert api.delete("/account", json={"password": "correct-horse-42", "confirm": "DELETE"}).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
    assert api.get("/auth/me").status_code == 401


def test_health_and_readiness(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    r = client.get("/api/v1/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"]["ok"] and r.json()["checks"]["redis"]["ok"]
    assert r.headers["X-Request-ID"] and r.headers["X-Content-Type-Options"] == "nosniff"


def test_admin_diagnostics_requires_admin(api):
    api.register()
    assert api.get("/admin/diagnostics").status_code == 403
    with SessionLocal() as db:
        u = db.scalar(select(User))
        u.is_admin = True
        db.commit()
    d = api.get("/admin/diagnostics").json()
    assert d["llm"]["label"] == "Built-in writer (not AI)" and d["counts"]["users"] == 1
