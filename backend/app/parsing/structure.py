"""Bridge between the document engine (`careercopilot`) and the structured, versioned representation.

* `parse_document` -> engine model + layout (PDF) for a stored file.
* `build_structure` -> sections, blocks (with confidence, page, char offsets), contact profile, warnings.
* `align_stable_ids` -> carries stable block identifiers from the previous version to the new one.
"""
from __future__ import annotations

import difflib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

from careercopilot import textutil as tu
from careercopilot.docx_io import read_docx
from careercopilot.model import ResumeModel
from careercopilot.pdf_io import read_pdf

DISPLAY_TYPES = [
    (r"intern", "internships"), (r"award|achievement|honou?r", "achievements"), (r"publication|paper", "publications"),
    (r"language", "languages"), (r"volunteer", "volunteering"), (r"interest|hobb", "interests"),
    (r"soft skill", "soft_skills"), (r"technical skill|tech stack|technolog", "technical_skills"),
]
LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?((?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|"
                     r"figma\.com|medium\.com|kaggle\.com|leetcode\.com)/[^\s•|,;)]+|[a-z0-9-]+\.(?:dev|io|me|app)\b[^\s•|,;)]*)",
                     re.I)
LOCATION_RE = re.compile(r"\b([A-Z][a-zA-Z .'-]{2,40}),\s*([A-Z][a-zA-Z .'-]{1,40})\b")


@dataclass
class ParsedBlock:
    doc_ref: str
    kind: str
    text: str
    section_index: int
    entry_key: str | None
    page: int | None
    char_start: int
    char_end: int
    confidence: float


@dataclass
class ParsedSection:
    section_type: str
    engine_section: str
    heading: str
    page: int | None
    confidence: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedResume:
    sections: list[ParsedSection]
    blocks: list[ParsedBlock]
    profile: dict
    warnings: list[dict]
    page_count: int | None
    scanned: bool
    ocr_confidence: float | None


def parse_document(data: bytes, fmt: str, as_of: date):
    if fmt == "docx":
        return read_docx(data, as_of), None
    return read_pdf(data, as_of)


def _display_type(engine_section: str, heading: str) -> str:
    h = heading.lower()
    for pat, t in DISPLAY_TYPES:
        if re.search(pat, h):
            return t
    return engine_section


def extract_profile(model: ResumeModel) -> dict:
    contact = [b.text for b in model.blocks if b.section == "contact"]
    text = "\n".join(contact) or "\n".join(b.text for b in model.blocks[:6])
    name = next((t for t in contact if 1 <= len(t.split()) <= 4 and not re.search(r"[@\d|•:/]", t)), None)
    if name and name.isupper():
        name = name.title()
    email = tu.EMAIL_RE.search(text)
    phone = tu.PHONE_RE.search(text)
    links = sorted({m.group(0).rstrip(".") for m in LINK_RE.finditer(text) if "@" not in m.group(0)})
    loc = None
    for part in re.split(r"[•|\n]", text):
        m = LOCATION_RE.search(part.strip())
        if m and not tu.EMAIL_RE.search(part) and not re.search(r"\d{3}", part):
            loc = m.group(0)
            break
    return {"name": name, "email": email.group(0) if email else None, "phone": phone.group(0).strip() if phone else None,
            "location": loc, "links": links}


def build_structure(model: ResumeModel) -> ParsedResume:
    sections: list[ParsedSection] = []
    blocks: list[ParsedBlock] = []
    warnings: list[dict] = []
    current = None
    offset = 0
    base_conf = (model.ocr_confidence or 0) / 100 if model.scanned else 1.0

    for b in model.blocks:
        if b.kind == "heading" or current is None or b.section != sections[current].engine_section:
            if b.kind == "heading":
                heading, conf = b.text, 1.0
            else:
                heading = ""
                # Content without a heading: the summary before the first heading, or header/footer contact text.
                conf = 0.9 if b.section == "contact" else 0.7
            sections.append(ParsedSection(_display_type(b.section, heading), b.section, heading, b.page,
                                          round(conf * (base_conf if model.scanned else 1.0), 2)))
            current = len(sections) - 1
            if b.kind == "heading":
                offset += len(b.text) + 1
                continue
        text = b.text
        conf = base_conf
        if b.kind in ("entry_header", "paragraph") and b.section in ("experience", "projects") and len(text.split()) > 60:
            conf = min(conf, 0.8)
        blocks.append(ParsedBlock(b.id, b.kind, text, current, b.entry_id, b.page, offset, offset + len(text),
                                  round(conf, 2)))
        offset += len(text) + 1

    seen = {s.engine_section for s in sections}
    for s in sections:
        if not any(bl.section_index == sections.index(s) for bl in blocks) and s.heading:
            s.warnings.append("This section appears to be empty.")
            warnings.append({"severity": "warning", "code": "empty_section", "section": s.section_type,
                             "message": f"The '{s.heading}' section appears to be empty."})
    for need, label in (("experience", "Experience"), ("education", "Education"), ("skills", "Skills")):
        if need not in seen:
            warnings.append({"severity": "warning", "code": "missing_section", "section": need,
                             "message": f"No '{label}' heading was detected. ATS software may not find this information."})
    profile = extract_profile(model)
    for f, label in (("email", "email address"), ("phone", "phone number")):
        if not profile[f]:
            warnings.append({"severity": "warning", "code": f"missing_{f}", "section": "contact",
                             "message": f"No {label} could be extracted."})
    if any(b.id.startswith("header") for b in model.blocks if b.section == "contact"):
        warnings.append({"severity": "info", "code": "contact_in_header", "section": "contact",
                         "message": "Contact details are in the page header. Some ATS ignore headers; consider moving them into the body."})
    if model.scanned:
        warnings.append({"severity": "warning" if (model.ocr_confidence or 0) >= 75 else "error", "code": "ocr_used",
                         "section": None, "message": f"This PDF is a scan; text was read with OCR "
                                                     f"(confidence {model.ocr_confidence:.0f}%). Review the extracted text."})
    if (model.meta or {}).get("columns", 1) > 1:
        warnings.append({"severity": "info", "code": "multi_column", "section": None,
                         "message": "Multi-column layout detected. Some ATS read columns in a different order."})
    if (model.meta or {}).get("tables"):
        warnings.append({"severity": "info", "code": "tables", "section": None,
                         "message": "Content is laid out in tables, which some ATS parse poorly."})
    if any(re.search(r"[�□]", bl.text) for bl in blocks):
        warnings.append({"severity": "warning", "code": "encoding", "section": None,
                         "message": "Some characters could not be decoded (shown as �). Check fonts and symbols."})
    return ParsedResume(sections, blocks, profile, warnings, model.page_count, model.scanned, model.ocr_confidence)


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", t.lower()).strip()


def align_stable_ids(prev: list[tuple[uuid.UUID, str]], new_texts: list[str],
                     hints: dict[int, uuid.UUID] | None = None) -> list[uuid.UUID]:
    """Map each new block to the stable id of the matching previous block (or a fresh id).

    `hints` pins specific new positions to known ids (e.g. the block an edit replaced)."""
    hints = hints or {}
    out: list[uuid.UUID | None] = [hints.get(i) for i in range(len(new_texts))]
    used = {v for v in out if v}
    a = [_norm(t) for _, t in prev]
    b = [_norm(t) for t in new_texts]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                if out[j1 + k] is None and prev[i1 + k][0] not in used:
                    out[j1 + k] = prev[i1 + k][0]
                    used.add(prev[i1 + k][0])
        elif op == "replace":
            for k in range(min(i2 - i1, j2 - j1)):
                ratio = difflib.SequenceMatcher(None, a[i1 + k], b[j1 + k]).ratio()
                if out[j1 + k] is None and ratio >= 0.6 and prev[i1 + k][0] not in used:
                    out[j1 + k] = prev[i1 + k][0]
                    used.add(prev[i1 + k][0])
    return [x or uuid.uuid4() for x in out]
