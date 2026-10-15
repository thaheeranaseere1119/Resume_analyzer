"""PDF reading: text-layer extraction with layout metadata, or OCR for scans.

Produces the shared ResumeModel plus a PdfLayout holding, for every logical
block, its lines, spans (font, size, colour, flags, baseline) and column so
the editor can make targeted, position-accurate modifications.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import pymupdf as fitz

from . import textutil as tu
from .model import RawItem, ResumeModel, build_model

OCR_DPI = 300


def _tess_config() -> str:
    import os
    import shutil
    cands = [os.environ.get("TESSDATA_PREFIX", "")]
    exe = shutil.which("tesseract")
    if exe:
        root = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
        cands += [os.path.join(root, "share", "tessdata"), os.path.join(root, "share", "tesseract-ocr", "5", "tessdata")]
    cands += ["/opt/homebrew/share/tessdata", "/usr/local/share/tessdata", "/usr/share/tesseract-ocr/5/tessdata",
              "/usr/share/tesseract-ocr/4.00/tessdata"]
    for c in cands:
        if c and os.path.exists(os.path.join(c, "eng.traineddata")):
            return f"--tessdata-dir {c}"
    return ""


@dataclass
class PLine:
    page: int
    bbox: tuple
    baseline: float
    spans: list
    col: str = "F"

    @property
    def text(self) -> str:
        return "".join(s["text"] for s in self.spans)

    @property
    def size(self) -> float:
        sizes = [s["size"] for s in self.spans if s["text"].strip()]
        return max(set(sizes), key=sizes.count) if sizes else 10.0


@dataclass
class PBlock:
    id: str
    page: int
    lines: list
    col: str
    col_bounds: tuple
    bullet_span: dict | None = None
    text_x0: float = 0.0
    cont_x0: float | None = None

    @property
    def bbox(self):
        return (min(l.bbox[0] for l in self.lines), min(l.bbox[1] for l in self.lines),
                max(l.bbox[2] for l in self.lines), max(l.bbox[3] for l in self.lines))

    def body_spans(self):
        out = []
        for i, l in enumerate(self.lines):
            for s in l.spans:
                if i == 0 and self.bullet_span is not None and s is self.bullet_span:
                    continue
                out.append(s)
        return out


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    gutter: float | None
    content_top: float
    content_bottom: float
    furniture: list = field(default_factory=list)  # header/footer lines (never moved)
    scanned: bool = False


@dataclass
class PdfLayout:
    pages: list[PageInfo]
    blocks: dict[str, PBlock]
    order: list[str]
    scanned: bool = False
    ocr_confidence: float | None = None
    fonts: dict = field(default_factory=dict)


def _line_bold(line) -> bool:
    spans = [s for s in line.spans if s["text"].strip() and not s.get("synthetic")]
    return bool(spans) and all(_is_bold(s) for s in spans)


def _is_bold(span) -> bool:
    return bool(span["flags"] & 16) or bool(re.search(r"bold|black|heavy|semibold", span["font"], re.I))


def _raw_lines_text(page, pno) -> list[PLine]:
    d = page.get_text("dict")
    lines = []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            spans = [dict(s) for s in l["spans"] if s["text"] != ""]
            if not any(s["text"].strip() for s in spans):
                continue
            # trim pure-whitespace edge spans so bboxes are tight
            while spans and not spans[-1]["text"].strip():
                spans.pop()
            while spans and not spans[0]["text"].strip():
                spans.pop(0)
            bbox = (min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                    max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans))
            baseline = max(set(round(s["origin"][1], 1) for s in spans),
                           key=[round(s["origin"][1], 1) for s in spans].count)
            lines.append(PLine(pno, bbox, baseline, spans))
    return lines


def _ocr_lines(page, pno) -> tuple[list[PLine], list[float]]:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=OCR_DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config=_tess_config())
    scale = 72.0 / OCR_DPI
    groups: dict = {}
    confs = []
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        confs.append(conf)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x0, y0 = data["left"][i] * scale, data["top"][i] * scale
        x1, y1 = x0 + data["width"][i] * scale, y0 + data["height"][i] * scale
        groups.setdefault(key, []).append((x0, y0, x1, y1, word, conf))
    lines = []
    for words in groups.values():
        words.sort()
        y0 = min(w[1] for w in words); y1 = max(w[3] for w in words)
        h = y1 - y0
        spans = []
        for x0, wy0, x1, wy1, word, conf in words:
            spans.append({"text": word + " ", "font": "OCR", "size": round(h * 0.95, 1), "flags": 0, "color": 0,
                          "bbox": (x0, y0, x1, y1), "origin": (x0, y1 - h * 0.2), "ocr_conf": conf})
        spans[-1]["text"] = spans[-1]["text"].rstrip()
        lines.append(PLine(pno, (words[0][0], y0, max(w[2] for w in words), y1), y1 - h * 0.2, spans))
    # OCR often reads bullets as 'e', '«', '©' or '*'
    for l in lines:
        first = l.spans[0]["text"].strip()
        if len(l.spans) > 1 and len(first) == 1 and (not first.isalnum() or first in "eo") and \
                l.spans[1]["text"][:1].isupper():
            l.spans[0]["text"] = "• "
            l.spans[0]["ocr_bullet"] = True
    return lines, confs


def _find_gutter(lines: list[PLine], width: float) -> float | None:
    if len(lines) < 8:
        return None
    best, best_w = None, 0.0
    x = width * 0.2
    while x < width * 0.8:
        crossing = [l for l in lines if l.bbox[0] < x < l.bbox[2]]
        left = [l for l in lines if l.bbox[2] <= x]
        right = [l for l in lines if l.bbox[0] >= x]
        if len(left) >= 3 and len(right) >= 3 and len(crossing) <= 0.15 * len(lines):
            right_top = min(l.bbox[1] for l in right)
            if all(l.bbox[3] <= right_top + 2 for l in crossing):
                # widen the band
                lo = max([l.bbox[2] for l in left if l.bbox[2] <= x] + [0])
                hi = min([l.bbox[0] for l in right if l.bbox[0] >= x] + [width])
                if hi - lo > best_w:
                    best, best_w = (lo + hi) / 2, hi - lo
        x += 2
    return best if best_w >= 8 else None


def _merge_rows(lines: list[PLine]) -> list[PLine]:
    """Merge fragments that share a baseline in the same column (e.g. bullet glyph + text, right-aligned dates)."""
    lines = sorted(lines, key=lambda l: (l.col, round(l.baseline), l.bbox[0]))
    out: list[PLine] = []
    for l in lines:
        if out and out[-1].col == l.col and abs(out[-1].baseline - l.baseline) < 1.5 and l.bbox[0] >= out[-1].bbox[2] - 1:
            prev = out[-1]
            gap = l.bbox[0] - prev.bbox[2]
            if gap > 2 * prev.size:
                sep = {"text": "\t", "font": l.spans[0]["font"], "size": l.spans[0]["size"], "flags": 0,
                       "color": 0, "bbox": (prev.bbox[2], prev.bbox[1], l.bbox[0], prev.bbox[3]),
                       "origin": (prev.bbox[2], prev.baseline), "synthetic": True}
                prev.spans.append(sep)
            prev.spans.extend(l.spans)
            prev.bbox = (prev.bbox[0], min(prev.bbox[1], l.bbox[1]), l.bbox[2], max(prev.bbox[3], l.bbox[3]))
        else:
            out.append(l)
    return out


def _bullet_span(line: PLine):
    first = line.spans[0]
    t = first["text"].strip()
    if t and t[0] in tu.BULLET_CHARS and (len(t) == 1 or t[1:2] == " "):
        return first
    return None


def read_pdf(data: bytes, as_of: date) -> tuple[ResumeModel, PdfLayout]:
    doc = fitz.open(stream=data, filetype="pdf")
    pages: list[PageInfo] = []
    all_lines: list[list[PLine]] = []
    scanned_any, confs = False, []
    for pno, page in enumerate(doc):
        text = page.get_text().strip()
        big_image = any(fitz.Rect(i["bbox"]).get_area() > 0.5 * page.rect.get_area()
                        for i in page.get_image_info())
        # An image-only page, or a page dominated by one image with little text
        # (e.g. a previously edited scan), is read with OCR.
        scanned = (len(text) < 20 and bool(page.get_images())) or (big_image and len(text) < 400)
        if scanned:
            lines, c = _ocr_lines(page, pno)
            confs += c
            scanned_any = True
        else:
            lines = _raw_lines_text(page, pno)
        W, H = page.rect.width, page.rect.height
        # header/footer furniture: tiny or page-number lines in the outer 5% bands
        furniture = [l for l in lines if (l.bbox[1] > H * 0.95 or l.bbox[3] < H * 0.04)
                     or (l.bbox[1] > H * 0.92 and re.fullmatch(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", l.text.strip(), re.I))]
        content = [l for l in lines if l not in furniture]
        gutter = _find_gutter(content, W)
        for l in content:
            if gutter is None or (l.bbox[0] < gutter < l.bbox[2]):
                l.col = "F"
            else:
                l.col = "L" if l.bbox[2] <= gutter else "R"
        content = _merge_rows(content)
        top = min((l.bbox[1] for l in content), default=0)
        bottom = max((l.bbox[3] for l in content), default=H)
        pages.append(PageInfo(pno, W, H, gutter, top, bottom, furniture, scanned))
        all_lines.append(content)

    blocks: dict[str, PBlock] = {}
    order: list[str] = []
    items: list[RawItem] = []
    for pno, lines in enumerate(all_lines):
        info = pages[pno]
        right_top = min((l.bbox[1] for l in lines if l.col == "R"), default=info.height)
        full_top = [l for l in lines if l.col == "F" and l.bbox[1] < right_top]
        full_rest = [l for l in lines if l.col == "F" and l.bbox[1] >= right_top]
        seq = sorted(full_top, key=lambda l: l.baseline) + \
            sorted([l for l in lines if l.col == "L"], key=lambda l: l.baseline) + \
            sorted([l for l in lines if l.col == "R"], key=lambda l: l.baseline) + \
            sorted(full_rest, key=lambda l: l.baseline)
        col_x = {}
        for c in ("L", "R", "F"):
            cl = [l for l in lines if l.col == c]
            if cl:
                lo = min(l.bbox[0] for l in cl)
                hi = max(l.bbox[2] for l in cl)
                if c == "L" and info.gutter:
                    hi = max(hi, info.gutter - 4)
                col_x[c] = (lo, hi)
        cur: PBlock | None = None
        for l in seq:
            bspan = _bullet_span(l)
            start_new = True
            if cur is not None and cur.col == l.col and bspan is None:
                prev = cur.lines[-1]
                gap = l.bbox[1] - prev.bbox[3]
                same_size = abs(l.size - prev.size) < 0.6
                if cur.bullet_span is not None:
                    aligned = abs(l.bbox[0] - (cur.cont_x0 or cur.text_x0)) < 3 or l.bbox[0] > cur.bullet_span["bbox"][0] + 2
                else:
                    aligned = abs(l.bbox[0] - cur.lines[0].bbox[0]) < 3 or abs(l.bbox[0] - (cur.cont_x0 or -99)) < 3
                lo, hi = col_x.get(l.col, (0, info.width))
                prev_full = prev.bbox[2] >= lo + 0.7 * (hi - lo)
                # Lines with a right-aligned element (date, location) are self-contained rows,
                # and a change between bold and regular text starts a new block.
                has_tab = any(s.get("synthetic") for s in prev.spans + l.spans)
                bold_change = _line_bold(prev) != _line_bold(l)
                if same_size and aligned and gap < 0.6 * l.size and prev_full and not has_tab and not bold_change and \
                        tu.classify_heading(prev.text) is None and not tu.DATE_RANGE_RE.search(prev.text):
                    start_new = False
                # A wrapped sentence: the line starts in lower case and the previous line has no
                # closing punctuation. Always a continuation, whatever its indent.
                first_char = l.text.strip()[:1]
                if start_new and same_size and gap < 0.8 * l.size and first_char.islower() and not has_tab \
                        and not re.search(r"[.!?:]\s*$", prev.text.strip()):
                    start_new = False
            if start_new:
                bid = f"pg{pno}-b{len([k for k in blocks if k.startswith(f'pg{pno}-')])}"
                tx0 = l.bbox[0]
                if bspan is not None:
                    after = [s for s in l.spans if s is not bspan and s["text"].strip()]
                    tx0 = after[0]["bbox"][0] if after else bspan["bbox"][2] + 3
                cur = PBlock(bid, pno, [l], l.col, col_x.get(l.col, (0, info.width)), bspan, tx0)
                blocks[bid] = cur
                order.append(bid)
            else:
                cur.lines.append(l)
                if cur.cont_x0 is None:
                    cur.cont_x0 = l.bbox[0]

    for bid in order:
        b = blocks[bid]
        parts = []
        for i, l in enumerate(b.lines):
            t = "".join(s["text"] for s in l.spans if not (i == 0 and s is b.bullet_span))
            parts.append(t.strip())
        text = " ".join(parts)
        if b.bullet_span is not None:
            raw = b.bullet_span["text"].strip()
            text = (raw[1:].strip() + " " + text).strip() if len(raw) > 1 else text
        body = [s for s in b.body_spans() if s["text"].strip() and not s.get("synthetic")]
        sizes = [s["size"] for s in body] or [10]
        items.append(RawItem(
            id=bid, text=text, is_bullet=b.bullet_span is not None,
            bullet_glyph=(b.bullet_span["text"].strip()[:1] if b.bullet_span is not None else ""),
            is_bold=bool(body) and all(_is_bold(s) for s in body), font_size=max(set(sizes), key=sizes.count),
            page=b.page, style={"font": body[0]["font"] if body else "", "size": sizes[0]},
        ))
    layout = PdfLayout(pages, blocks, order, scanned_any, round(sum(confs) / len(confs), 1) if confs else None)
    model = build_model(items, "pdf", as_of, page_count=len(doc), scanned=scanned_any,
                        ocr_confidence=layout.ocr_confidence,
                        meta={"columns": 2 if any(p.gutter for p in pages) else 1, "pages": len(doc)})
    doc.close()
    return model, layout
