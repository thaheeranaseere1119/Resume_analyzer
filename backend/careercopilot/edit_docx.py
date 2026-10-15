"""Structure-aware, targeted DOCX editing.

Only word/document.xml is rewritten; every other package part (styles,
numbering, headers/footers, relationships, fonts, theme, docProps, media) is
copied byte-for-byte. Inside document.xml only the targeted paragraph changes:

* replace     - word-level diff mapped onto the existing runs, so unchanged
                words keep their exact run formatting (bold labels, links...)
                and the paragraph properties (style, numbering, indentation,
                spacing, tab stops, alignment) are untouched.
* insert_after- a new paragraph cloned from a neighbouring paragraph in the
                same section: it inherits pPr (bullet/numbering, indentation,
                spacing) and the run formatting of the neighbour's body text.

`verify_docx_edit` re-opens the result and records which formatting
properties were verified as preserved.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import io
import re
import zipfile

from docx import Document
from lxml import etree

from .docx_io import W_BR, W_DEL, W_HYPERLINK, W_P, W_R, W_T, W_TAB, _inside, para_text, qn
from . import textutil as tu

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
DOC_PART = "word/document.xml"


class DocxEditError(Exception):
    pass


# ---------------------------------------------------------------------------
# package helpers
# ---------------------------------------------------------------------------
def _read_document_xml(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = etree.fromstring(z.read(DOC_PART))
    return root


def _write_package(data: bytes, new_doc_xml: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            payload = new_doc_xml if info.filename == DOC_PART else zin.read(info.filename)
            zout.writestr(info, payload, compress_type=info.compress_type)
    return out.getvalue()


def _serialize(root) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _body_paras(root) -> list:
    body = root.find(qn("w:body"))
    return list(body.iter(W_P))


def _para_by_block(root, block_id: str):
    m = re.fullmatch(r"p(\d+)", block_id)
    if not m:
        raise DocxEditError(f"Block {block_id} is not an editable body paragraph")
    paras = _body_paras(root)
    idx = int(m.group(1))
    if idx >= len(paras):
        raise DocxEditError(f"Paragraph {block_id} not found")
    return paras[idx], idx


# ---------------------------------------------------------------------------
# run-aware text replacement
# ---------------------------------------------------------------------------
def _char_owners(p):
    """Return (text, owners, tnodes): owners[i] is the w:t index for char i, or None for tabs/breaks."""
    tnodes, chars, owners = [], [], []
    for el in p.iter():
        if el.tag == W_T:
            if _inside(el, W_DEL, p):
                continue
            tnodes.append(el)
            for ch in el.text or "":
                chars.append(ch)
                owners.append(len(tnodes) - 1)
        elif el.tag == W_TAB and el.getparent() is not None and el.getparent().tag == W_R:
            chars.append("\t"); owners.append(None)
        elif el.tag == W_BR and el.get(qn("w:type")) in (None, "textWrapping"):
            chars.append("\n"); owners.append(None)
    return "".join(chars), owners, tnodes


def _tokens(s: str) -> list[str]:
    return re.findall(r"\s+|[A-Za-z0-9]+|[^A-Za-z0-9\s]", s)


def _in_link(t, p) -> bool:
    return _inside(t, W_HYPERLINK, p)


def replace_paragraph_text(p, new_text: str) -> dict:
    old, owners, tnodes = _char_owners(p)
    if not tnodes:
        raise DocxEditError("Paragraph has no editable text runs")
    a, b = _tokens(old), _tokens(new_text)
    a_pos = [0]
    for t in a:
        a_pos.append(a_pos[-1] + len(t))
    assigned: list[list[str]] = [[] for _ in tnodes]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    last_owner = next((o for o in owners if o is not None), 0)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        c1, c2 = a_pos[i1], a_pos[i2]
        if op == "equal":
            for k in range(c1, c2):
                if owners[k] is not None:
                    assigned[owners[k]].append(old[k])
                    last_owner = owners[k]
            continue
        if any(owners[k] is None for k in range(c1, c2)):
            raise DocxEditError("Edit would remove a tab or line break; refusing to alter layout characters")
        ins = "".join(b[j1:j2])
        prev_owner = next((owners[k] for k in range(c1 - 1, -1, -1) if owners[k] is not None), None)
        next_owner = next((owners[k] for k in range(c2, len(owners)) if owners[k] is not None), None)
        owner = prev_owner if prev_owner is not None else (next_owner if next_owner is not None else last_owner)
        # Do not extend a hyperlink with new words when a plain run follows.
        if (prev_owner is not None and next_owner is not None and _in_link(tnodes[prev_owner], p)
                and not _in_link(tnodes[next_owner], p)):
            owner = next_owner
        if ins:
            assigned[owner].append(ins)
            last_owner = owner
    touched = []
    for i, t in enumerate(tnodes):
        txt = "".join(assigned[i])
        if txt != (t.text or ""):
            touched.append(i)
        t.text = txt
        if txt != txt.strip() or "  " in txt:
            t.set(XML_SPACE, "preserve")
    # Drop runs that became completely empty (no text, tabs, drawings...)
    for t in tnodes:
        if t.text == "":
            r = t.getparent()
            t.getparent().remove(t)
            if r.tag == W_R and all(c.tag == qn("w:rPr") for c in r):
                r.getparent().remove(r)
    result = para_text(p)
    if result != new_text:
        raise DocxEditError(f"Text mapping mismatch: {result!r} != {new_text!r}")
    return {"runs_total": len(tnodes), "runs_touched": len(touched)}


# ---------------------------------------------------------------------------
# paragraph insertion with style inheritance
# ---------------------------------------------------------------------------
def _body_run_props(template):
    """rPr of the first run that carries body text (after any manual bullet prefix)."""
    text, owners, tnodes = _char_owners(template)
    glyph, rest = tu.strip_bullet(text)
    start = len(text) - len(rest) if glyph else 0
    for k in range(start, len(text)):
        if owners[k] is not None and text[k].strip():
            run = tnodes[owners[k]].getparent()
            return run.find(qn("w:rPr")), start
    return None, start


def build_inserted_paragraph(template, body_text: str):
    new_p = copy.deepcopy(template)
    for attr in list(new_p.attrib):
        if attr.startswith("{%s}" % W14):  # paraId/textId must stay unique
            del new_p.attrib[attr]
    ppr = new_p.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:sectPr")) is not None:
        ppr.remove(ppr.find(qn("w:sectPr")))  # never duplicate a section break
    rpr, prefix_len = _body_run_props(template)
    full = para_text(template)
    children = [c for c in new_p if c.tag != qn("w:pPr")]
    for c in children:
        new_p.remove(c)
    # Keep whole runs that form a manual bullet prefix (e.g. a Symbol-font glyph + tab).
    kept = ""
    if prefix_len:
        for c in children:
            if c.tag != W_R:
                continue
            rt = para_text(c)
            if len(kept) + len(rt) > prefix_len:
                break
            new_p.append(c)
            kept += rt
    run = etree.SubElement(new_p, W_R)
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    t = etree.SubElement(run, W_T)
    t.text = full[len(kept):prefix_len] + body_text
    t.set(XML_SPACE, "preserve")
    return new_p, full[:prefix_len]


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def apply_docx_edit(data: bytes, op: dict) -> tuple[bytes, dict]:
    """op = {mode: replace|insert_after, block_id, text, expected_text?, style_source_id?}"""
    root = _read_document_xml(data)
    p, idx = _para_by_block(root, op["block_id"])
    current = para_text(p)
    body = tu.strip_bullet(current)[1]
    if op.get("expected_text") is not None and tu.normalize_ws(op["expected_text"]) not in (
            tu.normalize_ws(current), tu.normalize_ws(body)):
        raise DocxEditError("The target paragraph changed since the recommendation was generated")
    info: dict = {"mode": op["mode"], "paragraph_index": idx}
    if op["mode"] == "replace":
        glyph, rest = tu.strip_bullet(current)
        prefix = current[: len(current) - len(rest)] if glyph else ""
        new_full = prefix + op["text"]
        info.update(replace_paragraph_text(p, new_full))
        info["new_paragraph_index"] = idx
    elif op["mode"] == "insert_after":
        template = p
        if op.get("style_source_id"):
            template, _ = _para_by_block(root, op["style_source_id"])
        new_p, prefix = build_inserted_paragraph(template, op["text"])
        p.addnext(new_p)
        info["new_paragraph_index"] = idx + 1
        info["style_source_index"] = _body_paras(root).index(template)
        info["manual_bullet_prefix"] = prefix
    else:
        raise DocxEditError(f"Unknown edit mode {op['mode']}")
    return _write_package(data, _serialize(root)), info


def _para_xml(p) -> bytes:
    return etree.tostring(p, method="c14n")


def _strip_w14(xml: bytes) -> bytes:
    return re.sub(rb'\sw14:(paraId|textId)="[^"]*"', b"", xml)


def verify_docx_edit(before: bytes, after: bytes, op: dict, info: dict) -> dict:
    """Independently re-open the exported DOCX and record preserved properties."""
    report: list[dict] = []

    def rec(prop, ok, detail=""):
        report.append({"property": prop, "status": "preserved" if ok else "changed", "detail": detail})

    # 1. Valid & editable
    valid = True
    try:
        Document(io.BytesIO(after))
        with zipfile.ZipFile(io.BytesIO(after)) as z:
            valid = z.testzip() is None
    except Exception as exc:  # pragma: no cover - reported, not raised
        valid = False
        report.append({"property": "Valid DOCX package", "status": "changed", "detail": str(exc)})
    if valid:
        rec("Valid, editable DOCX package", True, "Re-opened with python-docx; ZIP CRC check passed")

    # 2. Untouched package parts byte-identical
    with zipfile.ZipFile(io.BytesIO(before)) as zb, zipfile.ZipFile(io.BytesIO(after)) as za:
        names_b = [i.filename for i in zb.infolist()]
        names_a = [i.filename for i in za.infolist()]
        diff_parts = [n for n in names_b if n != DOC_PART and (n not in names_a or zb.read(n) != za.read(n))]
        rec("Package parts other than document.xml", names_a == names_b and not diff_parts,
            "styles, numbering, theme, fonts, settings, headers/footers, relationships and docProps byte-identical"
            if not diff_parts else f"changed: {diff_parts}")
        hf = [n for n in names_b if re.match(r"word/(header|footer)\d*\.xml", n)]
        rec("Header and footer content", not any(n in diff_parts for n in hf),
            f"{len(hf)} header/footer part(s) unchanged" if hf else "document has no header/footer parts")
        rec("Font families and style definitions", "word/styles.xml" not in diff_parts and "word/fontTable.xml" not in diff_parts,
            "styles.xml and fontTable.xml unchanged")
        rec("Bullet and numbering definitions", "word/numbering.xml" not in diff_parts,
            "numbering.xml unchanged" if "word/numbering.xml" in names_b else "no numbering part")
        rec("Hyperlink targets", "word/_rels/document.xml.rels" not in diff_parts, "relationships unchanged")

    rb, ra = _read_document_xml(before), _read_document_xml(after)
    pb, pa = _body_paras(rb), _body_paras(ra)
    i = info["paragraph_index"]
    if op["mode"] == "replace":
        expected_other = [_para_xml(x) for k, x in enumerate(pb) if k != i]
        actual_other = [_para_xml(x) for k, x in enumerate(pa) if k != i]
        edited_before, edited_after = pb[i], pa[i]
    else:
        expected_other = [_para_xml(x) for x in pb]
        actual_other = [_para_xml(x) for k, x in enumerate(pa) if k != i + 1]
        edited_before = pb[info["style_source_index"]]
        edited_after = pa[i + 1]
    same_other = expected_other == actual_other
    rec("Unrelated paragraphs (headings, dates, contact info, section order)", same_other,
        f"{len(actual_other)} other paragraphs byte-identical after canonicalisation" if same_other
        else "some unrelated paragraphs differ")

    sect_b, sect_a = rb.find(qn("w:body")).find(qn("w:sectPr")), ra.find(qn("w:body")).find(qn("w:sectPr"))
    same_sect = (sect_b is None and sect_a is None) or (
        sect_b is not None and sect_a is not None and _para_xml(sect_b) == _para_xml(sect_a))
    rec("Page dimensions, margins and column layout", same_sect, "body section properties (pgSz, pgMar, cols) unchanged")
    tb = [_para_xml(t.find(qn("w:tblPr"))) for t in rb.iter(qn("w:tbl")) if t.find(qn("w:tblPr")) is not None]
    ta = [_para_xml(t.find(qn("w:tblPr"))) for t in ra.iter(qn("w:tbl")) if t.find(qn("w:tblPr")) is not None]
    rec("Tables", tb == ta, f"{len(tb)} table(s) with unchanged table properties")
    pg_b = len(rb.xpath('.//w:br[@w:type="page"]', namespaces={"w": qn("w:x")[1:].split("}")[0]}))
    pg_a = len(ra.xpath('.//w:br[@w:type="page"]', namespaces={"w": qn("w:x")[1:].split("}")[0]}))
    rec("Explicit page breaks", pg_b == pg_a, f"{pg_a} page break(s)")
    hl_b, hl_a = len(list(rb.iter(W_HYPERLINK))), len(list(ra.iter(W_HYPERLINK)))
    rec("Hyperlinks", hl_b == hl_a, f"{hl_a} hyperlink element(s)")

    ppr_b, ppr_a = edited_before.find(qn("w:pPr")), edited_after.find(qn("w:pPr"))
    if ppr_b is not None and ppr_b.find(qn("w:sectPr")) is not None and op["mode"] == "insert_after":
        ppr_b = copy.deepcopy(ppr_b)
        ppr_b.remove(ppr_b.find(qn("w:sectPr")))
    same_ppr = (ppr_b is None and ppr_a is None) or (
        ppr_b is not None and ppr_a is not None and _para_xml(ppr_b) == _para_xml(ppr_a))
    label = "inherited from neighbouring paragraph" if op["mode"] == "insert_after" else "kept on edited paragraph"
    rec("Paragraph style, bullets, indentation, spacing, alignment, tab stops", same_ppr, f"pPr {label}")

    rprs_b = {_para_xml(r.find(qn("w:rPr"))) if r.find(qn("w:rPr")) is not None else b"" for r in edited_before.iter(W_R)}
    rprs_a = {_para_xml(r.find(qn("w:rPr"))) if r.find(qn("w:rPr")) is not None else b"" for r in edited_after.iter(W_R)}
    rec("Run formatting (font, size, weight, italics, underline, colour)", rprs_a <= rprs_b,
        f"{len(rprs_a)} run format(s) used, all taken from the {'neighbouring' if op['mode'] == 'insert_after' else 'original'} paragraph")
    expected = op["text"]
    got = tu.strip_bullet(para_text(edited_after))[1] if tu.strip_bullet(para_text(edited_after))[0] else para_text(edited_after)
    rec("Edited text written exactly", tu.normalize_ws(got) == tu.normalize_ws(expected), got)
    return {
        "format": "docx",
        "method": "targeted XML edit (document.xml only)",
        "exact": all(r["status"] == "preserved" for r in report),
        "properties": report,
        "sha256": hashlib.sha256(after).hexdigest(),
    }
