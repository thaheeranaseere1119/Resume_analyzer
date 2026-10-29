import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
FIX = ROOT / "tests" / "fixtures"
AS_OF = date(2026, 10, 15)


def pytest_configure(config):
    needed = ["classic.docx", "two_column_table.docx", "section_columns.docx", "single_column.pdf",
              "two_column.pdf", "tight_page.pdf", "scanned.pdf", "job_description.txt"]
    if not all((FIX / n).exists() for n in needed):
        sys.path.insert(0, str(FIX))
        import build_fixtures
        build_fixtures.main()


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    """Tests never call a real AI API, even when keys are present in .env."""
    for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    import careercopilot.llm as llm
    monkeypatch.setattr(llm.os.path, "exists", lambda p, _real=llm.os.path.exists: False if p.endswith(".config/anthropic") else _real(p))
    llm._cache.clear()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Isolated session storage for every test."""
    import careercopilot.config as cfg
    import careercopilot.service as svc
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(svc, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture(scope="session")
def jd_text():
    return (FIX / "job_description.txt").read_text()


@pytest.fixture(scope="session")
def jd(jd_text):
    from careercopilot.jd import parse_jd
    return parse_jd(jd_text)


@pytest.fixture(scope="session")
def weights():
    from careercopilot.config import load_weights
    return load_weights()


def fixture_bytes(name: str) -> bytes:
    return (FIX / name).read_bytes()
