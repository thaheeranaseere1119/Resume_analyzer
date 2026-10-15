"""Structured resume editor: an editable, re-orderable draft built from the current version.

The draft is the source for the ATS-friendly export. Text edits to lines that exist in the
original document can also be written back into the original file (format-preserving).
"""
from __future__ import annotations

import re
import secrets

from . import textutil as tu
from .grammar import validate_text
from .model import RawItem, ResumeModel, build_model
from .polish import improve_line

KINDS = ("paragraph", "bullet", "entry_header", "skills_line", "contact")


def _lid() -> str:
    return secrets.token_hex(4)


def draft_from_model(model: ResumeModel, version: int) -> dict:
    sections, cur = [], None
    for b in model.blocks:
        if b.kind == "heading" or cur is None or b.section != cur["type"]:
            cur = {"id": _lid(), "type": b.section, "title": b.text if b.kind == "heading" else "", "lines": []}
            sections.append(cur)
            if b.kind == "heading":
                continue
        cur["lines"].append({"id": _lid(), "text": b.body, "kind": b.kind if b.kind in KINDS else "paragraph",
                             "block_id": b.id, "original": b.body})
    return {"base_version": version, "sections": sections}


def model_from_draft(draft: dict, as_of, fmt: str = "draft") -> ResumeModel:
    items = []
    for s in draft["sections"]:
        if s["type"] != "contact" and (s.get("title") or s["type"]):
            items.append(RawItem(id=f"h-{s['id']}", text=s.get("title") or s["type"].title(), is_bold=True,
                                 is_heading_style=True))
        for l in s["lines"]:
            if l["text"].strip():
                items.append(RawItem(id=l["id"], text=l["text"], is_bullet=l["kind"] == "bullet",
                                     is_bold=l["kind"] == "entry_header"))
    return build_model(items, fmt, as_of, page_count=None)


def sanitize(draft: dict) -> dict:
    """Validate shape and size of a client-supplied draft."""
    out = {"base_version": int(draft.get("base_version", 0)), "sections": []}
    for s in draft.get("sections", [])[:40]:
        sec = {"id": str(s.get("id") or _lid())[:16], "type": str(s.get("type") or "other")[:30],
               "title": str(s.get("title") or "")[:120], "lines": []}
        for l in s.get("lines", [])[:120]:
            kind = l.get("kind") if l.get("kind") in KINDS else "paragraph"
            text = tu.normalize_ws(str(l.get("text") or ""))[:1500]
            sec["lines"].append({"id": str(l.get("id") or _lid())[:16], "text": text, "kind": kind,
                                 "block_id": (str(l["block_id"])[:40] if l.get("block_id") else None),
                                 "original": str(l.get("original") or "")[:1500]})
        out["sections"].append(sec)
    return out


def line_checks(draft: dict, known_words: set[str]) -> dict:
    """Writing issues per line id (errors only, plus a suggested fix when one is safe)."""
    out = {}
    for s in draft["sections"]:
        if s["type"] == "contact":
            continue
        for l in s["lines"]:
            if not l["text"].strip():
                continue
            vk = {"bullet": "bullet", "paragraph": "paragraph", "skills_line": "skills_line"}.get(l["kind"], "line")
            r = validate_text(l["text"], kind=vk, known_words=known_words)
            issues = [c["message"] for c in r["checks"] if c["status"] == "fail"]
            better = improve_line(l["text"], kind=l["kind"], tense="past")
            if issues or better != l["text"]:
                out[l["id"]] = {"issues": issues or ["Wording can be made more professional"],
                                "suggestion": better if better != l["text"] else r.get("fixed_text")}
    return out


def changed_original_lines(draft: dict) -> list[dict]:
    """Lines that came from the original document and whose text was edited."""
    return [l for s in draft["sections"] for l in s["lines"]
            if l.get("block_id") and l["text"].strip() and tu.normalize_ws(l["text"]) != tu.normalize_ws(l.get("original", ""))]
