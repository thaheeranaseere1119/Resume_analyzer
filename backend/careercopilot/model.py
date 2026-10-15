"""Format-independent resume model.

Both the DOCX and PDF readers produce a list of RawItems (one per paragraph
or visual text block). `build_model` assigns sections, block kinds and
experience/project entries. The scoring engine only ever sees a ResumeModel,
which is what lets the simulator score a proposed edit in memory with the
exact same algorithm used for the real document.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from . import textutil as tu


@dataclass
class RawItem:
    id: str
    text: str
    is_bullet: bool = False
    bullet_glyph: str = ""
    is_bold: bool = False
    is_heading_style: bool = False
    font_size: float | None = None
    in_table: bool = False
    page: int | None = None
    style: dict = field(default_factory=dict)


@dataclass
class Block:
    id: str
    text: str
    section: str
    kind: str              # heading | contact | paragraph | bullet | entry_header | skills_line
    section_title: str = ""
    entry_id: str | None = None
    in_table: bool = False
    page: int | None = None
    bullet_glyph: str = ""
    style: dict = field(default_factory=dict)
    synthetic: bool = False  # created by an in-memory simulation, not yet in a document

    @property
    def body(self) -> str:
        return tu.strip_bullet(self.text)[1] if self.bullet_glyph else self.text


@dataclass
class Entry:
    id: str
    section: str
    title: str
    ranges: list = field(default_factory=list)  # [((y,m),(y,m),current)]

    @property
    def current(self) -> bool:
        return any(r[2] for r in self.ranges)


@dataclass
class ResumeModel:
    format: str
    blocks: list[Block]
    entries: dict[str, Entry]
    as_of: date
    page_count: int | None = None
    scanned: bool = False
    ocr_confidence: float | None = None
    meta: dict = field(default_factory=dict)

    # ---- lookup -------------------------------------------------------
    def block(self, block_id: str) -> Block:
        for b in self.blocks:
            if b.id == block_id:
                return b
        raise KeyError(block_id)

    def section_blocks(self, section: str) -> list[Block]:
        return [b for b in self.blocks if b.section == section and b.kind != "heading"]

    def entry_blocks(self, entry_id: str) -> list[Block]:
        return [b for b in self.blocks if b.entry_id == entry_id]

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    # ---- in-memory edits (used by the simulator) ----------------------
    def clone(self) -> "ResumeModel":
        return copy.deepcopy(self)

    def with_replaced(self, block_id: str, new_text: str) -> "ResumeModel":
        m = self.clone()
        b = m.block(block_id)
        b.text = (b.bullet_glyph + " " + new_text) if b.bullet_glyph else new_text
        return m

    def with_inserted_after(self, anchor_id: str, new_text: str, kind: str | None = None) -> "ResumeModel":
        m = self.clone()
        idx = next(i for i, b in enumerate(m.blocks) if b.id == anchor_id)
        anchor = m.blocks[idx]
        new = copy.deepcopy(anchor)
        new.id = f"sim-{anchor_id}"
        new.kind = kind or ("bullet" if anchor.kind in ("bullet", "entry_header") else anchor.kind)
        new.bullet_glyph = anchor.bullet_glyph if new.kind == "bullet" else ""
        new.text = (new.bullet_glyph + " " + new_text) if new.bullet_glyph else new_text
        new.synthetic = True
        m.blocks.insert(idx + 1, new)
        return m

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "page_count": self.page_count,
            "scanned": self.scanned,
            "ocr_confidence": self.ocr_confidence,
            "blocks": [asdict(b) for b in self.blocks],
            "entries": {k: {"id": e.id, "section": e.section, "title": e.title, "current": e.current}
                        for k, e in self.entries.items()},
            "meta": self.meta,
        }


def build_model(items: list[RawItem], fmt: str, as_of: date, **kw) -> ResumeModel:
    blocks: list[Block] = []
    entries: dict[str, Entry] = {}
    section, section_title = "contact", ""
    current_entry: Entry | None = None
    prev_kind = None
    sizes = sorted(i.font_size for i in items if i.font_size)
    body_size = sizes[len(sizes) // 2] if sizes else None

    doc_has_bold_headers = any(i.is_bold and tu.DATE_RANGE_RE.search(i.text or "") for i in items)
    for it in items:
        text = tu.normalize_ws(it.text)
        if not text:
            continue
        heading = tu.classify_heading(text)
        looks_heading = heading and (it.is_heading_style or it.is_bold or text.isupper()
                                     or (body_size and it.font_size and it.font_size > body_size + 0.4)
                                     or len(text.split()) <= 3)
        strong = it.is_heading_style or text.isupper() or bool(
            body_size and it.font_size and it.font_size > body_size + 0.4)
        # Labels inside a skills block ("Languages", "Tools") or a table are not new sections
        # unless they are styled like the document's real headings.
        if looks_heading and not strong and (it.in_table or section == "skills") and heading in ("skills", "other"):
            looks_heading = False
        if looks_heading and not it.is_bullet:
            section, section_title = heading, text
            current_entry, prev_kind = None, "heading"
            blocks.append(Block(it.id, text, section, "heading", section_title, None, it.in_table, it.page,
                                style=it.style))
            continue

        glyph = it.bullet_glyph
        if not glyph and not it.is_bullet:
            g, _ = tu.strip_bullet(text)
            if g and g not in "-*" or (g and len(text) > 20):
                glyph = g
        is_bullet = it.is_bullet or bool(glyph)

        n_words = len(text.split())
        if section == "contact" and n_words >= 12 and not (tu.EMAIL_RE.search(text) or tu.PHONE_RE.search(text)
                                                          or re.search(r"https?://|www\.|\.com/", text)):
            # A sentence-length paragraph before the first heading is an unlabelled summary.
            section, section_title = "summary", ""
        if section == "contact":
            kind = "contact"
        elif section == "skills":
            kind = "skills_line"
        elif is_bullet:
            kind = "bullet"
        elif section in ("experience", "projects", "education", "certifications", "other"):
            has_date = bool(tu.DATE_RANGE_RE.search(text)) or bool(tu.re.search(r"\b(19|20)\d{2}\b", text))
            is_tech_line = bool(tu.re.match(r"^(tech(nologies|nology| stack)?|tools|stack|environment)\s*:", text, tu.re.I))
            is_detail = bool(re.match(r"^(outcome|result|impact|achievements?|highlights?|responsibilities)\s*:", text, re.I))
            if is_tech_line or is_detail:
                kind = "paragraph"
            elif has_date or it.is_bold:
                kind = "entry_header"
            elif prev_kind in ("paragraph", "bullet") and section in ("experience", "projects") and doc_has_bold_headers:
                kind = "paragraph"  # unbolded follow-on text belongs to the current entry
            elif n_words <= 14:
                kind = "entry_header"
            else:
                kind = "paragraph"
        else:
            kind = "paragraph"

        if kind == "entry_header" and section in ("experience", "projects", "education"):
            if current_entry is None or current_entry.section != section or prev_kind not in ("entry_header",):
                eid = f"e{len(entries) + 1}"
                current_entry = Entry(eid, section, text)
                entries[eid] = current_entry
            else:
                current_entry.title = f"{current_entry.title} | {text}"
            current_entry.ranges.extend(tu.date_ranges(text, as_of))

        entry_id = current_entry.id if current_entry and section in ("experience", "projects", "education") else None
        blocks.append(Block(it.id, text, section, kind, section_title, entry_id, it.in_table, it.page,
                            bullet_glyph=glyph if not it.is_bullet else "", style=it.style))
        prev_kind = kind

    return ResumeModel(format=fmt, blocks=blocks, entries=entries, as_of=as_of, **kw)
