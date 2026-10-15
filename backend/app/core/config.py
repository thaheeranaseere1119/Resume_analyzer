"""Environment-based configuration (12-factor). Nothing secret is hardcoded."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", description="development | test | production")
    app_name: str = "ATS Resume Analyzer"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://ats:ats@localhost:5432/ats"
    redis_url: str = "redis://localhost:6379/0"
    celery_eager: bool = False  # run tasks in-process (tests / no worker)

    # auth
    jwt_secret: str = Field("dev-only-change-me-please-32-bytes-min", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    cookie_secure: bool = False
    cookie_domain: str | None = None
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    # uploads & storage
    storage_dir: Path = ROOT / "data" / "storage"
    max_upload_mb: int = 10
    retention_days: int = 365

    # cors & rate limits
    cors_origins: str = "http://localhost:3000"
    rate_limit_auth_per_minute: int = 10
    rate_limit_upload_per_hour: int = 30
    rate_limit_ai_per_hour: int = 60

    # AI
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 2
    semantic_backend: str = "auto"  # auto | sentence-transformers | tfidf
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # OCR / conversion
    ocr_enabled: bool = True
    tessdata_dir: str | None = None
    soffice_path: str | None = None  # LibreOffice binary for DOCX->PDF
    conversion_timeout_seconds: int = 60

    # optional integrations
    smtp_url: str | None = None
    email_from: str | None = None
    job_provider: str = "manual"  # manual | (future providers)

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in ("development", "test", "production"):
            raise ValueError("APP_ENV must be development, test or production")
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate_for_production(self) -> None:
        if self.is_production:
            if self.jwt_secret.startswith("dev-only"):
                raise RuntimeError("JWT_SECRET must be set in production")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production (HTTPS)")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_production()
    return s
