"""Targeted PDF editing with layout preservation and honest reporting.

Strategy
--------
* Pages that are not affected are copied unchanged (``insert_pdf``).
* An affected page is rebuilt from *regions of the original page*: each
  region is drawn with ``show_pdf_page`` (vector content, original embedded
  fonts, images) and, in the edited column, everything below the edit point
  is shifted by the height difference. Before a region is drawn, all text
  outside it is removed by redaction so that no clipped-but-hidden text
  remains for an ATS parser to read twice.
* New text is set with the original embedded font when that font program
  contains every glyph needed; otherwise the same family from a system font,
  and finally a standard PDF font. Substitutions are reported, never hidden.
* If the shifted column would run past the bottom margin, the overflowing
  blocks are carried to the top of the next page (pagination change is
  reported). Scanned pages follow the same region pipeline on the page image.
"""
from __future__ import annotations

import difflib
import glob
import hashlib
import os
import re
import secrets
from dataclasses import dataclass, field

import pymupdf as fitz

from . import textutil as tu
from .pdf_io import PBlock, PdfLayout

STANDARD_NAMES = {
    "Helvetica": "helv", "Helvetica-Bold": "hebo", "Helvetica-Oblique": "heit", "Helvetica-BoldOblique": "hebi",
    "Times-Roman": "tiro", "Times-Bold": "tibo", "Times-Italic": "tiit", "Times-BoldItalic": "tibi",
    "Courier": "cour", "Courier-Bold": "cobo", "Courier-Oblique": "coit", "Courier-BoldOblique": "cobi",
}
FONT_DIRS = ["/System/Library/Fonts/Supplemental", "/System/Library/Fonts", "/Library/Fonts",
             os.path.expanduser("~/Library/Fonts"), "/usr/share/fonts", "C:/Windows/Fonts"]


class PdfEditError(Exception):
    pass


@dataclass
class FontChoice:
    alias: str
    font: fitz.Font
    buffer: bytes | None
    status: str          # exact | family | substituted
    source_font: str
    note: str = ""


@dataclass
class Placement:
    src_page: int
    clip: fitz.Rect
    dy: float
    dst_page: int


