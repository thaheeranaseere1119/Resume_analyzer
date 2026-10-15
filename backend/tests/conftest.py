"""Test configuration: real PostgreSQL test database (migrated with Alembic), Redis DB 15, eager Celery."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.mkdtemp(prefix="ats-test-")
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://ats:ats@localhost:5432/ats_test"),
    "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
    "CELERY_EAGER": "true",
    "STORAGE_DIR": os.path.join(_tmp, "storage"),
    "JWT_SECRET": "test-secret-test-secret-test-secret-123456",
    "RATE_LIMIT_AUTH_PER_MINUTE": "1000",
    "RATE_LIMIT_UPLOAD_PER_HOUR": "1000",
    "RATE_LIMIT_AI_PER_HOUR": "1000",
    "ANTHROPIC_API_KEY": "",
    "SMTP_URL": "",
})

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import Base, get_engine, reset_engine  # noqa: E402

FIXTURES = BACKEND.parent / "tests" / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def database():
    get_settings.cache_clear()
    reset_engine()
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    reset_engine()


@pytest.fixture(autouse=True)
def clean_db():
    yield
    tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    try:
        import redis
        redis.Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    except Exception:
        pass


@pytest.fixture
def client():
    from app.main import create_app
    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c


class Api:
    """Tiny helper that keeps cookies and sends the CSRF header like the frontend does."""

    def __init__(self, client: TestClient):
        self.c = client
        self.csrf = None

    def _h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    def register(self, email="ada@example.com", password="correct-horse-42", name="Ada Lovelace"):
        r = self.c.post("/api/v1/auth/register", json={"email": email, "password": password, "full_name": name})
        assert r.status_code == 201, r.text
        self.csrf = r.json()["csrf_token"]
        return r.json()

    def get(self, path, **kw):
        return self.c.get(f"/api/v1{path}", **kw)

    def post(self, path, **kw):
        return self.c.post(f"/api/v1{path}", headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, path, **kw):
        return self.c.patch(f"/api/v1{path}", headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, path, **kw):
        return self.c.put(f"/api/v1{path}", headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, path, **kw):
        return self.c.request("DELETE", f"/api/v1{path}", headers=self._h(kw.pop("headers", None)), **kw)


@pytest.fixture
def api(client):
    return Api(client)


@pytest.fixture
def user(api):
    api.register()
    return api


@pytest.fixture
def other_user():
    """A second, independent browser session for authorization tests."""
    from app.main import create_app
    with TestClient(create_app(), base_url="http://testserver") as c:
        a = Api(c)
        a.register(email="mallory@example.com", password="mallory-pass-42", name="Mallory")
        yield a
