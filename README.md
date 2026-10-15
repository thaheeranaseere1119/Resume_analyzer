# CareerCopilot AI: Add to Existing Resume

One-click, approval-gated keyword insertion into an **existing** DOCX or PDF resume. Formatting is preserved, every claim is checked against the resume, and the score impact is calculated deterministically.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn careercopilot.api:app --app-dir backend --port 8765
```

Open http://localhost:8765 and upload a resume plus a job description, or click **Use the sample resume**.
OCR for scanned PDFs needs the `tesseract` binary with `eng.traineddata`. The tessdata folder is detected automatically.

```bash
.venv/bin/python -m pytest tests -q
```

The test run also regenerates [tests/reports/preservation_report.md](tests/reports/preservation_report.md), which lists every formatting property verified per fixture.

## The suite

| Page | What it does |
|---|---|
| **Tailor resume** (`#/`) | Upload a resume + job description, then approve verified edits to the existing DOCX/PDF (details below). Recent sessions are listed under the form. |
| **Cover letters** (`#/letters`) | Drafts a letter from the tailored resume and the job. Every sentence is fact-checked: skills and figures that aren't in your resume or your own notes are flagged, and download is blocked until they're fixed. Export as DOCX (in your resume's body font) or TXT. |
| **Tracker** (`#/tracker`) | Kanban board (Saved → Applied → Interview → Offer → Rejected) with drag-and-drop or a status menu, notes, dates, salary, contact, and status history. Each job links to its tailored resume and its letters, and shows the current score. New analyses are added automatically (optional). |

### Claude writing (optional)

With an Anthropic API key, Claude (`claude-opus-5-5`) drafts cover letters, turns your descriptions into resume bullets, and powers **✦ Rewrite with Claude** on each card.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/uvicorn careercopilot.api:app --app-dir backend --port 8765
```

Claude only writes words. Its drafts go through the same grammar and factual checks and the same approval step. A bullet draft that adds an unconfirmed tool or an invented number is discarded, and the built-in draft is shown with an explanation. Scores always come from the deterministic engine. Without a key, or if the API is unreachable, every feature falls back to the built-in templates and the UI says which writer was used. Resume and job text are sent to the Claude API only when a key is configured.

## Workflow

1. Parse the resume into a format-independent model, and the job description into weighted requirements.
2. Score with the deterministic engine. For every missing or partially matched requirement, build a recommendation card.
3. Each card option is validated for grammar and factual support, then **simulated in memory** with the same engine to predict the score.
4. **Preview Change** renders the edited document without saving it.
5. **Add to Existing Resume** / **Replace Existing Text** opens an approval dialog. Nothing is applied without *Approve & apply*.
6. The targeted edit is applied, verified, and saved as a new version. The original upload is read-only.
7. The saved file is **re-parsed and re-scored independently**. Predicted and actual scores are shown side by side.
8. Undo, restore any version, or download any version.

Rejected and unanswered recommendations can't be applied; the server refuses them with 409. A version is never saved if critical verification fails: the edited text isn't extractable, hidden or duplicated text is found, or unrelated content changed.

## Architecture (`backend/careercopilot/`)

| Module | Responsibility |
|---|---|
| `taxonomy.py`, `data/skills.json` | Normalised skill IDs, aliases, implications (PostgreSQL ⇒ SQL), weak-evidence rewrite rules, learning projects |
| `jd.py` | Required/preferred skills, certifications, years, degree, responsibilities |
| `docx_io.py`, `pdf_io.py` | Readers. The PDF reader keeps spans, fonts, baselines and columns; scanned pages go through Tesseract OCR |
| `model.py` | Sections, block kinds, experience/project entries; in-memory edits for simulation |
| `scoring.py` | Component scores and weighted overall score |
| `grammar.py` | Spelling, punctuation, tense, agreement, articles, tone, conciseness, terminology, keyword stuffing, relevance |
| `factual.py` | Claim classification: evidence / confirmation / stylistic / clarification / unsupported |
| `recommend.py` | Locates the best section, generates wording, validates and simulates every option |
| `edit_docx.py` | Rewrites only `word/document.xml`; every other package part is copied byte for byte |
| `edit_pdf.py` | Region-preserving page rebuild with honest verification |
| `versions.py`, `service.py`, `api.py` | Immutable original, version history, approval workflow, REST API |
| `llm.py` | Optional Claude drafting (structured JSON output, refusal fallback, graceful degradation) |
| `coverletter.py` | Letter drafting, sentence-level fact checking, DOCX/TXT export |
| `tracker.py` | Application tracker store (`data/tracker.json`) |

## Scoring (methodology `ccai-score-1.0`)

`overall = Σ component × weight / Σ applicable weights × 100`, clamped to 0–100.
The weights are initial project assumptions, configurable through `CAREERCOPILOT_WEIGHTS`:

| Component | Weight |
|---|---|
| Keyword and skill coverage | 30% |
| Contextual skill relevance | 25% |
| Experience and responsibility alignment | 20% |
| Education and certifications | 10% |
| Formatting and parsing quality | 15% |

Anti-stuffing rules:

* A skill counts **once**, at its strongest evidence level:
  * demonstrated in an experience or project sentence: 1.0
  * summary or tech line: 0.7
  * implied by a demonstrated related skill: 0.5
  * skills list only: 0.35
* Keyword-list "sentences" count as listed, not as demonstrated.
* Repeated mentions and synonyms add nothing.
* Edits that add no new coverage or evidence show **0.0** estimated points.

The absolute point change is the primary metric; the relative % is labelled separately. Neither is a hiring probability.

## How formatting is preserved

**DOCX**
* Replacements are word-level diffs mapped onto the existing runs, so bold metrics, hyperlinks and coloured labels stay intact, and the paragraph's style, numbering, indentation, spacing and tab stops are untouched.
* Inserted bullets clone the neighbouring paragraph's properties and its body-text run formatting. Paragraph IDs and section breaks are never duplicated.
* Verification re-opens the file and checks that unrelated paragraphs, section properties, tables, hyperlinks, page breaks and headers/footers are byte-identical.
* In testing, Microsoft Word rendered all three DOCX layouts with identical pagination.

**PDF**
* Unaffected pages are copied as-is.
* The edited page is rebuilt from vector regions of the original. Only content in the edited column below the edit point moves.
* Text outside each region is redacted, so no hidden text is left behind; this is verified with pdfminer as an independent extractor.
* New text uses the original embedded font when it contains all the needed glyphs and passes a Unicode round-trip test.
* Overflow past the bottom margin moves whole blocks to the next page, keeping headings with their content, and is reported as a pagination change.

## Known limitations (also shown in the UI)

* **PDF fonts:** subset fonts often lack glyphs for new words. The same family is then loaded from a system font (reported as *approximated*), or a standard PDF font is used (*substituted*). Text set with some system fonts extracts spaces as non-breaking spaces.
* **Scanned PDFs:** edits are reconstructed on the page image. New text uses Helvetica sized from OCR, and the result is re-read with OCR.
* **PDF pagination:** overflow can change pagination. Annotations other than links on edited pages aren't carried over (reported).
* **DOCX preview:** the in-app preview of a DOCX is an HTML approximation of Word's layout. The downloaded file is the authoritative result.
* **Wording generation:** without an API key, wording is rule-based, so phrasing can be plain. Use *Edit Suggestion*, which re-runs every validation.
* **Single user:** there are no accounts. All data lives in `data/` on this machine.
# Resume_analyzer