@dataclass
class Plan:
    placements: list[Placement] = field(default_factory=list)
    texts: list[dict] = field(default_factory=list)       # {page, x, baseline, text, style}
    moved_pages: set = field(default_factory=set)
    new_pages: int = 0
    pagination_changed: bool = False
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------
def _base14_for(name: str, flags: int) -> str:
    n = name.lower()
    bold = bool(flags & 16) or any(k in n for k in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or "italic" in n or "oblique" in n
    if any(k in n for k in ("courier", "mono", "consolas", "menlo")):
        fam = {(0, 0): "cour", (1, 0): "cobo", (0, 1): "coit", (1, 1): "cobi"}
    elif any(k in n for k in ("times", "georgia", "garamond", "cambria", "serif", "roman", "minion", "palatino",
                              "book")) and "sans" not in n:
        fam = {(0, 0): "tiro", (1, 0): "tibo", (0, 1): "tiit", (1, 1): "tibi"}
    else:
        fam = {(0, 0): "helv", (1, 0): "hebo", (0, 1): "heit", (1, 1): "hebi"}
    return fam[(int(bold), int(italic))]


_FONT_INDEX: dict[str, str] | None = None


def _norm_font(name: str) -> str:
    n = name.split("+")[-1].lower()
    n = re.sub(r"[\s_\-,]", "", n)
    n = re.sub(r"(psmt|mt|ps)$", "", n)
    n = re.sub(r"(regular|roman|book|normal)$", "", n)
    return n


def _font_index() -> dict[str, str]:
    """normalised font name -> file, from system fonts and the fonts bundled with LibreOffice."""
    global _FONT_INDEX
    if _FONT_INDEX is None:
        idx: dict[str, str] = {}
        dirs = FONT_DIRS + ["/Applications/Microsoft Word.app/Contents/Resources/DFonts",
                            "/Applications/LibreOffice.app/Contents/Resources/fonts/truetype",
                            "/usr/lib/libreoffice/share/fonts/truetype", "/usr/share/fonts/truetype"]
        for d in dirs:
            for path in glob.glob(os.path.join(d, "**", "*.[ot]tf"), recursive=True):
                stem = os.path.splitext(os.path.basename(path))[0]
                idx.setdefault(_norm_font(stem), path)
                try:
                    idx.setdefault(_norm_font(fitz.Font(fontfile=path).name), path)
                except Exception:
                    pass
        _FONT_INDEX = idx
    return _FONT_INDEX


# Metric-compatible substitutes (same widths, different glyph design) used only when the real font is absent.
METRIC_COMPAT = {"calibri": "carlito", "cambria": "caladea", "arial": "liberationsans", "helvetica": "liberationsans",
                 "timesnewroman": "liberationserif", "couriernew": "liberationmono"}


def _system_family_file(name: str) -> str | None:
    return _font_index().get(_norm_font(name))


def _metric_compatible_file(name: str) -> str | None:
    n = _norm_font(name)
    for base, sub in METRIC_COMPAT.items():
        if n.startswith(base):
            return _font_index().get(sub + n[len(base):])
    return None


def _font_xref(doc, pno: int, font_name: str) -> int | None:
    for f in doc[pno].get_fonts(full=True):
        xref, ext, ftype, basefont = f[0], f[1], f[2], f[3]
        if basefont.split("+")[-1] == font_name:
            return xref
    return None


def _roundtrip_ok(alias: str, buffer: bytes | None, text: str) -> bool:
    """Write `text` with the font into a scratch PDF and require exact extraction.

    Guards against font programs that render correctly but lack a usable
    Unicode mapping (the text would be unreadable to an ATS)."""
    sample = "".join(sorted(set(text))) or "a"
    try:
        d = fitz.open()
        p = d.new_page()
        if buffer is not None:
            p.insert_font(fontname=alias, fontbuffer=buffer)
        p.insert_text((20, 50), sample, fontname=alias, fontsize=10)
        got = d[0].get_text()
        d.close()
        strip = lambda x: "".join(c for c in x if not c.isspace())
        return strip(got) == strip(sample)
    except Exception:
        return False


def resolve_font(doc, pno: int, span: dict, chars: str, cache: dict) -> FontChoice:
    key = (span["font"], span["flags"], frozenset(chars))
    if key in cache:
        return cache[key]
    # Font resource names must be unique per edit: earlier edits leave their own font
    # resources inside the page XObjects, and reusing a name would bind the wrong font.
    nonce = cache.setdefault("_nonce", secrets.token_hex(3))
    name = span["font"]
    need = {c for c in chars if not c.isspace()}
    choice = None
    if name in STANDARD_NAMES:
        alias = STANDARD_NAMES[name]
        choice = FontChoice(alias, fitz.Font(alias), None, "exact", name, "standard PDF font")
    elif name != "OCR":
        xref = _font_xref(doc, pno, name)
        if xref:
            try:
                _, ext, _, buf = doc.extract_font(xref)
                if buf:
                    f = fitz.Font(fontbuffer=buf)
                    missing = [c for c in need if not f.has_glyph(ord(c))]
                    if not missing and _roundtrip_ok(f"cc{xref}", buf, chars):
                        choice = FontChoice(f"cc{xref}{nonce}", f, buf, "exact", name, "original embedded font program")
                    elif not missing:
                        cache.setdefault("_missing", {})[name] = "(no Unicode mapping)"
                    else:
                        cache.setdefault("_missing", {})[name] = "".join(sorted(missing))
            except Exception:  # pragma: no cover - font programs vary widely
                pass
        if choice is None:
            path = _system_family_file(name)
            if path:
                f = fitz.Font(fontfile=path)
                if all(f.has_glyph(ord(c)) for c in need) and _roundtrip_ok("ccsystest", open(path, "rb").read(), chars):
                    # Same typeface, same weight/style: the full font of which the PDF embeds a subset.
                    choice = FontChoice("ccsys" + hashlib.md5(path.encode()).hexdigest()[:4] + nonce, f,
                                        open(path, "rb").read(), "exact", name,
                                        f"same font: full {os.path.basename(path)} (your PDF embeds only a subset)")
    if choice is None and name != "OCR":
        path = _metric_compatible_file(name)
        if path:
            f = fitz.Font(fontfile=path)
            buf = open(path, "rb").read()
            if all(f.has_glyph(ord(c)) for c in need) and _roundtrip_ok("ccmetric", buf, chars):
                choice = FontChoice("ccmc" + hashlib.md5(path.encode()).hexdigest()[:4] + nonce, f, buf, "family",
                                    name, f"metric-compatible substitute {os.path.basename(path)} (original font not installed)")
    if choice is None:
        alias = _base14_for(name, span["flags"])
        note = "OCR text has no font information" if name == "OCR" else "closest standard font"
        choice = FontChoice(alias, fitz.Font(alias), None, "substituted", name, note)
    cache[key] = choice
    return choice


def _rgb(color) -> tuple:
    if isinstance(color, (tuple, list)):
        return tuple(color)
    c = int(color)
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


# ---------------------------------------------------------------------------
# text styling & wrapping
# ---------------------------------------------------------------------------
def _block_char_spans(blk: PBlock) -> tuple[str, list]:
    """Block body text (as the reader builds it) and the span owning each char."""
    parts = []
    for i, line in enumerate(blk.lines):
        chars, owners = [], []
        for s in line.spans:
            if s.get("synthetic"):
                chars.append(" "); owners.append(None)
                continue
            t = s["text"]
            if i == 0 and s is blk.bullet_span:
                rest = t.strip()[1:]
                if not rest.strip():
                    continue
                t = rest
            for ch in t:
                chars.append(ch); owners.append(s)
        s_ = "".join(chars)
        lead = len(s_) - len(s_.lstrip())
        trail = len(s_.rstrip())
        parts.append((s_[lead:trail], owners[lead:trail]))
    text, owners = "", []
    for k, (t, o) in enumerate(parts):
        if not t:
            continue
        if text:
            text += " "; owners.append(owners[-1] if owners else None)
        text += t; owners.extend(o)
    # fill None owners (synthetic separators) from neighbours
    last = next((o for o in owners if o is not None), None)
    for k, o in enumerate(owners):
        if o is None:
            owners[k] = last
        else:
            last = o
    return text, owners


def _assign_styles(old: str, owners: list, new: str, default_span: dict) -> list:
    tok = lambda s: re.findall(r"\s+|[A-Za-z0-9]+|[^A-Za-z0-9\s]", s)
    a, b = tok(old), tok(new)
    pos = [0]
    for t in a:
        pos.append(pos[-1] + len(t))
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.extend(owners[pos[i1]:pos[i2]])
            continue
        prev = owners[pos[i1] - 1] if pos[i1] > 0 and owners else None
        nxt = owners[pos[i2]] if pos[i2] < len(owners) else None
        owner = prev or nxt or default_span
        out.extend([owner] * len("".join(b[j1:j2])))
    return out


def _layout_lines(text: str, styles: list, fonts: dict, first_w: float, cont_w: float) -> list[list]:
    """Greedy word wrap. Returns lines as lists of (segment_text, span)."""
    words, cur, cur_st = [], "", []
    for ch, st in zip(text, styles):
        if ch == " ":
            if cur:
                words.append((cur, cur_st))
            words.append((" ", [st]))
            cur, cur_st = "", []
        else:
            cur += ch; cur_st.append(st)
    if cur:
        words.append((cur, cur_st))

    def width(seg, sts):
        w = 0.0
        for ch, st in zip(seg, sts):
            f = fonts[id(st)].font
            w += f.text_length(ch, fontsize=st["size"])
        return w

    lines, line, lw = [], [], 0.0
    limit = first_w
    for w, sts in words:
        ww = width(w, sts)
        if w == " ":
            if line:
                line.append((w, sts)); lw += ww
            continue
        if line and lw + ww > limit + 0.01:
            while line and line[-1][0] == " ":
                line.pop()
            lines.append(line)
            line, lw, limit = [], 0.0, cont_w
        line.append((w, sts)); lw += ww
    while line and line[-1][0] == " ":
        line.pop()
    if line:
        lines.append(line)
    # merge into style runs
    out = []
    for line in lines:
        runs = []
        for w, sts in line:
            for ch, st in zip(w, sts):
                if runs and runs[-1][1] is st:
                    runs[-1][0] += ch
                else:
                    runs.append([ch, st])
        out.append([(t, s) for t, s in runs])
    return out


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def _col_range(layout: PdfLayout, pno: int, col: str) -> tuple[float, float]:
    p = layout.pages[pno]
    if p.gutter is None or col == "F":
        return 0.0, p.width
    return (0.0, p.gutter) if col == "L" else (p.gutter, p.width)


def _page_blocks(layout: PdfLayout, pno: int, col: str) -> list[PBlock]:
    out = [b for b in layout.blocks.values() if b.page == pno and (b.col == col or col == "F")]
    return sorted(out, key=lambda b: b.bbox[1])


def _pitch(layout: PdfLayout, blk: PBlock) -> float:
    if len(blk.lines) >= 2:
        return blk.lines[1].baseline - blk.lines[0].baseline
    size = blk.lines[0].size
    cands = [b.lines[1].baseline - b.lines[0].baseline for b in layout.blocks.values()
             if len(b.lines) >= 2 and abs(b.lines[0].size - size) < 0.6]
    cands = [c for c in cands if 0.8 * size < c < 2.2 * size]
    if cands:
        cands.sort()
        return cands[len(cands) // 2]
    return round(size * 1.2, 2)


def _all_spans(layout: PdfLayout, pno: int) -> list[tuple[dict, float]]:
    """(span, baseline) for all spans on a page including furniture."""
    out = []
    for b in layout.blocks.values():
        if b.page == pno:
            for l in b.lines:
                out += [(s, l.baseline) for s in l.spans if not s.get("synthetic")]
    for l in layout.pages[pno].furniture:
        out += [(s, l.baseline) for s in l.spans if not s.get("synthetic")]
    return out


def _span_point(span, baseline):
    x0, _, x1, _ = span["bbox"]
    return (x0 + x1) / 2, baseline - span["size"] * 0.3


def _in_rect(pt, r: fitz.Rect) -> bool:
    return r.x0 <= pt[0] < r.x1 and r.y0 <= pt[1] < r.y1


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------
def _column_box(layout, pno: int, col: str) -> tuple[float, float, float]:
    """(x0, x1, top) hugging the text of `col`, so shapes beside it (sidebars) stay static."""
    info = layout.pages[pno]
    if info.gutter is None or col == "F":
        return 0.0, info.width, 0.0
    lines = [l for b in layout.blocks.values() if b.page == pno and b.col == col for l in b.lines]
    top = min((l.bbox[1] for l in lines), default=0.0) - 1.0
    if col == "L":
        return 0.0, max(l.bbox[2] for l in lines) + 1.5, top
    return min(l.bbox[0] for l in lines) - 1.5, info.width, top


def _plan_column_shift(layout, plan: Plan, pno: int, dst: int, col: str, cut_top: float, cut_bottom: float,
                       delta: float, limit: float, keep_rects=()):
    """Add placements for page `pno` drawn onto `dst`; returns carry (src_page, rect, first_top, last_bottom) or None.

    Regions: a full-width band above the columns, the other column(s) as single unsplit
    regions, and the edited column split into a static top part and a shifted bottom part.
    """
    info = layout.pages[pno]
    W, H = info.width, info.height
    cx0, cx1, col_top = _column_box(layout, pno, col)
    band = max(0.0, min(col_top, cut_top))
    # A region boundary must never slice through a line of text in any column.
    all_lines = [l for b in layout.blocks.values() if b.page == pno for l in b.lines]
    moved = True
    while moved and band > 0:
        moved = False
        for l in all_lines:
            if l.bbox[1] < band < l.bbox[3]:
                band, moved = max(0.0, l.bbox[1] - 0.5), True
    furn_top = min([l.bbox[1] for l in info.furniture if l.bbox[1] > H / 2] + [H])
    add = lambda r, dy: plan.placements.append(Placement(pno, fitz.Rect(r), dy, dst))
    if band > 0:
        add((0, 0, W, band), 0)
    if cx0 > 0:
        add((0, band, cx0, H), 0)
    if cx1 < W:
        add((cx1, band, W, H), 0)
    if cut_top > band:
        add((cx0, band, cx1, cut_top), 0)
    if furn_top < H:
        add((cx0, furn_top, cx1, H), 0)
    for r in keep_rects:
        add(r, 0)
    # Large shapes inside the edited column that cross the edit point cannot be split cleanly.
    if delta:
        try:
            src_page = layout.source_doc[pno] if getattr(layout, "source_doc", None) else None
        except Exception:
            src_page = None
        if src_page is not None:
            for d in src_page.get_drawings():
                r = d["rect"]
                if r.x0 >= cx0 - 1 and r.x1 <= cx1 + 1 and r.y0 < cut_bottom - 1 and r.y1 > cut_bottom + 1 \
                        and r.height > 30 and d.get("fill") is not None:
                    plan.notes.append("A background shape in the edited column crosses the edit point and was split.")
                    break
    moving = [b for b in _page_blocks(layout, pno, col) if b.bbox[1] >= cut_bottom - 0.5 and b.bbox[3] <= furn_top]
    carry = None
    region_bottom = furn_top
    if moving and delta > 0 and max(b.bbox[3] for b in moving) + delta > limit + 0.5:
        # carry the first block that would cross the margin (and everything after) to the next page
        idx = next(i for i, b in enumerate(moving) if b.bbox[3] + delta > limit + 0.5)
        # keep a section heading with its content
        while idx > 0 and _looks_heading(moving[idx - 1]):
            idx -= 1
        first = moving[idx]
        prev_bottom = moving[idx - 1].bbox[3] if idx > 0 else cut_bottom
        cut_y = max(prev_bottom + 0.1, first.bbox[1] - 1.0)
        carry = (pno, fitz.Rect(cx0, cut_y, cx1, furn_top), first.bbox[1], max(b.bbox[3] for b in moving[idx:]),
                 first.bbox[1] - prev_bottom)
        region_bottom = cut_y
        plan.pagination_changed = True
    if region_bottom > cut_bottom:
        add((cx0, cut_bottom, cx1, region_bottom), delta)
    return carry


def bottom_limit(layout: PdfLayout) -> float:
    """Lowest y text may occupy: the deepest existing text, or a bottom margin mirroring the top
    margin, whichever is lower, but always above footer furniture."""
    pages = layout.pages
    H = pages[0].height
    top_margin = min(p.content_top for p in pages)
    limit = max(max(p.content_bottom for p in pages), H - top_margin)
    furn = [l.bbox[1] for p in pages for l in p.furniture if l.bbox[1] > H / 2]
    if furn:
        limit = min(limit, min(furn) - 6)
    return max(limit, max(p.content_bottom for p in pages))


def _looks_heading(b: PBlock) -> bool:
    return tu.classify_heading(" ".join(l.text for l in b.lines)) is not None


def build_plan(src, layout: PdfLayout, op: dict, fonts_cache: dict) -> tuple[Plan, dict]:
    blk = layout.blocks.get(op["block_id"])
    if blk is None:
        raise PdfEditError(f"Block {op['block_id']} not found")
    pno = blk.page
    template = layout.blocks.get(op.get("style_source_id") or op["block_id"])
    pitch = _pitch(layout, template)
    cx0, cx1, _ = _column_box(layout, pno, blk.col)
    right = min(blk.col_bounds[1], _col_range(layout, pno, blk.col)[1])
    plan = Plan()
    body_spans = [s for s in template.body_spans() if s["text"].strip() and not s.get("synthetic")]
    if not body_spans:
        raise PdfEditError("Template block has no text spans")
    new_text = op["text"]

    if op["mode"] == "replace":
        old_text, owners = _block_char_spans(blk)
        styles = _assign_styles(old_text, owners, new_text, body_spans[0])
    else:
        styles = [body_spans[0]] * len(new_text)
    # fonts per distinct span style
    fonts = {}
    by_span: dict[int, str] = {}
    for ch, st in zip(new_text, styles):
        by_span[id(st)] = by_span.get(id(st), "") + ch
    for ch, st in zip(new_text, styles):
        if id(st) not in fonts:
            fonts[id(st)] = resolve_font(src, template.page, st, by_span[id(st)], fonts_cache)
    x_first = template.text_x0
    x_cont = template.cont_x0 if template.cont_x0 is not None else template.text_x0
    lines = _layout_lines(new_text, styles, fonts, right - x_first, right - x_cont)
    n_new = len(lines)

    limit = bottom_limit(layout)
    page_blocks = _page_blocks(layout, pno, blk.col)

    if op["mode"] == "replace":
        first_baseline = blk.lines[0].baseline
        prev = [b for b in page_blocks if b.bbox[3] <= blk.bbox[1] + 0.5 and b is not blk]
        nxt = [b for b in page_blocks if b.bbox[1] >= blk.bbox[3] - 0.5 and b is not blk]
        cut_top = (max(b.bbox[3] for b in prev) + blk.bbox[1]) / 2 if prev else blk.bbox[1] - 1
        cut_top = max(cut_top, blk.bbox[1] - pitch * 0.5)
        cut_bottom = (min(b.bbox[1] for b in nxt) + blk.bbox[3]) / 2 if nxt else blk.bbox[3] + 1
        delta = (n_new - len(blk.lines)) * pitch
        keep = []
        if blk.bullet_span is not None:
            bx0, by0, bx1, by1 = blk.bullet_span["bbox"]
            if blk.bullet_span["text"].strip()[1:].strip():
                bx1 = bx0 + fitz.Font("helv").text_length("•", blk.bullet_span["size"]) + 0.5
            keep.append((bx0 - 0.5, max(cut_top, by0 - 0.5), bx1 + 0.3, min(cut_bottom, by1 + 0.5)))
        dy_bullet = 0.0
    else:
        anchor = blk
        nxt = [b for b in page_blocks if b.bbox[1] >= anchor.bbox[3] - 0.5 and b is not anchor]
        same_kind_next = nxt[0] if nxt and (nxt[0].bullet_span is not None) == (template.bullet_span is not None) \
            and not _looks_heading(nxt[0]) else None
        if same_kind_next is not None:
            gap = same_kind_next.lines[0].baseline - anchor.lines[-1].baseline
        else:
            prevs = [b for b in page_blocks if b.bbox[3] <= anchor.bbox[1] + 0.5 and b is not anchor
                     and (b.bullet_span is not None) == (template.bullet_span is not None)]
            gap = anchor.lines[0].baseline - prevs[-1].lines[-1].baseline if prevs else pitch
            if not (0.8 * pitch <= gap <= 2.5 * pitch):
                gap = pitch
        first_baseline = anchor.lines[-1].baseline + gap
        cut_top = cut_bottom = (anchor.bbox[3] + min(b.bbox[1] for b in nxt)) / 2 if nxt else anchor.bbox[3] + 1
        delta = gap + (n_new - 1) * pitch
        keep = []
        dy_bullet = first_baseline - template.lines[0].baseline

    carry = _plan_column_shift(layout, plan, pno, pno, blk.col, cut_top, cut_bottom, delta, limit, keep)
    # bullet glyph for inserted paragraph: copy the template's glyph as vector content
    if op["mode"] == "insert_after" and template.bullet_span is not None:
        bx0, by0, bx1, by1 = template.bullet_span["bbox"]
        if template.bullet_span["text"].strip()[1:].strip():
            bx1 = bx0 + fitz.Font("helv").text_length("•", template.bullet_span["size"]) + 0.5
        plan.placements.append(Placement(template.page, fitz.Rect(bx0 - 0.5, by0 - 0.5, bx1 + 0.3, by1 + 0.5),
                                         dy_bullet, pno))
    # new text
    for k, line in enumerate(lines):
        x = x_first if k == 0 else x_cont
        for seg, st in line:
            plan.texts.append({"page": pno, "x": x, "baseline": first_baseline + k * pitch, "text": seg,
                               "span": st, "font": fonts[id(st)]})
            x += fonts[id(st)].font.text_length(seg, fontsize=st["size"])
    plan.moved_pages.add(pno)

    # cascade overflow onto following pages
    dst = pno
    n_src = len(layout.pages)
    while carry is not None:
        src_p, rect, first_top, last_bottom, gap_before = carry
        dst += 1
        h = last_bottom - first_top
        if dst < n_src:
            col = blk.col if layout.pages[dst].gutter is not None else "F"
            col_blocks = _page_blocks(layout, dst, col)
            top = min((b.bbox[1] for b in col_blocks), default=layout.pages[dst].content_top)
            dy = top - first_top
            plan.placements.append(Placement(src_p, rect, dy + (rect.y0 - rect.y0), dst))
            shift = h + max(gap_before, pitch * 0.5)
            carry = _plan_column_shift(layout, plan, dst, dst, col, top - 0.5, top - 0.5, shift, limit)
            plan.moved_pages.add(dst)
        else:
            top = min(p.content_top for p in layout.pages[1:]) if n_src > 1 else layout.pages[0].content_top
            plan.placements.append(Placement(src_p, rect, top - first_top, dst))
            plan.new_pages += 1
            carry = None
    geom = {"page": pno, "first_baseline": first_baseline, "lines": n_new, "pitch": pitch, "delta": delta,
            "cut_top": cut_top, "cut_bottom": cut_bottom, "column": blk.col, "column_range": (cx0, cx1),
            "fonts": {f.source_font: {"status": f.status, "note": f.note, "alias": f.alias} for f in fonts.values()},
            "missing_glyphs": fonts_cache.get("_missing", {})}
    return plan, geom


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def _region_source(src, layout: PdfLayout, pno: int, rect: fitz.Rect, cache: dict):
    """Single-page copy of page `pno` with every text span outside `rect` redacted."""
    key = (pno, tuple(round(v, 2) for v in rect))
    if key in cache:
        return cache[key]
    tmp = fitz.open()
    tmp.insert_pdf(src, from_page=pno, to_page=pno)
    page = tmp[0]
    n = 0
    for span, baseline in _all_spans(layout, pno):
        if span["font"] == "OCR":
            continue
        if not _in_rect(_span_point(span, baseline), rect):
            x0, _, x1, _ = span["bbox"]
            sz = span["size"]
            page.add_redact_annot(fitz.Rect(x0 + 0.3, baseline - 0.55 * sz, x1 - 0.3, baseline - 0.15 * sz))
            n += 1
    if n:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
    cache[key] = tmp
    return tmp


def execute_plan(src, layout: PdfLayout, plan: Plan) -> bytes:
    out = fitz.open()
    n_src = len(src)
    n_out = n_src + plan.new_pages
    links_by_page = {p: src[p].get_links() for p in range(n_src)}
    region_cache: dict = {}
    for dst in range(n_out):
        if dst < n_src and dst not in plan.moved_pages:
            out.insert_pdf(src, from_page=dst, to_page=dst)
            continue
        ref = src[min(dst, n_src - 1)]
        page = out.new_page(width=ref.rect.width, height=ref.rect.height)
        for pl in [p for p in plan.placements if p.dst_page == dst]:
            clip = pl.clip & src[pl.src_page].rect
            if clip.is_empty or clip.height < 0.5 or clip.width < 0.5:
                continue
            region = _region_source(src, layout, pl.src_page, clip, region_cache)
            target = fitz.Rect(clip.x0, clip.y0 + pl.dy, clip.x1, clip.y1 + pl.dy)
            page.show_pdf_page(target, region, 0, clip=clip, keep_proportion=False)
            for link in links_by_page.get(pl.src_page, []):
                r = link["from"]
                if _in_rect(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2), clip):
                    nl = {k: v for k, v in link.items() if k not in ("xref", "id")}
                    nl["from"] = fitz.Rect(r.x0, r.y0 + pl.dy, r.x1, r.y1 + pl.dy)
                    page.insert_link(nl)
        inserted = set()
        for t in [t for t in plan.texts if t["page"] == dst]:
            fc: FontChoice = t["font"]
            if fc.buffer is not None and fc.alias not in inserted:
                page.insert_font(fontname=fc.alias, fontbuffer=fc.buffer)
                inserted.add(fc.alias)
            page.insert_text((t["x"], t["baseline"]), t["text"], fontname=fc.alias, fontsize=t["span"]["size"],
                             color=_rgb(t["span"]["color"]))
    out.set_metadata(src.metadata)
    toc = src.get_toc()
    if toc:
        out.set_toc(toc)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    for d in region_cache.values():
        d.close()
    return data


def apply_pdf_edit(data: bytes, layout: PdfLayout, op: dict) -> tuple[bytes, dict]:
    src = fitz.open(stream=data, filetype="pdf")
    try:
        blk = layout.blocks.get(op["block_id"])
        if blk is None:
            raise PdfEditError("Target block not found in PDF layout")
        if op.get("expected_text") is not None:
            cur, _ = _block_char_spans(blk)
            exp = op["expected_text"]
            if tu.normalize_ws(cur) != tu.normalize_ws(exp):
                raise PdfEditError("The target text changed since the recommendation was generated")
        layout.source_doc = src
        try:
            plan, geom = build_plan(src, layout, op, {})
        finally:
            layout.source_doc = None
        out = execute_plan(src, layout, plan)
        geom.update(notes=plan.notes, pagination_changed=plan.pagination_changed, new_pages=plan.new_pages,
                    moved_pages=sorted(plan.moved_pages), scanned=layout.scanned,
                    page_annotations=[p for p in sorted(plan.moved_pages) if p < len(src) and
                                      any(a.type[0] != fitz.PDF_ANNOT_LINK for a in src[p].annots())])
        geom["placements"] = [{"src": p.src_page, "dst": p.dst_page, "clip": tuple(round(v, 2) for v in p.clip),
                               "dy": round(p.dy, 2)} for p in plan.placements]
        return out, geom
    finally:
        src.close()


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------
def _spans_of(doc) -> list[list[dict]]:
    out = []
    for page in doc:
        spans = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                for s in l["spans"]:
                    if s["text"].strip():
                        spans.append(s)
        out.append(spans)
    return out


def verify_pdf_edit(before: bytes, after: bytes, layout: PdfLayout, op: dict, geom: dict) -> dict:
    report: list[dict] = []

    def rec(prop, status, detail=""):
        report.append({"property": prop, "status": status, "detail": detail})

    a = fitz.open(stream=after, filetype="pdf")
    b = fitz.open(stream=before, filetype="pdf")
    try:
        rec("Valid PDF", "preserved", f"re-opened: {len(a)} page(s)")
        sizes_ok = all(abs(a[i].rect.width - b[min(i, len(b) - 1)].rect.width) < 0.01 and
                       abs(a[i].rect.height - b[min(i, len(b) - 1)].rect.height) < 0.01 for i in range(len(a)))
        rec("Page dimensions", "preserved" if sizes_ok else "changed",
            f"{b[0].rect.width:.1f} x {b[0].rect.height:.1f} pt")
        if geom["pagination_changed"]:
            rec("Pagination", "changed", f"content after the edit flowed to the next page; "
                f"{len(b)} -> {len(a)} page(s)")
        else:
            rec("Pagination", "preserved", f"{len(a)} page(s), no content moved across pages")
        fonts = geom["fonts"]
        for name, f in fonts.items():
            status = {"exact": "preserved", "family": "approximated", "substituted": "substituted"}[f["status"]]
            rec(f"Font for new text ({name})", status, f["note"])
        for note in geom.get("notes", []):
            rec("Background graphics", "approximated", note)
        if layout.scanned:
            rec("Scanned page reconstruction", "approximated",
                "page image regions preserved; new text drawn with a standard font sized from OCR, "
                "original typeface cannot be reproduced exactly")

        # Unaffected spans: same text/font/size/colour at the expected (possibly shifted) position.
        target = layout.blocks[op["block_id"]]
        removed = set()
        if op["mode"] == "replace":
            removed = {id(s) for l in target.lines for s in l.spans if s is not target.bullet_span}
        after_spans = _spans_of(a)
        checked = matched = 0
        mismatches = []
        placements = geom["placements"]
        for pno in range(len(b)):
            for span, baseline in _all_spans(layout, pno):
                if id(span) in removed or span["font"] == "OCR" or not span["text"].strip():
                    continue
                pt = _span_point(span, baseline)
                pl = next((p for p in placements if p["src"] == pno and
                           _in_rect(pt, fitz.Rect(p["clip"]))), None)
                if pl is None:
                    if pno in geom["moved_pages"]:
                        mismatches.append(span["text"])
                        checked += 1
                        continue
                    dst, dy = pno, 0.0
                else:
                    dst, dy = pl["dst"], pl["dy"]
                checked += 1
                ox, oy = span["origin"]
                # An unnamed embedded font (no BaseFont) is reported as "" before and as a generic name
                # after re-embedding; compare such spans on text, size, colour and position only.
                ok = any(s["text"].strip() == span["text"].strip() and
                         (s["font"] == span["font"] or not span["font"] or not s["font"]) and
                         abs(s["size"] - span["size"]) < 0.05 and s["color"] == span["color"] and
                         abs(s["origin"][0] - ox) < 0.6 and abs(s["origin"][1] - (oy + dy)) < 0.6
                         for s in after_spans[dst]) if dst < len(after_spans) else False
                if ok:
                    matched += 1
                elif len(mismatches) < 5:
                    mismatches.append(span["text"])
        rec("Unaffected text: fonts, sizes, colours, positions", "preserved" if matched == checked else "changed",
            f"{matched}/{checked} original text spans found unchanged at their expected position"
            + (f"; e.g. {mismatches[:3]}" if mismatches else ""))

        # Duplicate / hidden text check with an independent extractor (pdfminer).
        try:
            from io import BytesIO
            from pdfminer.high_level import extract_text
            alnum = lambda x: re.sub(r"[^A-Za-z0-9]", "", x)
            txt_b = alnum(extract_text(BytesIO(before)))
            txt_a = alnum(extract_text(BytesIO(after)))
            dups = []
            for pno in range(len(b)):
                for bl in layout.blocks.values():
                    if bl.page != pno or bl is target and op["mode"] == "replace":
                        continue
                    for l in bl.lines:
                        for sp in l.spans:
                            t = alnum(sp["text"])
                            if len(t) >= 12 and txt_a.count(t) > txt_b.count(t):
                                dups.append(sp["text"])
            old_line = alnum(target.lines[0].text)
            hidden_old = op["mode"] == "replace" and len(old_line) >= 12 and \
                old_line not in alnum(op["text"]) and txt_b.count(old_line) > 0 and \
                txt_a.count(old_line) >= txt_b.count(old_line)
            ok = not dups and not hidden_old
            rec("No hidden or duplicated text (pdfminer)", "preserved" if ok else "changed",
                "independent extractor sees each line once and no removed text" if ok else
                f"duplicates: {dups[:3]} hidden_old={hidden_old}")
        except Exception as exc:  # pragma: no cover
            rec("No hidden or duplicated text (pdfminer)", "not_checked", str(exc))

        links_b = sum(len(p.get_links()) for p in b)
        links_a = sum(len(p.get_links()) for p in a)
        rec("Hyperlinks", "preserved" if links_a == links_b else "changed", f"{links_a}/{links_b} link(s)")
        imgs_b = sum(len(p.get_images()) for p in b)
        rec("Images", "preserved", f"{imgs_b} image(s) drawn from the original page content")
        if geom.get("page_annotations"):
            rec("Non-link annotations on edited pages", "changed", "comments/annotations are not carried over")
        rec("Metadata", "preserved" if a.metadata.get("title") == b.metadata.get("title") else "changed",
            "document info copied")
        new_ok = tu.normalize_ws(op["text"]).replace(" ", "") in tu.normalize_ws(a[geom["page"]].get_text()).replace(" ", "").replace("\n", "")
        rec("Edited text written", "preserved" if new_ok else "changed", "new text is extractable from the page")
    finally:
        a.close(); b.close()
    exact = all(r["status"] == "preserved" for r in report)
    return {"format": "pdf", "method": "scanned-page reconstruction" if layout.scanned else
            "region-preserving targeted edit", "exact": exact, "properties": report,
            "sha256": hashlib.sha256(after).hexdigest()}
