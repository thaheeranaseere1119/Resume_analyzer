"""Document previews.

PDF  -> rendered page images (exact rendering by MuPDF) with the edited
        region outlined on the preview only.
DOCX -> formatting-aware HTML (page size, margins, fonts, sizes, weights,
        colours, alignment, indentation, spacing, bullets/numbering, tab
        stops, tables, section columns, page breaks, headers/footers,
        hyperlinks). This is an approximation of Word's layout engine and is
        labelled as such in the UI.
"""
from __future__ import annotations

import base64
import html
import io
import re

import pymupdf as fitz
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .docx_io import W_P, W_R, W_T, W_TAB, W_BR, W_HYPERLINK, body_paragraphs


def pdf_preview(data: bytes, highlights: list[tuple[int, tuple]] | None = None, dpi: int = 110) -> list[str]:
    doc = fitz.open(stream=data, filetype="pdf")
    out = []
    for pno, page in enumerate(doc):
        for hp, rect in highlights or []:
            if hp == pno:
                annot = page.add_rect_annot(fitz.Rect(rect))
                annot.set_colors(stroke=(0.85, 0.33, 0.36), fill=None)
                annot.set_border(width=1.2, dashes=[3, 2])
                annot.update(opacity=0.9)
        pix = page.get_pixmap(dpi=dpi, annots=True)
        out.append("data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode())
    doc.close()
    return out


# ---------------------------------------------------------------------------
# DOCX -> HTML
# ---------------------------------------------------------------------------
def _twips_to_pt(v) -> float:
    return int(v) / 20.0


def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


class _DocxRenderer:
    def __init__(self, data: bytes):
        self.doc = Document(io.BytesIO(data))
        # Keep the element proxies alive: lxml only guarantees a stable id() while a reference exists.
        self._paras = body_paragraphs(self.doc)
        self.index = {id(p): i for i, p in enumerate(self._paras)}
        self.num_counters: dict = {}
        self.numbering = self._load_numbering()
        try:
            normal = self.doc.styles["Normal"]
            self.default_font = normal.font.name or "Calibri"
            self.default_size = normal.font.size.pt if normal.font.size else 11.0
        except KeyError:
            self.default_font, self.default_size = "Calibri", 11.0
        rpr_default = self.doc.styles.element.find(qn("w:docDefaults"))
        if rpr_default is not None:
            rf = rpr_default.find(".//" + qn("w:rFonts"))
            if rf is not None and rf.get(qn("w:ascii")) and not self.doc.styles["Normal"].font.name:
                self.default_font = rf.get(qn("w:ascii"))

    def _load_numbering(self) -> dict:
        try:
            part = self.doc.part.numbering_part
        except Exception:
            return {}
        el = part.element
        abstract = {}
        for a in el.findall(qn("w:abstractNum")):
            lv = {}
            for l in a.findall(qn("w:lvl")):
                fmt = l.find(qn("w:numFmt")); txt = l.find(qn("w:lvlText"))
                lv[int(l.get(qn("w:ilvl")))] = (fmt.get(qn("w:val")) if fmt is not None else "bullet",
                                               txt.get(qn("w:val")) if txt is not None else "•")
            abstract[a.get(qn("w:abstractNumId"))] = lv
        nums = {}
        for n in el.findall(qn("w:num")):
            aid = n.find(qn("w:abstractNumId")).get(qn("w:val"))
            nums[n.get(qn("w:numId"))] = abstract.get(aid, {})
        return nums

    def _num_props(self, para: Paragraph):
        ppr = para._p.pPr
        numpr = ppr.numPr if ppr is not None else None
        if numpr is None:
            for st in _style_chain(para.style):
                sp = st.element.pPr
                if sp is not None and sp.numPr is not None:
                    numpr = sp.numPr
                    break
        if numpr is None or numpr.numId is None:
            return None
        num_id = str(numpr.numId.val)
        ilvl = int(numpr.ilvl.val) if numpr.ilvl is not None else 0
        fmt, txt = self.numbering.get(num_id, {}).get(ilvl, ("bullet", "•"))
        if fmt == "bullet":
            glyph = txt if txt and ord(txt[0]) < 0xF000 else "•"
            return glyph if glyph.strip() else "•"
        key = (num_id, ilvl)
        self.num_counters[key] = self.num_counters.get(key, 0) + 1
        n = self.num_counters[key]
        val = {"decimal": str(n), "lowerLetter": chr(96 + n), "upperLetter": chr(64 + n),
               "lowerRoman": ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][min(n, 10) - 1]}.get(fmt, str(n))
        return re.sub(r"%\d", val, txt or "%1.")

    def _run_css(self, run, para) -> str:
        css = []
        name = run.font.name or next((s.font.name for s in _style_chain(run.style) if s.font.name), None) or \
            next((s.font.name for s in _style_chain(para.style) if s.font.name), None) or self.default_font
        size = run.font.size or next((s.font.size for s in _style_chain(para.style) if s.font.size), None)
        size = size.pt if size else self.default_size
        bold = run.bold if run.bold is not None else next((s.font.bold for s in _style_chain(para.style) if s.font.bold is not None), False)
        italic = run.italic if run.italic is not None else next((s.font.italic for s in _style_chain(para.style) if s.font.italic is not None), False)
        under = run.underline if run.underline is not None else next((s.font.underline for s in _style_chain(para.style) if s.font.underline is not None), False)
        color = None
        if run.font.color is not None and run.font.color.type is not None and run.font.color.rgb is not None:
            color = str(run.font.color.rgb)
        else:
            for s in _style_chain(para.style):
                if s.font.color is not None and s.font.color.type is not None and s.font.color.rgb is not None:
                    color = str(s.font.color.rgb); break
        name = re.sub(r"[^A-Za-z0-9 _-]", "", str(name))[:60] or "sans-serif"  # font names come from the untrusted file
        css.append(f"font-family:'{name}',sans-serif;font-size:{float(size):.1f}pt")
        if bold: css.append("font-weight:700")
        if italic: css.append("font-style:italic")
        if under: css.append("text-decoration:underline")
        if color and re.fullmatch(r"[0-9A-Fa-f]{6}", color): css.append(f"color:#{color}")
        return ";".join(css)

    def _para_html(self, p_el, highlight: set) -> str:
        para = Paragraph(p_el, self.doc.part)
        pf = para.paragraph_format
        def chain(attr):
            v = getattr(pf, attr)
            if v is None:
                for s in _style_chain(para.style):
                    v = getattr(s.paragraph_format, attr)
                    if v is not None:
                        break
            return v
        css = []
        al = chain("alignment")
        if al is not None:
            css.append("text-align:" + {0: "left", 1: "center", 2: "right", 3: "justify"}.get(int(al), "left"))
        for attr, prop in (("space_before", "margin-top"), ("space_after", "margin-bottom")):
            v = chain(attr)
            css.append(f"{prop}:{v.pt if v is not None else (0 if attr == 'space_before' else 8)}pt")
        li, fi = chain("left_indent"), chain("first_line_indent")
        bullet = self._num_props(para)
        if bullet is not None and li is None:
            li = 457200  # 0.5in default list indent (EMU)
        if li: css.append(f"padding-left:{li.pt if hasattr(li, 'pt') else li / 12700}pt")
        if fi: css.append(f"text-indent:{fi.pt}pt")
        ls = chain("line_spacing")
        if isinstance(ls, float):
            css.append(f"line-height:{ls * 1.15:.2f}")
        right_tab = any(ts.alignment == 2 or str(ts.alignment).endswith("RIGHT (2)") for ts in pf.tab_stops) if pf.tab_stops else False
        parts: list[str] = []
        segments: list[list[str]] = [[]]
        page_break = False
        for child in p_el.iterchildren():
            runs = [child] if child.tag == W_R else (list(child.iter(W_R)) if child.tag == W_HYPERLINK else [])
            href = None
            if child.tag == W_HYPERLINK:
                rid = child.get(qn("r:id"))
                if rid and rid in self.doc.part.rels:
                    href = self.doc.part.rels[rid].target_ref
                    if not re.match(r"^(https?://|mailto:)", href or "", re.I):
                        href = None  # never render javascript:, data:, file: or relative links
            for r_el in runs:
                from docx.text.run import Run
                run = Run(r_el, para)
                rcss = self._run_css(run, para)
                for node in r_el.iterchildren():
                    if node.tag == W_T:
                        txt = html.escape(node.text or "")
                        style = html.escape(rcss, quote=True)
                        span = f'<span style="{style}">{txt}</span>'
                        if href:
                            span = (f'<a href="{html.escape(href, quote=True)}" rel="noopener noreferrer nofollow" '
                                    f'target="_blank" style="{style}">{txt}</a>')
                        segments[-1].append(span)
                    elif node.tag == W_TAB:
                        segments.append([])
                    elif node.tag == W_BR:
                        if node.get(qn("w:type")) == "page":
                            page_break = True
                        else:
                            segments[-1].append("<br>")
        if bullet is not None:
            first = self._run_css(para.runs[0], para) if para.runs else ""
            lead = f'<span class="cc-bullet" style="{html.escape(first, quote=True)}">{html.escape(bullet)}</span>'
        else:
            lead = ""
        if len(segments) > 1 and right_tab:
            body = (f'<span class="cc-tabrow"><span>{"".join(segments[0])}</span>'
                    f'<span>{"&emsp;".join("".join(s) for s in segments[1:])}</span></span>')
        else:
            body = '<span class="cc-tab"></span>'.join("".join(s) for s in segments)
        idx = self.index.get(id(p_el))
        cls = "cc-p" + (" cc-hl" if idx in highlight else "")
        out = f'<p class="{cls}" data-block="p{idx}" style="{html.escape(";".join(css), quote=True)}">{lead}{body or "&nbsp;"}</p>'
        if page_break:
            out += '<div class="cc-pagebreak" title="Page break"></div>'
        return out

    def _table_html(self, tbl_el, highlight: set) -> str:
        rows = []
        for tr in tbl_el.findall(qn("w:tr")):
            cells = []
            for tc in tr.findall(qn("w:tc")):
                w = tc.find(qn("w:tcPr") + "/" + qn("w:tcW"))
                width = f"width:{_twips_to_pt(w.get(qn('w:w')))}pt;" if w is not None and w.get(qn("w:type")) == "dxa" else ""
                inner = "".join(self._block_html(c, highlight) for c in tc.iterchildren() if c.tag in (W_P, qn("w:tbl")))
                cells.append(f'<td style="{width}vertical-align:top;padding:2pt 5pt">{inner}</td>')
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return '<table class="cc-table">' + "".join(rows) + "</table>"

    def _block_html(self, el, highlight: set) -> str:
        if el.tag == W_P:
            return self._para_html(el, highlight)
        if el.tag == qn("w:tbl"):
            return self._table_html(el, highlight)
        return ""

    def _hf_html(self, part) -> str:
        return "".join(self._para_html(p, set()) for p in part._element.iter(W_P))

    def render(self, highlight: set) -> str:
        body = self.doc.element.body
        sect = self.doc.sections[0]
        width, height = sect.page_width.pt if sect.page_width else 612, sect.page_height.pt if sect.page_height else 792
        ml = sect.left_margin.pt if sect.left_margin is not None else 72
        mr = sect.right_margin.pt if sect.right_margin is not None else 72
        mt = sect.top_margin.pt if sect.top_margin is not None else 72
        mb = sect.bottom_margin.pt if sect.bottom_margin is not None else 72
        sections, cur = [], []
        for el in body.iterchildren():
            if el.tag == qn("w:sectPr"):
                continue
            cur.append(el)
            sp = el.find(qn("w:pPr") + "/" + qn("w:sectPr")) if el.tag == W_P else None
            if sp is not None:
                sections.append((cur, sp)); cur = []
        sections.append((cur, body.find(qn("w:sectPr"))))
        html_parts = []
        for els, sp in sections:
            cols = sp.find(qn("w:cols")) if sp is not None else None
            n = int(cols.get(qn("w:num"))) if cols is not None and cols.get(qn("w:num")) else 1
            inner = "".join(self._block_html(e, highlight) for e in els)
            style = f"column-count:{n};column-gap:{_twips_to_pt(cols.get(qn('w:space')) or 720)}pt" if n > 1 else ""
            html_parts.append(f'<div class="cc-section" style="{style}">{inner}</div>')
        header = self._hf_html(sect.header) if not sect.header.is_linked_to_previous else ""
        footer = self._hf_html(sect.footer) if not sect.footer.is_linked_to_previous else ""
        return (f'<div class="cc-docx-page" style="width:{width}pt;min-height:{height}pt;'
                f'padding:{mt}pt {mr}pt {mb}pt {ml}pt">'
                f'<div class="cc-header">{header}</div>{"".join(html_parts)}'
                f'<div class="cc-footer">{footer}</div></div>')


def docx_preview(data: bytes, highlight_ids: list[str] | None = None) -> str:
    hl = {int(h[1:]) for h in (highlight_ids or []) if re.fullmatch(r"p\d+", h)}
    return _DocxRenderer(data).render(hl)
