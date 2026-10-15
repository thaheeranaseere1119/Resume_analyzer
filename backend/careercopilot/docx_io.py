"""DOCX reading helpers shared by the parser, editor and previewer."""
from __future__ import annotations

import io
from datetime import date

from docx import Document
from docx.oxml.ns import qn

from .model import RawItem, ResumeModel, build_model

W_P, W_R, W_T, W_TAB, W_BR = qn("w:p"), qn("w:r"), qn("w:t"), qn("w:tab"), qn("w:br")
W_DEL, W_TC, W_HYPERLINK = qn("w:del"), qn("w:tc"), qn("w:hyperlink")


def body_paragraphs(doc) -> list:
    """All w:p elements in body order (including tables and text boxes)."""
    return list(doc.element.body.iter(W_P))


def para_text(p) -> str:
    out = []
    for el in p.iter():
        if el.tag == W_T:
            if _inside(el, W_DEL, p):
                continue
            out.append(el.text or "")
        elif el.tag == W_TAB and el.getparent() is not None and el.getparent().tag == W_R:
            out.append("\t")
        elif el.tag == W_BR and el.get(qn("w:type")) in (None, "textWrapping"):
            out.append("\n")
    return "".join(out)


def _inside(el, tag, stop) -> bool:
    cur = el.getparent()
    while cur is not None and cur is not stop:
        if cur.tag == tag:
            return True
        cur = cur.getparent()
    return False


def _style_chain_value(style, getter):
    while style is not None:
        v = getter(style)
        if v is not None:
            return v
        style = style.base_style
    return None


def paragraph_info(doc, p) -> dict:
    """Effective formatting summary of a paragraph (for heading/bullet detection and previews)."""
    from docx.text.paragraph import Paragraph
    para = Paragraph(p, doc.part)
    style = para.style
    style_name = style.name if style is not None else ""
    ppr = p.pPr
    has_num = ppr is not None and ppr.numPr is not None
    if not has_num and style is not None:
        s = style
        while s is not None and not has_num:
            spp = s.element.pPr
            has_num = spp is not None and spp.numPr is not None
            s = s.base_style
    runs = [r for r in para.runs if r.text.strip()]
    sizes, bolds = [], []
    for r in runs:
        size = r.font.size or _style_chain_value(style, lambda s: s.font.size if s.type == 1 else None)
        sizes.append(size.pt if size else None)
        b = r.bold
        if b is None:
            b = _style_chain_value(style, lambda s: s.font.bold)
        bolds.append(bool(b))
    size = next((s for s in sizes if s), None)
    if size is None:
        try:
            default = doc.styles["Normal"].font.size
            size = default.pt if default else 11.0
        except KeyError:
            size = 11.0
    return {
        "style": style_name,
        "is_list": has_num or "list" in style_name.lower(),
        "is_heading_style": style_name.lower().startswith(("heading", "title")),
        "bold": bool(bolds) and all(bolds),
        "size": size,
        "in_table": _inside(p, W_TC, None),
    }


def read_docx(data: bytes, as_of: date) -> ResumeModel:
    doc = Document(io.BytesIO(data))
    items: list[RawItem] = []
    # Header text (often holds contact details) is scored but never edited.
    seen_parts = set()
    for i, section in enumerate(doc.sections):
        for kind, hf in (("header", section.header), ("footer", section.footer)):
            if hf.is_linked_to_previous or id(hf.part) in seen_parts:
                continue
            seen_parts.add(id(hf.part))
            for j, p in enumerate(hf._element.iter(W_P)):
                t = para_text(p)
                if t.strip():
                    items.append(RawItem(f"{kind}{i}-{j}", t, style={"part": kind}))
    for idx, p in enumerate(body_paragraphs(doc)):
        t = para_text(p)
        if not t.strip():
            continue
        info = paragraph_info(doc, p)
        items.append(RawItem(
            id=f"p{idx}", text=t, is_bullet=info["is_list"], is_bold=info["bold"],
            is_heading_style=info["is_heading_style"], font_size=info["size"], in_table=info["in_table"],
            style={"style": info["style"], "size": info["size"], "bold": info["bold"]},
        ))
    cols = 1
    for s in doc.sections:
        c = s._sectPr.find(qn("w:cols"))
        if c is not None and c.get(qn("w:num")):
            cols = max(cols, int(c.get(qn("w:num"))))
    meta = {"columns": cols, "tables": len(doc.tables)}
    model = build_model(items, "docx", as_of, page_count=None, meta=meta)
    # Header/footer items are contact info regardless of where they appear.
    for b in model.blocks:
        if b.id.startswith(("header", "footer")):
            b.section, b.kind = "contact", "contact"
    return model
