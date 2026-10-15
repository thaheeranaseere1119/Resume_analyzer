"""Generate test resumes with different layouts, fonts, bullet styles,
tables, columns, hyperlinks, headers/footers and page breaks.

Run:  python tests/fixtures/build_fixtures.py
"""
from __future__ import annotations

import io
import os
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

HERE = Path(__file__).parent

JD = """Senior Backend Engineer

Responsibilities:
- Design and build scalable REST APIs and backend services in Python.
- Deploy containerized services with Docker to AWS.
- Maintain CI/CD pipelines and automated testing.
- Mentor junior engineers and review code.

Requirements:
- 3+ years of professional software engineering experience.
- Strong Python and SQL skills.
- Experience with Docker and REST API design.
- Experience with Java.
- Bachelor's degree in Computer Science or related field.

Nice to have:
- Kubernetes experience is a plus.
- React for internal tools is a plus.
- AWS Certified Developer is a plus.
"""

CONTACT = "jordan.lee@example.com | (555) 010-2030 | github.com/jordanlee"
SUMMARY = ("Backend engineer with four years of experience building data-heavy web services in Python "
           "and shipping reliable APIs for e-commerce teams.")
EXPERIENCE = [
    ("Software Engineer, Acme Commerce", "Jan 2022 – Present", [
        "Built order-processing services in Python and Flask that handle 2M requests per day.",
        "Designed PostgreSQL schemas and optimized slow reports, cutting query time by 40%.",
        "Containerized three legacy services and migrated them to the cloud.",
    ]),
    ("Junior Developer, Brightside Labs", "Jun 2020 – Dec 2021", [
        "Developed internal dashboards in React and exposed API endpoints for the analytics team.",
        "Wrote automated tests that raised coverage from 45% to 80%.",
    ]),
]
PROJECTS = [("Inventory Tracker", [
    "Created a Flask web app with PostgreSQL storage for tracking warehouse stock levels.",
])]
SKILLS = [("Languages", "Python, Java, JavaScript"), ("Frameworks", "Flask, React"),
          ("Tools", "Git, PostgreSQL, Linux")]
EDUCATION = ("B.S. in Computer Science, State University", "2016 – 2020")


# ---------------------------------------------------------------------------
# DOCX helpers
# ---------------------------------------------------------------------------
def add_hyperlink(paragraph, url, text, color="1F4E79"):
    part = paragraph.part
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    c = OxmlElement("w:color"); c.set(qn("w:val"), color); rpr.append(c)
    u = OxmlElement("w:u"); u.set(qn("w:val"), "single"); rpr.append(u)
    run.append(rpr)
    t = OxmlElement("w:t"); t.text = text; run.append(t)
    link.append(run)
    paragraph._p.append(link)


def heading(doc, text, color=RGBColor(0x8A, 0x3B, 0x3B), size=13, underline=False):
    p = doc.add_paragraph(style="Heading 1")
    r = p.add_run(text.upper())
    r.font.size, r.font.color.rgb, r.underline = Pt(size), color, underline
    p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(10), Pt(4)
    return p


