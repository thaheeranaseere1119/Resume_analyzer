"""FastAPI application factory."""
from __future__ import annotations

import logging
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api.v1 import router as v1_router
from .core.config import get_settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, request_id_var

log = logging.getLogger("app.http")


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    app = FastAPI(title="AI-Powered ATS Resume Analyzer API", version="1.0.0",
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "Authorization", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or secrets.token_hex(8)
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        log.info("request", extra={"extra_fields": {"request_id": rid, "method": request.method,
                                                    "path": request.url.path, "status": response.status_code,
                                                    "ms": ms}})
        return response

    app.include_router(v1_router, prefix="/api/v1")
    return app


app = create_app()
