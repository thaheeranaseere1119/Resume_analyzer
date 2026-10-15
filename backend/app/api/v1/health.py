from __future__ import annotations

import shutil
import time

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...core.config import get_settings
from ...core.db import get_db
from ...models import AnalysisRun, Recommendation, Resume, User
from ..deps import require_admin

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Liveness: the process is up."""
    return {"status": "ok"}


def _checks(db: Session) -> dict:
    s = get_settings()
    out: dict = {}
    t = time.perf_counter()
    try:
        db.execute(text("select 1"))
        out["database"] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000, 1)}
    except Exception:
        out["database"] = {"ok": False}
    try:
        import redis
        r = redis.Redis.from_url(s.redis_url, socket_timeout=1, socket_connect_timeout=1)
        out["redis"] = {"ok": bool(r.ping())}
    except Exception:
        out["redis"] = {"ok": False}
    try:
        s.storage_dir.mkdir(parents=True, exist_ok=True)
        out["storage"] = {"ok": True, "free_gb": round(shutil.disk_usage(s.storage_dir).free / 1e9, 1)}
    except OSError:
        out["storage"] = {"ok": False}
    return out


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    """Readiness: database, Redis and storage reachable."""
    checks = _checks(db)
    ok = all(c["ok"] for c in checks.values())
    return JSONResponse({"status": "ready" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)


@router.get("/admin/diagnostics")
def diagnostics(db: Session = Depends(get_db), _=Depends(require_admin)):
    from ...ai import provider
    from ...services import converter
    checks = _checks(db)
    counts = {name: db.scalar(select(func.count()).select_from(model))
              for name, model in (("users", User), ("resumes", Resume), ("analyses", AnalysisRun),
                                  ("recommendations", Recommendation))}
    return {"checks": checks, "counts": counts, "llm": provider.status(), "pdf_conversion": converter.status(),
            "environment": get_settings().app_env}