def build_classic_docx(path):
    """Single column, Calibri 11, List Bullet bullets, right tab dates, header w/ hyperlink,
    footer, skills table, page break before Education."""
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(11)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.8)
    sec.top_margin = sec.bottom_margin = Inches(0.7)
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = hp.add_run("JORDAN LEE"); r.bold = True; r.font.size = Pt(18)
    hp2 = sec.header.add_paragraph(); hp2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hp2.add_run("jordan.lee@example.com | (555) 010-2030 | ")
    add_hyperlink(hp2, "https://github.com/jordanlee", "github.com/jordanlee")
    fp = sec.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fr = fp.add_run("Jordan Lee – Résumé"); fr.italic = True; fr.font.size = Pt(8)

    heading(doc, "Summary")
    doc.add_paragraph(SUMMARY)
    heading(doc, "Experience")
    for title, dates, bullets in EXPERIENCE:
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(Inches(6.9), WD_TAB_ALIGNMENT.RIGHT)
        r = p.add_run(title); r.bold = True
        p.add_run("\t")
        d = p.add_run(dates); d.italic = True; d.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        for bt in bullets:
            bp = doc.add_paragraph(style="List Bullet")
            bp.paragraph_format.space_after = Pt(2)
            # mixed run formatting inside a bullet: bold metric
            if "40%" in bt:
                a, b = bt.split("40%")
                bp.add_run(a); m = bp.add_run("40%"); m.bold = True; bp.add_run(b)
            else:
                bp.add_run(bt)
    heading(doc, "Projects")
    for name, bullets in PROJECTS:
        p = doc.add_paragraph(); r = p.add_run(name); r.bold = True
        for bt in bullets:
            doc.add_paragraph(bt, style="List Bullet")
    heading(doc, "Skills")
    table = doc.add_table(rows=len(SKILLS), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, (label, val) in enumerate(SKILLS):
        c0, c1 = table.rows[i].cells
        c0.paragraphs[0].add_run(label).bold = True
        c1.paragraphs[0].add_run(val)
    pb = doc.add_paragraph(); pb.add_run().add_break(WD_BREAK.PAGE)
    heading(doc, "Education")
    p = doc.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(Inches(6.9), WD_TAB_ALIGNMENT.RIGHT)
    p.add_run(EDUCATION[0]).bold = True; p.add_run("\t" + EDUCATION[1])
    doc.save(path)


def build_twocol_table_docx(path):
    """Two-column layout table, Georgia 10.5, manual en-dash bullets with tab, coloured headings."""
    doc = Document()
    st = doc.styles["Normal"]; st.font.name, st.font.size = "Georgia", Pt(10.5)
    sec = doc.sections[0]; sec.left_margin = sec.right_margin = Inches(0.5)
    name = doc.add_paragraph(); r = name.add_run("Jordan Lee"); r.font.size = Pt(22); r.font.color.rgb = RGBColor(0x5A, 0x2E, 0x2E)
    doc.add_paragraph(CONTACT)
    t = doc.add_table(rows=1, cols=2)
    left, right = t.rows[0].cells
    left.width, right.width = Inches(2.3), Inches(5.0)
    def cell_heading(cell, text, first=False):
        p = cell.paragraphs[0] if first else cell.add_paragraph()
        rr = p.add_run(text.upper()); rr.bold = True; rr.font.color.rgb = RGBColor(0xB5, 0x54, 0x5C); rr.font.size = Pt(11)
    cell_heading(left, "Skills", first=True)
    for label, val in SKILLS:
        p = left.add_paragraph(); p.add_run(label + ": ").bold = True; p.add_run(val)
    cell_heading(left, "Education")
    left.add_paragraph(EDUCATION[0]); left.add_paragraph(EDUCATION[1]).runs[0].italic = True
    cell_heading(right, "Summary", first=True)
    right.add_paragraph(SUMMARY)
    cell_heading(right, "Experience")
    for title, dates, bullets in EXPERIENCE:
        p = right.add_paragraph(); p.add_run(title).bold = True; p.add_run(" · "); p.add_run(dates).italic = True
        for bt in bullets:
            bp = right.add_paragraph()
            bp.paragraph_format.left_indent = Inches(0.2); bp.paragraph_format.first_line_indent = Inches(-0.15)
            g = bp.add_run("–\t"); g.font.color.rgb = RGBColor(0xB5, 0x54, 0x5C)
            bp.add_run(bt)
    cell_heading(right, "Projects")
    for pname, bullets in PROJECTS:
        right.add_paragraph().add_run(pname).bold = True
        for bt in bullets:
            bp = right.add_paragraph(); g = bp.add_run("–\t"); g.font.color.rgb = RGBColor(0xB5, 0x54, 0x5C); bp.add_run(bt)
    doc.save(path)


def build_section_columns_docx(path):
    """Section with 2 text columns (w:cols), numbered list, Times New Roman 12, underlined headings."""
    doc = Document()
    st = doc.styles["Normal"]; st.font.name, st.font.size = "Times New Roman", Pt(12)
    doc.add_paragraph().add_run("Jordan Lee").bold = True
    doc.add_paragraph(CONTACT)
    heading(doc, "Experience", color=RGBColor(0, 0, 0), size=12, underline=True)
    for title, dates, bullets in EXPERIENCE:
        p = doc.add_paragraph(); p.add_run(f"{title} ({dates})").bold = True
        for bt in bullets:
            doc.add_paragraph(bt, style="List Number")
    new_sec = doc.add_section(WD_SECTION.CONTINUOUS)
    cols = new_sec._sectPr.find(qn("w:cols"))
    if cols is None:
        cols = OxmlElement("w:cols"); new_sec._sectPr.append(cols)
    cols.set(qn("w:num"), "2"); cols.set(qn("w:space"), "720")
    heading(doc, "Skills", color=RGBColor(0, 0, 0), size=12, underline=True)
    for label, val in SKILLS:
        doc.add_paragraph(f"{label}: {val}")
    heading(doc, "Education", color=RGBColor(0, 0, 0), size=12, underline=True)
    doc.add_paragraph(f"{EDUCATION[0]}, {EDUCATION[1]}")
    doc.save(path)


# ---------------------------------------------------------------------------
# PDF fixtures (reportlab)
# ---------------------------------------------------------------------------
def build_pdfs():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    def bullets_block(c, x, y, items, font, size, width, lead, glyph="•", color=None):
        for it in items:
            words, line, lines = it.split(), "", []
            for w in words:
                trial = (line + " " + w).strip()
                if pdfmetrics.stringWidth(trial, font, size) > width - 12:
                    lines.append(line); line = w
                else:
                    line = trial
            lines.append(line)
            c.setFont(font, size)
            if color: c.setFillColor(color)
            c.drawString(x, y, glyph)
            c.setFillColor(HexColor("#000000"))
            for i, ln in enumerate(lines):
                c.drawString(x + 12, y, ln); y -= lead
            y -= 2
        return y

    # 1. single-column, standard Helvetica fonts, link annotation
    c = canvas.Canvas(str(HERE / "single_column.pdf"), pagesize=letter)
    c.setTitle("Jordan Lee Resume")
    W, H = letter
    y = H - 60
    c.setFont("Helvetica-Bold", 20); c.drawString(54, y, "Jordan Lee"); y -= 18
    c.setFont("Helvetica", 9.5); c.drawString(54, y, CONTACT)
    c.linkURL("https://github.com/jordanlee", (54 + pdfmetrics.stringWidth(CONTACT[:-19], "Helvetica", 9.5), y - 2,
                                               54 + pdfmetrics.stringWidth(CONTACT, "Helvetica", 9.5), y + 9))
    y -= 26
    def sec(title):
        nonlocal y
        c.setFont("Helvetica-Bold", 12); c.setFillColor(HexColor("#8A3B3B")); c.drawString(54, y, title.upper())
        c.setStrokeColor(HexColor("#F4B4B3")); c.line(54, y - 4, W - 54, y - 4); c.setFillColor(HexColor("#000000"))
        y -= 20
    sec("Summary")
    c.setFont("Helvetica", 10)
    for ln in ["Backend engineer with four years of experience building data-heavy web services in Python",
               "and shipping reliable APIs for e-commerce teams."]:
        c.drawString(54, y, ln); y -= 13
    y -= 8
    sec("Experience")
    for title, dates, bl in EXPERIENCE:
        c.setFont("Helvetica-Bold", 10.5); c.drawString(54, y, title)
        c.setFont("Helvetica-Oblique", 10); c.drawRightString(W - 54, y, dates); y -= 15
        y = bullets_block(c, 60, y, bl, "Helvetica", 10, W - 120, 13) - 6
    sec("Projects")
    for name, bl in PROJECTS:
        c.setFont("Helvetica-Bold", 10.5); c.drawString(54, y, name); y -= 15
        y = bullets_block(c, 60, y, bl, "Helvetica", 10, W - 120, 13) - 6
    sec("Skills")
    for label, val in SKILLS:
        c.setFont("Helvetica-Bold", 10); c.drawString(54, y, label + ":")
        c.setFont("Helvetica", 10); c.drawString(54 + pdfmetrics.stringWidth(label + ": ", "Helvetica-Bold", 10), y, val); y -= 14
    y -= 8
    sec("Education")
    c.setFont("Helvetica-Bold", 10.5); c.drawString(54, y, EDUCATION[0])
    c.setFont("Helvetica-Oblique", 10); c.drawRightString(W - 54, y, EDUCATION[1])
    c.save()

    # 2. two-column, embedded TrueType subset (Arial / Georgia), 2 pages with page numbers
    reg = "/System/Library/Fonts/Supplemental/Arial.ttf"
    if os.path.exists(reg):
        pdfmetrics.registerFont(TTFont("ArialTT", reg))
        pdfmetrics.registerFont(TTFont("ArialTT-Bold", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"))
        body, bold = "ArialTT", "ArialTT-Bold"
    else:  # pragma: no cover
        body, bold = "Helvetica", "Helvetica-Bold"
    c = canvas.Canvas(str(HERE / "two_column.pdf"), pagesize=A4)
    W, H = A4
    gutter = 200
    def page_furniture(n):
        c.setFont(body, 8); c.setFillColor(HexColor("#777777")); c.drawCentredString(W / 2, 24, f"Page {n} of 2")
        c.setFillColor(HexColor("#F9DCB5")); c.rect(0, 0, gutter - 10, H, stroke=0, fill=1)
        c.setFillColor(HexColor("#000000"))
    page_furniture(1)
    ly = H - 60
    c.setFont(bold, 18); c.drawString(30, ly, "Jordan Lee"); ly -= 20
    c.setFont(body, 8.5)
    for part in CONTACT.split(" | "):
        c.drawString(30, ly, part); ly -= 12
    ly -= 14
    c.setFont(bold, 11); c.setFillColor(HexColor("#8A3B3B")); c.drawString(30, ly, "SKILLS"); c.setFillColor(HexColor("#000000")); ly -= 16
    for label, val in SKILLS:
        c.setFont(bold, 9); c.drawString(30, ly, label); ly -= 12
        c.setFont(body, 9); c.drawString(30, ly, val); ly -= 16
    ly -= 6
    c.setFont(bold, 11); c.setFillColor(HexColor("#8A3B3B")); c.drawString(30, ly, "EDUCATION"); c.setFillColor(HexColor("#000000")); ly -= 16
    c.setFont(body, 9); c.drawString(30, ly, "B.S. Computer Science"); ly -= 12; c.drawString(30, ly, "State University, 2016 – 2020")
    ry = H - 60
    rx = gutter + 10
    def rsec(t):
        nonlocal ry
        c.setFont(bold, 12); c.setFillColor(HexColor("#8A3B3B")); c.drawString(rx, ry, t); c.setFillColor(HexColor("#000000")); ry -= 18
    rsec("SUMMARY")
    c.setFont(body, 9.5)
    for ln in ["Backend engineer with four years of experience building data-heavy",
               "web services in Python and shipping reliable APIs for e-commerce teams."]:
        c.drawString(rx, ry, ln); ry -= 12.5
    ry -= 10
    rsec("EXPERIENCE")
    for title, dates, bl in EXPERIENCE:
        c.setFont(bold, 10); c.drawString(rx, ry, title); ry -= 12
        c.setFont(body, 9); c.setFillColor(HexColor("#555555")); c.drawString(rx, ry, dates); c.setFillColor(HexColor("#000000")); ry -= 14
        ry = bullets_block(c, rx + 4, ry, bl, body, 9.5, W - rx - 40, 12.5, color=HexColor("#B5545C")) - 6
    c.showPage()
    page_furniture(2)
    ry = H - 60
    rsec("PROJECTS")
    for name, bl in PROJECTS:
        c.setFont(bold, 10); c.drawString(rx, ry, name); ry -= 14
        ry = bullets_block(c, rx + 4, ry, bl, body, 9.5, W - rx - 40, 12.5, color=HexColor("#B5545C"))
    c.save()

    # 3. nearly full page so that an insertion must overflow to a new page
    c = canvas.Canvas(str(HERE / "tight_page.pdf"), pagesize=letter)
    W, H = letter
    y = H - 50
    c.setFont("Times-Bold", 16); c.drawString(54, y, "Jordan Lee"); y -= 14
    c.setFont("Times-Roman", 10); c.drawString(54, y, CONTACT); y -= 22
    c.setFont("Times-Bold", 11.5); c.drawString(54, y, "EXPERIENCE"); y -= 16
    filler = [("Software Engineer, Acme Commerce", "Jan 2022 – Present", EXPERIENCE[0][2] * 6),
              ("Junior Developer, Brightside Labs", "Jun 2020 – Dec 2021", EXPERIENCE[1][2] * 7)]
    for title, dates, bl in filler:
        c.setFont("Times-Bold", 10.5); c.drawString(54, y, title); c.setFont("Times-Italic", 10); c.drawRightString(W - 54, y, dates); y -= 14
        y = bullets_block(c, 60, y, bl, "Times-Roman", 10.5, W - 120, 13) - 4
    c.setFont("Times-Bold", 11.5); c.drawString(54, y, "PROJECTS"); y -= 16
    c.setFont("Times-Bold", 10.5); c.drawString(54, y, "Inventory Tracker"); y -= 14
    # fill the page down to the bottom margin so that any insertion must overflow
    while y > 140:
        y = bullets_block(c, 60, y, PROJECTS[0][1], "Times-Roman", 10.5, W - 120, 13)
    c.setFont("Times-Bold", 11.5); c.drawString(54, y, "SKILLS"); y -= 16
    c.setFont("Times-Roman", 10.5)
    for label, val in SKILLS:
        c.drawString(54, y, f"{label}: {val}"); y -= 13
    y -= 6
    c.setFont("Times-Bold", 11.5); c.drawString(54, y, "EDUCATION"); y -= 16
    c.setFont("Times-Roman", 10.5); c.drawString(54, y, f"{EDUCATION[0]}, {EDUCATION[1]}")
    c.save()

    # 4. scanned (image-only) version of the single-column resume
    import pymupdf as fitz
    srcdoc = fitz.open(HERE / "single_column.pdf")
    out = fitz.open()
    for p in srcdoc:
        pix = p.get_pixmap(dpi=200)
        np_ = out.new_page(width=p.rect.width, height=p.rect.height)
        np_.insert_image(np_.rect, stream=pix.tobytes("jpeg", jpg_quality=80))
    out.save(HERE / "scanned.pdf")


def main():
    build_classic_docx(HERE / "classic.docx")
    build_twocol_table_docx(HERE / "two_column_table.docx")
    build_section_columns_docx(HERE / "section_columns.docx")
    build_pdfs()
    (HERE / "job_description.txt").write_text(JD)
    print("fixtures written to", HERE)


if __name__ == "__main__":
    main()
