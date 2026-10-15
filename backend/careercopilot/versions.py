"""Immutable original + append-only version history.

Layout:  <data>/sessions/<sid>/original.<ext>   (read-only, never rewritten)
                              versions/v0001.<ext>
                              manifest.json
Undo/restore only move the HEAD pointer; no version file is ever deleted,
so every state stays recoverable.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from pathlib import Path


class VersionStore:
    def __init__(self, root: Path):
        self.root = root
        self.manifest_path = root / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else None

    @classmethod
    def create(cls, root: Path, data: bytes, filename: str) -> "VersionStore":
        root.mkdir(parents=True, exist_ok=True)
        (root / "versions").mkdir(exist_ok=True)
        ext = Path(filename).suffix.lower()
        orig = root / f"original{ext}"
        orig.write_bytes(data)
        os.chmod(orig, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # immutable original
        store = cls(root)
        store.manifest = {
            "filename": filename, "ext": ext, "head": 0,
            "versions": [{"id": 0, "file": orig.name, "parent": None, "created": time.time(),
                          "label": "Original upload", "sha256": hashlib.sha256(data).hexdigest(), "change": None}],
            "events": [{"type": "upload", "at": time.time(), "version": 0}],
        }
        store._save()
        return store

    def _save(self):
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.manifest, indent=2, default=str))
        tmp.replace(self.manifest_path)

    @property
    def head(self) -> int:
        return self.manifest["head"]

    def version(self, vid: int) -> dict:
        return next(v for v in self.manifest["versions"] if v["id"] == vid)

    def read(self, vid: int | None = None) -> bytes:
        v = self.version(self.head if vid is None else vid)
        data = (self.root / ("" if v["id"] == 0 else "versions") / v["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != v["sha256"]:
            raise IOError(f"Version {v['id']} failed its integrity check")
        return data

    def add(self, data: bytes, label: str, change: dict) -> dict:
        vid = max(v["id"] for v in self.manifest["versions"]) + 1
        name = f"v{vid:04d}{self.manifest['ext']}"
        path = self.root / "versions" / name
        path.write_bytes(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        v = {"id": vid, "file": name, "parent": self.head, "created": time.time(), "label": label,
             "sha256": hashlib.sha256(data).hexdigest(), "change": change}
        self.manifest["versions"].append(v)
        self.manifest["head"] = vid
        self.manifest["events"].append({"type": "apply", "at": time.time(), "version": vid})
        self._save()
        return v

    def update_change(self, vid: int, **fields):
        self.version(vid)["change"].update(fields)
        self._save()

    def undo(self) -> int:
        cur = self.version(self.head)
        if cur["parent"] is None:
            raise ValueError("Nothing to undo: the original is already active")
        self.manifest["head"] = cur["parent"]
        self.manifest["events"].append({"type": "undo", "at": time.time(), "from": cur["id"], "to": cur["parent"]})
        self._save()
        return self.head

    def restore(self, vid: int) -> int:
        self.version(vid)
        prev = self.head
        self.manifest["head"] = vid
        self.manifest["events"].append({"type": "restore", "at": time.time(), "from": prev, "to": vid})
        self._save()
        return vid

    def lineage(self) -> list[int]:
        out, cur = [], self.head
        while cur is not None:
            out.append(cur)
            cur = self.version(cur)["parent"]
        return out[::-1]
