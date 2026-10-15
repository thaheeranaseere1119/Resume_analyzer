"""PDF: layout preservation, honest reconstruction warnings, font inheritance, validity."""
from io import BytesIO

import pymupdf as fitz
import pytest
from pdfminer.high_level import extract_text

from careercopilot.edit_pdf import apply_pdf_edit, verify_pdf_edit
from careercopilot.pdf_io import read_pdf

from conftest import AS_OF, fixture_bytes


def _edit(name, mode, contains, text):
    data = fixture_bytes(name)
    model, layout = read_pdf(data, AS_OF)
    b = next(x for x in model.blocks if x.kind == "bullet" and contains in x.text)
    op = {"mode": mode, "block_id": b.id, "text": text}
    out, geom = apply_pdf_edit(data, layout, op)
    report = verify_pdf_edit(data, out, layout, op, geom)
    props = {p["property"]: p for p in report["properties"]}
    return data, out, model, b, geom, report, props


def test_pdf_has_text_layer_detection():
    m, _ = read_pdf(fixture_bytes("single_column.pdf"), AS_OF)
    assert not m.scanned and m.page_count == 1
    m, _ = read_pdf(fixture_bytes("scanned.pdf"), AS_OF)
    assert m.scanned and m.ocr_confidence and m.ocr_confidence > 60


def test_single_column_replace_is_exact():
    new = "Containerized three legacy services with Docker and migrated them to the cloud."
    data, out, model, b, geom, report, props = _edit("single_column.pdf", "replace", "Containerized", new)
    assert report["exact"], [p for p in report["properties"] if p["status"] != "preserved"]
    assert geom["fonts"]["Helvetica"]["status"] == "exact"
    a, o = fitz.open(stream=data), fitz.open(stream=out)
    assert len(a) == len(o) and a[0].rect == o[0].rect
    assert len(o[0].get_links()) == len(a[0].get_links()) == 1


def test_insert_inherits_font_size_colour_and_indent():
    text = "Deployed containerized services with Docker to AWS."
    data, out, model, b, geom, report, props = _edit("single_column.pdf", "insert_after", "Containerized", text)
    assert report["exact"]
    m2, l2 = read_pdf(out, AS_OF)
    ids = [x.id for x in m2.blocks]
    anchor_idx = next(i for i, x in enumerate(m2.blocks) if "Containerized three" in x.text)
    new = m2.blocks[anchor_idx + 1]
    assert new.body == text and new.kind == "bullet"
    assert new.section == b.section and new.entry_id == b.entry_id
    old_anchor = l2.blocks[m2.blocks[anchor_idx].id]
    new_layout = l2.blocks[new.id]
    span_old = old_anchor.body_spans()[0]
    span_new = new_layout.body_spans()[0]
    assert span_new["font"] == span_old["font"]
    assert abs(span_new["size"] - span_old["size"]) < 0.01
    assert span_new["color"] == span_old["color"]
    assert abs(new_layout.text_x0 - old_anchor.text_x0) < 0.6
    assert new_layout.bullet_span is not None


def test_two_column_edit_keeps_other_column_and_reports_font_approximation():
    new = "Containerized three legacy services with Docker and migrated them to the cloud."
    data, out, model, b, geom, report, props = _edit("two_column.pdf", "replace", "Containerized", new)
    assert props["Unaffected text: fonts, sizes, colours, positions"]["status"] == "preserved"
    assert props["No hidden or duplicated text (pdfminer)"]["status"] == "preserved"
    # The embedded subset lacks glyphs for new words; must be reported, never claimed exact.
    font_props = [p for k, p in props.items() if k.startswith("Font for new text")]
    assert font_props and all(p["status"] in ("preserved", "approximated", "substituted") for p in font_props)
    if any(p["status"] != "preserved" for p in font_props):
        assert not report["exact"]
    # Left column (skills/education) spans are untouched.
    a, o = fitz.open(stream=data)[0], fitz.open(stream=out)[0]
    left = lambda p: sorted((round(s["origin"][0], 1), round(s["origin"][1], 1), s["text"])
                            for blk in p.get_text("dict")["blocks"] for l in blk.get("lines", [])
                            for s in l["spans"] if s["origin"][0] < 180 and s["text"].strip())
    assert left(a) == left(o)


def test_overflow_moves_content_to_next_page_and_reports_it():
    text = ("Deployed containerized services with Docker to AWS, cutting release time by half across all product "
            "teams and standardizing the build process for every service.")
    data, out, model, b, geom, report, props = _edit("tight_page.pdf", "insert_after", "Containerized", text)
    assert geom["pagination_changed"]
    assert props["Pagination"]["status"] == "changed"
    assert not report["exact"]
    assert len(fitz.open(stream=out)) == 2
    # Nothing lost: every original line is still extractable exactly once.
    m2, _ = read_pdf(out, AS_OF)
    assert [x.text for x in m2.blocks if x.kind == "heading"] == [x.text for x in model.blocks if x.kind == "heading"]
    assert props["Unaffected text: fonts, sizes, colours, positions"]["status"] == "preserved"


def test_scanned_pdf_reconstruction_is_flagged():
    new = "Containerized three legacy services with Docker and migrated them to the cloud."
    data, out, model, b, geom, report, props = _edit("scanned.pdf", "replace", "Containerized", new)
    assert not report["exact"]
    assert props["Scanned page reconstruction"]["status"] == "approximated"
    assert any(k.startswith("Font for new text") and p["status"] == "substituted" for k, p in props.items())
    m2, _ = read_pdf(out, AS_OF)
    assert m2.scanned  # still treated as a scan; re-read with OCR
    assert any("Docker" in x.text for x in m2.blocks)


@pytest.mark.parametrize("name", ["single_column.pdf", "two_column.pdf", "tight_page.pdf"])
def test_no_hidden_or_duplicate_text_for_other_parsers(name):
    data, out, model, b, geom, report, props = _edit(name, "replace", "Containerized",
                                                      "Containerized legacy services with Docker.")
    old = "Containerized three legacy services and migrated"
    before = extract_text(BytesIO(data)).replace("\n", " ")
    after = extract_text(BytesIO(out)).replace("\n", " ")
    # Exactly the edited occurrence disappeared (the tight fixture repeats bullets on purpose).
    assert after.count(old) == before.count(old) - 1
    assert after.count("Wrote automated tests") == before.count("Wrote automated tests")


def test_repeated_edits_stay_readable():
    """Second edit must not reuse an earlier edit's font resource name (regression)."""
    data = fixture_bytes("two_column.pdf")
    m, l = read_pdf(data, AS_OF)
    b = next(x for x in m.blocks if "Containerized" in x.text)
    out1, _ = apply_pdf_edit(data, l, {"mode": "replace", "block_id": b.id,
                                       "text": "Containerized three legacy services with Docker and migrated them to the cloud."})
    m1, l1 = read_pdf(out1, AS_OF)
    b1 = next(x for x in m1.blocks if "Docker" in x.text)
    text = "Built a Java Spring Boot service for inventory sync with the warehouse API."
    out2, _ = apply_pdf_edit(out1, l1, {"mode": "insert_after", "block_id": b1.id, "text": text})
    m2, _ = read_pdf(out2, AS_OF)
    assert any(x.body == text for x in m2.blocks)
