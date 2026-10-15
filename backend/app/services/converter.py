"""DOCX -> PDF conversion with LibreOffice headless (sandboxed temp dir, timeout, cleanup)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..core.config import get_settings
from . import storage


class ConversionError(Exception):
    pass


def soffice_path() -> str | None:
    s = get_settings()
    for cand in (s.soffice_path, shutil.which("soffice"), shutil.which("libreoffice"),
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if cand and Path(cand).exists():
            return cand
    return None


def status() -> dict:
    p = soffice_path()
    return {"available": bool(p), "engine": "libreoffice-headless" if p else "reportlab-fallback"}


def docx_to_pdf(docx: bytes) -> bytes:
    exe = soffice_path()
    if not exe:
        raise ConversionError("LibreOffice is not installed")
    with storage.temp_dir() as d:
        src = d / "document.docx"
        src.write_bytes(docx)
        profile = d / "profile"
        cmd = [exe, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore", "--nolockcheck",
               "--convert-to", "pdf", "--outdir", str(d), str(src)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=get_settings().conversion_timeout_seconds,
                           env={**os.environ, "HOME": str(d)})
        except subprocess.TimeoutExpired:
            raise ConversionError("PDF conversion timed out")
        except subprocess.CalledProcessError as exc:
            raise ConversionError(f"PDF conversion failed ({exc.returncode})")
        out = d / "document.pdf"
        if not out.exists() or out.stat().st_size == 0:
            raise ConversionError("PDF conversion produced no output")
        return out.read_bytes()
