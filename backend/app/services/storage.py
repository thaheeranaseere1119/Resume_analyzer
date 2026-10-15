"""Private file storage with generated keys. Paths are never exposed to clients.

Keys look like `u/<user-uuid>/<random>.<ext>` so a user's files can be removed
in one operation on account deletion.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..core.config import get_settings


def _root() -> Path:
    root = Path(get_settings().storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(key: str) -> Path:
    p = (_root() / key).resolve()
    if _root().resolve() not in p.parents:
        raise ValueError("invalid storage key")
    return p


def put(user_id: uuid.UUID, data: bytes, ext: str) -> tuple[str, str]:
    """Store bytes, return (key, sha256). Files are written read-only (immutable)."""
    ext = ext.lower().lstrip(".")
    if ext not in ("pdf", "docx", "txt"):
        raise ValueError("unsupported extension")
    key = f"u/{user_id}/{secrets.token_hex(16)}.{ext}"
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o440)
    tmp.replace(p)
    return key, hashlib.sha256(data).hexdigest()


def get(key: str) -> bytes:
    return _path(key).read_bytes()


def delete(key: str) -> None:
    p = _path(key)
    if p.exists():
        os.chmod(p, 0o640)
        p.unlink()


def delete_user_files(user_id: uuid.UUID) -> None:
    d = _path(f"u/{user_id}")
    if d.exists():
        for f in d.rglob("*"):
            if f.is_file():
                os.chmod(f, 0o640)
        shutil.rmtree(d)


@contextmanager
def temp_dir():
    """Secure temporary directory, always removed afterwards."""
    d = tempfile.mkdtemp(prefix="ats-")
    try:
        os.chmod(d, 0o700)
        yield Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
