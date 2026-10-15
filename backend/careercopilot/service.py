"""Session orchestration: the approval and document-update workflow.

1. identify unmet requirements        6. predict score impact (in memory)
2. locate the best resume section     7. preview the formatted document
3. generate addition/replacement      8. wait for explicit approval
4. validate grammar + facts           9-11. apply targeted edit, save new version
5. show original vs proposed          12-15. re-parse saved file, recalculate actual
                                             score, compare, preview, undo/restore
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from datetime import date
from pathlib import Path

from . import ats_format, coverletter, editor, llm
from . import textutil as tu
from .config import DATA_DIR, METHODOLOGY_VERSION, load_weights
from .docx_io import read_docx
from .edit_docx import DocxEditError, apply_docx_edit, verify_docx_edit
from .edit_pdf import PdfEditError, apply_pdf_edit, verify_pdf_edit
from .jd import parse_jd
from .pdf_io import read_pdf
from .preview import docx_preview, pdf_preview
from .recommend import Recommender
from .scoring import score_delta, score_resume
from .versions import VersionStore


# A version is never saved if any of these verification checks fail.
CRITICAL_PROPERTIES = {
    "Valid, editable DOCX package", "Valid PDF", "Edited text written exactly", "Edited text written",
    "Unrelated paragraphs (headings, dates, contact info, section order)",
    "Unaffected text: fonts, sizes, colours, positions", "No hidden or duplicated text (pdfminer)",
}


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_document(data: bytes, ext: str, as_of: date):
    if ext == ".docx":
        return read_docx(data, as_of), None
    if ext == ".pdf":
        return read_pdf(data, as_of)
    raise WorkflowError("Only .docx and .pdf resumes are supported")


class Session:
    _locks: dict[str, threading.Lock] = {}

    def __init__(self, sid: str):
        self.sid = sid
        self.root = DATA_DIR / "sessions" / sid
        if not (self.root / "session.json").exists():
            raise WorkflowError("Session not found", 404)
        self.state = json.loads((self.root / "session.json").read_text())
        self.store = VersionStore(self.root)
        self.jd = parse_jd(self.state["jd_text"])
        self.weights = self.state["weights"]
        self.as_of = date.fromisoformat(self.state["as_of"])
        self._cache: dict = {}
        self.lock = Session._locks.setdefault(sid, threading.Lock())

    # ------------------------------------------------------------------ setup
    @classmethod
    def create(cls, data: bytes, filename: str, jd_text: str, company: str = "", role: str = "") -> "Session":
        ext = Path(filename).suffix.lower()
        if ext not in (".docx", ".pdf"):
            raise WorkflowError("Please upload a .docx or .pdf resume")
        if len(jd_text.strip()) < 40:
            raise WorkflowError("Please paste the full job description")
        as_of = date.today()
        parse_document(data, ext, as_of)  # fail fast on unreadable files
        sid = secrets.token_urlsafe(9)
        root = DATA_DIR / "sessions" / sid
        VersionStore.create(root, data, filename)
        state = {"jd_text": jd_text, "weights": load_weights(), "methodology": METHODOLOGY_VERSION,
                 "as_of": as_of.isoformat(), "created": time.time(), "confirmations": {}, "denials": [],
                 "answers": {}, "rejected": {}, "edited": {}, "letters": [],
                 "company": company.strip()[:200], "role": role.strip()[:200] or parse_jd(jd_text).title}
        (root / "session.json").write_text(json.dumps(state, indent=2))
        return cls(sid)

    def _save(self):
        (self.root / "session.json").write_text(json.dumps(self.state, indent=2))

    @property
    def ext(self) -> str:
        return self.store.manifest["ext"]

    # --------------------------------------------------------------- analysis
    def current(self):
        head = self.store.head
        if head not in self._cache:
            data = self.store.read(head)
            model, layout = parse_document(data, self.ext, self.as_of)
            score = score_resume(model, self.jd, self.weights)
            self._cache[head] = (data, model, layout, score)
        return self._cache[head]

    def recommender(self) -> Recommender:
        _, model, _, score = self.current()
        return Recommender(model, self.jd, self.state["jd_text"], self.weights, score,
                           self.state["confirmations"], set(self.state["denials"]), self.state["answers"])

    def recommendations(self) -> list[dict]:
        recs = self.recommender().build()
        for r in recs:
            if r["key"] in self.state["rejected"]:
                r["status"] = "rejected"
            edited = self.state["edited"].get(r["id"])
            if edited and r["options"]:
                rec = self.recommender()
                idx = min(edited.get("selected", 0), len(r["options"]) - 1)
                opt = dict(r["options"][idx])
                if edited.get("block_id") == opt["block_id"] and edited.get("mode") == opt["mode"]:
                    opt["proposed_text"] = edited["text"]
                    opt["edited_by_user"] = True
                    r["options"][idx] = rec.evaluate(opt)
                r["selected"] = idx
        return recs

    def find_rec(self, rec_id: str) -> dict:
        rec = next((r for r in self.recommendations() if r["id"] == rec_id), None)
        if rec is None:
            raise WorkflowError("This recommendation no longer applies to the current version", 404)
        return rec

    @staticmethod
    def list_all() -> list[dict]:
        root = DATA_DIR / "sessions"
        out = []
        for d in root.glob("*/session.json") if root.exists() else []:
            try:
                st = json.loads(d.read_text())
                man = json.loads((d.parent / "manifest.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"session_id": d.parent.name, "filename": man["filename"], "company": st.get("company", ""),
                        "role": st.get("role", ""), "created": st.get("created"), "head": man["head"],
                        "versions": len(man["versions"]), "letters": len(st.get("letters", [])),
                        "score": st.get("last_score")})
        return sorted(out, key=lambda x: -(x["created"] or 0))

    def summary(self) -> dict:
        _, model, _, score = self.current()
        if self.state.get("last_score") != score.overall:
            self.state["last_score"] = score.overall
            self._save()
        applied = []
        for vid in self.store.lineage()[1:]:
            v = self.store.version(vid)
            applied.append({"version": vid, "label": v["label"], "change": v["change"]})
        return {
            "session_id": self.sid,
            "company": self.state.get("company", ""),
            "role": self.state.get("role", ""),
            "llm": {"configured": llm.configured(), "model": llm.MODEL},
            "filename": self.store.manifest["filename"],
            "format": self.ext.lstrip("."),
            "head": self.store.head,
            "versions": [{k: v[k] for k in ("id", "label", "parent", "created")} for v in self.store.manifest["versions"]],
            "applied": applied,
            "score": score.to_dict(),
            "job": {"title": self.jd.title, "skills": [s.__dict__ for s in self.jd.skills],
                    "min_years": self.jd.min_years},
            "document": {"scanned": model.scanned, "ocr_confidence": model.ocr_confidence,
                         "page_count": model.page_count, "columns": model.meta.get("columns"),
                         "blocks": [{"id": b.id, "text": b.text, "section": b.section, "kind": b.kind,
                                     "entry_id": b.entry_id} for b in model.blocks]},
            "recommendations": self.recommendations(),
            "remaining": [r for r in score.requirements if r["status"] != "met"],
        }

    # ---------------------------------------------------------------- options
    def _resolve_option(self, rec: dict, override: dict | None) -> dict:
        if rec["status"] == "rejected":
            raise WorkflowError("This recommendation was rejected and cannot be applied", 409)
        if not rec["options"]:
            raise WorkflowError("Answer the clarification question before applying this recommendation", 409)
        idx = int((override or {}).get("selected", rec.get("selected", 0)))
        if not 0 <= idx < len(rec["options"]):
            raise WorkflowError("Unknown option")
        opt = dict(rec["options"][idx])
        if override:
            if override.get("mode") in ("replace", "insert_after"):
                opt["mode"] = override["mode"]
                opt["action"] = "replace" if override["mode"] == "replace" else "add"
            if override.get("block_id"):
                _, model, _, _ = self.current()
                model.block(override["block_id"])  # validates the id
                opt["block_id"] = override["block_id"]
            if override.get("text") is not None:
                opt["proposed_text"] = tu.normalize_ws(override["text"])
                opt["edited_by_user"] = True
            opt = self.recommender().evaluate(opt)
        return opt

    def simulate(self, rec_id: str, override: dict | None) -> dict:
        rec = self.find_rec(rec_id)
        return self._resolve_option(rec, override)

    def edit_suggestion(self, rec_id: str, override: dict) -> dict:
        rec = self.find_rec(rec_id)
        opt = self._resolve_option(rec, override)
        self.state["edited"][rec_id] = {"text": opt["proposed_text"], "block_id": opt["block_id"],
                                        "mode": opt["mode"], "selected": int(override.get("selected", 0))}
        self._save()
        return opt

    def _edit_op(self, opt: dict) -> dict:
        _, model, _, _ = self.current()
        b = model.block(opt["block_id"])
        op = {"mode": opt["mode"], "block_id": opt["block_id"], "text": opt["proposed_text"]}
        if opt["mode"] == "replace":
            op["expected_text"] = b.body
        return op

    def _apply_bytes(self, opt: dict) -> tuple[bytes, dict, dict]:
        data, model, layout, _ = self.current()
        op = self._edit_op(opt)
        try:
            if self.ext == ".docx":
                new, info = apply_docx_edit(data, op)
                report = verify_docx_edit(data, new, op, info)
                info["highlight"] = [f"p{info['new_paragraph_index']}"]
            else:
                new, info = apply_pdf_edit(data, layout, op)
                report = verify_pdf_edit(data, new, layout, op, info)
                ascent = 0.8
                first = info["first_baseline"]
                size = next((s["size"] for s in layout.blocks[op["block_id"]].body_spans()), 10)
                x0, x1 = info["column_range"]
                info["highlight"] = [(info["page"], (x0 + 6, first - size * ascent - 2, x1 - 6,
                                                      first + (info["lines"] - 1) * info["pitch"] + size * 0.3 + 2))]
        except (DocxEditError, PdfEditError) as exc:
            raise WorkflowError(f"Could not edit the document safely: {exc}", 422)
        return new, info, report

    def preview(self, rec_id: str, override: dict | None) -> dict:
        """Render the proposed result without saving it."""
        rec = self.find_rec(rec_id)
        opt = self._resolve_option(rec, override)
        new, info, report = self._apply_bytes(opt)
        return {"option": opt, "preservation": report, **self._render(new, info["highlight"])}

    def _render(self, data: bytes, highlight) -> dict:
        if self.ext == ".docx":
            return {"format": "docx", "html": docx_preview(data, highlight),
                    "fidelity": "HTML approximation of the Word layout; open the downloaded file for exact rendering."}
        return {"format": "pdf", "pages": pdf_preview(data, highlight),
                "fidelity": "Exact rendering of the PDF pages; the dashed outline is shown in the preview only."}

    def document(self, vid: int | None = None) -> dict:
        data = self.store.read(vid)
        return self._render(data, [])

    # ---------------------------------------------------------------- actions
    def apply(self, rec_id: str, override: dict | None, approved: bool, version: int | None) -> dict:
        if not approved:
            raise WorkflowError("Changes are only applied after explicit approval", 403)
        with self.lock:
            if version is not None and int(version) != self.store.head:
                raise WorkflowError("The resume changed since this recommendation was shown; refresh first", 409)
            rec = self.find_rec(rec_id)
            opt = self._resolve_option(rec, override)
            if not opt["can_apply"]:
                raise WorkflowError(opt.get("blocked_reason", "This change has not passed validation"), 409)
            _, model, _, before = self.current()
            predicted = opt["score"]
            new, info, report = self._apply_bytes(opt)
            critical = [r for r in report["properties"] if r["status"] == "changed" and r["property"] in CRITICAL_PROPERTIES]
            if critical:
                raise WorkflowError("The edited document failed verification and was not saved: " +
                                    "; ".join(f"{r['property']} ({r['detail']})" for r in critical), 422)
            change = {
                "rec_id": rec_id, "requirement": rec["requirement"]["name"], "requirement_id": rec["key"],
                "action": opt["action"], "mode": opt["mode"], "block_id": opt["block_id"],
                "target_label": opt["target_label"], "original_text": opt.get("original_text"),
                "new_text": opt["proposed_text"], "factual_status": opt["factual"]["label"],
                "predicted": {"before": predicted["before"], "after": predicted["after"], "points": predicted["points"]},
                "preservation": report, "methodology": METHODOLOGY_VERSION, "applied_at": time.time(),
            }
            v = self.store.add(new, f"{'Added' if opt['action'] == 'add' else 'Replaced'}: {rec['requirement']['name']}", change)
            # Independent recalculation from the saved file (fresh parse, same engine & weights).
            saved = self.store.read(v["id"])
            m2, l2 = parse_document(saved, self.ext, self.as_of)
            actual = score_resume(m2, self.jd, self.weights)
            self._cache[v["id"]] = (saved, m2, l2, actual)
            actual_delta = score_delta(before, actual)
            comparison = {
                "current_before": before.overall,
                "predicted_after": predicted["after"], "predicted_points": predicted["points"],
                "actual_after": actual.overall, "actual_points": actual_delta["points"],
                "relative_percent": actual_delta["relative_percent"],
                "match": abs(actual.overall - predicted["after"]) < 0.05,
                "explanation": None if abs(actual.overall - predicted["after"]) < 0.05 else
                "The saved document parsed slightly differently from the in-memory simulation "
                "(for example pagination or OCR changes); the actual score is authoritative.",
                "requirement_changes": actual_delta["requirement_changes"],
            }
            self.store.update_change(v["id"], actual={"after": actual.overall, "points": actual_delta["points"]},
                                     comparison=comparison)
            self.state["edited"].pop(rec_id, None)
            self._save()
            return {"version": v["id"], "comparison": comparison, "preservation": report,
                    "preview": self._render(saved, info["highlight"]),
                    "remaining": [r for r in actual.requirements if r["status"] != "met"]}

    def reject(self, rec_id: str) -> dict:
        rec = self.find_rec(rec_id)
        self.state["rejected"][rec["key"]] = time.time()
        self._save()
        return {"rejected": rec_id}

    def restore_rejected(self, rec_id: str) -> dict:
        rec = next((r for r in self.recommendations() if r["id"] == rec_id), None)
        if rec:
            self.state["rejected"].pop(rec["key"], None)
            self._save()
        return {"restored": rec_id}

    def answer(self, rec_id: str, yes: bool, text: str = "", anchor_id: str | None = None) -> dict:
        """Record the candidate's answer to a clarification question."""
        rec = self.find_rec(rec_id)
        q = rec.get("question") or {}
        sid = q.get("skill_id")
        key = sid or rec["key"]
        text = tu.normalize_ws(text or "")
        if not yes:
            if key not in self.state["denials"]:
                self.state["denials"].append(key)
            self.state["confirmations"].pop(key, None)
            self.state["answers"].pop(rec["key"], None)
        elif sid and text and self._answer_denies(sid, text):
            # The words say "not used / not yet": treat as No, never as a confirmation.
            if key not in self.state["denials"]:
                self.state["denials"].append(key)
            self.state["confirmations"].pop(key, None)
            self.state["answers"].pop(rec["key"], None)
            self.state.setdefault("denial_notes", {})[key] = (
                "Your answer says you haven't used this yet, so it was not added to your resume.")
        else:
            # A bare "yes" is enough to confirm a rewrite of existing wording; new content needs a description.
            has_rewrite = any(o.get("strategy") in ("weak", "skills_line") for o in rec["options"])
            if not text and not (q.get("type") == "skill_usage" and has_rewrite):
                raise WorkflowError("Please describe the experience so we can write an accurate statement")
            if key in self.state["denials"]:
                self.state["denials"].remove(key)
            if sid:
                self.state["confirmations"][sid] = {"text": text or "Confirmed by candidate", "at": time.time(),
                                                    "requirement": rec["key"]}
            if text:
                self.state["answers"][rec["key"]] = {"text": text, "anchor_id": anchor_id}
        self._save()
        return self.find_rec(rec_id)

    @staticmethod
    def _answer_denies(skill_id: str, text: str) -> bool:
        from .phrasing import is_negated, skill_mentions
        from .taxonomy import taxonomy
        pos, only_neg = skill_mentions(text, skill_id)
        if only_neg:
            return True
        # "I have not used it yet" without naming the skill
        name = taxonomy().get(skill_id).name.lower()
        return not pos and is_negated(text) and not any(
            not is_negated(sent) and len(sent.split()) > 4 for sent in re.split(r"(?<=[.!?])\s+", text)) \
            or (not pos and re.search(r"\b(haven't|have not|never)\s+(used|worked|tried)\b", text, re.I) is not None
                and name not in text.lower())

    def confirm_claim(self, skill_id: str, text: str) -> dict:
        self.state["confirmations"][skill_id] = {"text": tu.normalize_ws(text or ""), "at": time.time()}
        if skill_id in self.state["denials"]:
            self.state["denials"].remove(skill_id)
        self._save()
        return {"confirmed": skill_id}

    # ------------------------------------------------------------- ATS format & explanation
    def format_audit(self) -> dict:
        _, model, layout, _ = self.current()
        return ats_format.audit(model, self.ext.lstrip("."), layout)

    def ats_explained(self) -> dict:
        _, model, _, score = self.current()
        from .config import EVIDENCE_LEVELS, IMPORTANCE_WEIGHTS
        from .taxonomy import taxonomy
        tax = taxonomy()
        skills = []
        for r in score.requirements:
            if r["type"] != "skill":
                continue
            ev = score.evidence.get(r["skill_id"])
            skills.append({"name": r["name"], "importance": r["importance"], "status": r["status"],
                           "evidence_level": r["evidence_level"], "credit": EVIDENCE_LEVELS[r["evidence_level"]],
                           "weight": IMPORTANCE_WEIGHTS[r["importance"]], "generic": tax.get(r["skill_id"]).generic,
                           "where": (ev.quote[:160] if ev and ev.level != "none" else None)})
        return {"score": score.to_dict(), "skills": skills, "format": self.format_audit(),
                "evidence_levels": EVIDENCE_LEVELS, "importance_weights": IMPORTANCE_WEIGHTS}

    # ------------------------------------------------------------- eligible jobs
    def eligible_jobs(self) -> list[dict]:
        import json as _json
        from urllib.parse import quote_plus
        from .jd import parse_jd
        from .scoring import score_resume
        _, model, _, _ = self.current()
        roles = _json.loads((Path(__file__).with_name("data") / "roles.json").read_text())["roles"]
        out = []
        for role in roles:
            jd = parse_jd(role["text"])
            sc = score_resume(model, jd, self.weights)
            from .taxonomy import taxonomy as _tax
            req = [r for r in sc.requirements if r["type"] == "skill" and r["importance"] == "required"
                   and not _tax().get(r["skill_id"]).generic]
            # "Java, Python or C++": one requirement satisfied by any of the alternatives
            groups: dict[str, list[dict]] = {}
            for r in req:
                key = r["jd_text"] if re.search(r"\bor\b", r["jd_text"]) else r["skill_id"]
                groups.setdefault(key, []).append(r)
            have, miss = [], []
            for g in groups.values():
                ok = [r["name"] for r in g if r["status"] != "missing"]
                if ok:
                    have += ok
                else:
                    miss.append(" or ".join(r["name"] for r in g))
            req = list(groups.values())
            pref_have = [r["name"] for r in sc.requirements if r["type"] == "skill" and r["importance"] == "preferred"
                         and r["status"] != "missing"]
            degree = next((r for r in sc.requirements if r["type"] == "education"), None)
            coverage = (len(req) - len(miss)) / len(req) if req else 0
            eligible = coverage >= 0.6 and (degree is None or degree["status"] != "missing")
            q = quote_plus(role["title"])
            out.append({"id": role["id"], "title": role["title"], "level": role["level"], "score": sc.overall,
                        "required_coverage": round(100 * coverage), "eligible": eligible,
                        "matched": have, "missing": miss, "preferred_matched": pref_have,
                        "degree_ok": degree is None or degree["status"] == "met",
                        "search": {"LinkedIn": f"https://www.linkedin.com/jobs/search/?keywords={q}",
                                   "Indeed": f"https://www.indeed.com/jobs?q={q}",
                                   "Naukri": f"https://www.naukri.com/{role['title'].lower().replace(' / ', '-').replace('/', '-').replace(' ', '-')}-jobs"}})
        out.sort(key=lambda r: (not r["eligible"], -r["score"]))
        return out

    # ------------------------------------------------------------- editor
    def editor_get(self) -> dict:
        draft = self.state.get("draft")
        _, model, _, _ = self.current()
        if not draft:
            draft = editor.draft_from_model(model, self.store.head)
            self.state["draft"] = draft
            self._save()
        return self._editor_payload(draft)

    def _editor_payload(self, draft: dict) -> dict:
        from .recommend import _known_words
        from .scoring import score_resume
        _, model, _, current = self.current()
        dm = editor.model_from_draft(draft, self.as_of)
        sc = score_resume(dm, self.jd, self.weights)
        return {"draft": draft, "stale": draft.get("base_version") != self.store.head,
                "score": {"draft": sc.overall, "document": current.overall,
                          "components": {k: round(100 * c.score, 1) for k, c in sc.components.items() if c.applicable}},
                "checks": editor.line_checks(draft, _known_words(model, self.state["jd_text"])),
                "pending_original": len(editor.changed_original_lines(draft))}

    def editor_save(self, draft: dict) -> dict:
        clean = editor.sanitize(draft)
        self.state["draft"] = clean
        self._save()
        return self._editor_payload(clean)

    def editor_reset(self) -> dict:
        self.state.pop("draft", None)
        self._save()
        return self.editor_get()

    def editor_improve(self, text: str, kind: str) -> dict:
        from .phrasing import phrase_bullets
        from .polish import improve_line
        text = tu.normalize_ws(text)[:1500]
        better = improve_line(text, kind=kind if kind in editor.KINDS else "paragraph", tense="past")
        if kind == "bullet" and re.match(r"^(i|we|my)\b", text, re.I):
            alt = phrase_bullets(text, tense="past")
            if alt:
                better = alt[0]
        return {"text": better, "changed": better != text}

    def editor_export(self, fmt: str) -> tuple[bytes, str, str]:
        draft = self.state.get("draft") or editor.draft_from_model(self.current()[1], self.store.head)
        _, model, _, _ = self.current()
        docx, pdf = ats_format.export_fitted(draft, max(1, model.page_count or 1))
        stem = re.sub(r"[^A-Za-z0-9]+", "_", f"Resume_ATS_{self.state.get('company') or 'version'}").strip("_")
        if fmt == "pdf":
            if pdf is None:
                raise WorkflowError("PDF export needs LibreOffice; download the DOCX instead.", 503)
            return pdf, f"{stem}.pdf", "application/pdf"
        return docx, f"{stem}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def editor_apply_to_original(self) -> dict:
        """Write edited lines back into the original document (same fonts and layout), one verified version each."""
        draft = self.state.get("draft")
        if not draft:
            raise WorkflowError("Open the editor first", 409)
        if draft.get("base_version") != self.store.head:
            raise WorkflowError("Your document changed since the editor was opened. Reload the editor first.", 409)
        applied, skipped = [], []
        for line in editor.changed_original_lines(draft):
            _, model, _, _ = self.current()
            target = next((b for b in model.blocks if tu.normalize_ws(b.body) == tu.normalize_ws(line["original"])), None)
            if target is None:
                skipped.append({"text": line["text"], "reason": "Line not found in the document"})
                continue
            self.state["confirmations"][f"edit:{line['id']}"] = {"text": line["text"]}  # the user's own wording
            opt = self.recommender().evaluate({"mode": "replace", "action": "replace", "block_id": target.id,
                                               "proposed_text": line["text"], "strategy": "editor"})
            if not opt["grammar"]["passed"]:
                skipped.append({"text": line["text"], "reason": "; ".join(c["message"] for c in opt["grammar"]["checks"]
                                                                          if c["status"] == "fail")})
                continue
            try:
                new, info, report = self._apply_bytes(opt)
            except WorkflowError as exc:
                skipped.append({"text": line["text"], "reason": str(exc)})
                continue
            v = self.store.add(new, "Edited in editor", {"rec_id": None, "requirement": "Editor change",
                               "action": "replace", "mode": "replace", "block_id": target.id,
                               "target_label": target.section.title(), "original_text": target.body,
                               "new_text": line["text"], "factual_status": "Your own edit",
                               "predicted": {"before": None, "after": None, "points": None},
                               "preservation": report, "applied_at": time.time()})
            line["original"] = line["text"]
            draft["base_version"] = v["id"]
            applied.append(line["text"])
        self.state["draft"] = draft
        self._save()
        return {"applied": applied, "skipped": skipped, "head": self.store.head, **self._editor_payload(draft)}

    def claude_rewrite(self, rec_id: str, override: dict | None) -> dict:
        """Ask Claude for alternative wording of the selected option. Saved as the user's edit only
        when it passes validation; never applied without approval."""
        if not llm.configured():
            raise WorkflowError("Claude is not configured. Set ANTHROPIC_API_KEY and restart the server.", 409)
        rec = self.find_rec(rec_id)
        opt = self._resolve_option(rec, override)
        source = opt.get("original_text") or opt["proposed_text"]
        skill = rec["keyword"] if rec["kind"] == "skill" else None
        new = self.recommender().claude_variant(dict(opt), mode="rewrite" if opt["mode"] == "replace" else "from_notes",
                                                 source=source if opt["mode"] == "replace" else opt["proposed_text"],
                                                 skill=skill)
        if new.get("writer") == "claude":
            self.state["edited"][rec_id] = {"text": new["proposed_text"], "block_id": new["block_id"],
                                            "mode": new["mode"], "selected": int((override or {}).get("selected", 0))}
            self._save()
        return new

    # ------------------------------------------------------------- letters
    def letters(self) -> list[dict]:
        return self.state.get("letters", [])

    def create_letter(self, **opts) -> dict:
        letter = coverletter.generate(self, **opts)
        self.state.setdefault("letters", []).insert(0, letter)
        self._save()
        return letter

    def _letter(self, lid: str) -> dict:
        letter = next((l for l in self.letters() if l["id"] == lid), None)
        if letter is None:
            raise WorkflowError("Cover letter not found", 404)
        return letter

    def update_letter(self, lid: str, greeting: str, paragraphs: list[str], closing: str) -> dict:
        letter = self._letter(lid)
        letter.update(greeting=greeting.strip(), paragraphs=[p.strip() for p in paragraphs if p.strip()],
                      closing=closing.strip(), updated=time.time(), edited_by_user=True)
        _, model, _, _ = self.current()
        letter["validation"] = coverletter.validate_letter(letter, model, self.state["confirmations"], letter.get("notes", ""))
        self._save()
        return letter

    def delete_letter(self, lid: str) -> dict:
        self._letter(lid)
        self.state["letters"] = [l for l in self.letters() if l["id"] != lid]
        self._save()
        return {"deleted": lid}

    def export_letter(self, lid: str, fmt: str) -> tuple[bytes, str, str]:
        letter = self._letter(lid)
        if not letter["validation"]["passed"]:
            raise WorkflowError("Fix the flagged sentences before downloading this letter", 409)
        stem = re.sub(r"[^A-Za-z0-9]+", "_", f"Cover_Letter_{self.state.get('company') or 'job'}").strip("_")
        if fmt == "txt":
            return coverletter.export_text(letter).encode(), f"{stem}.txt", "text/plain; charset=utf-8"
        data, model, _, _ = self.current()
        return (coverletter.export_docx(letter, model, data if self.ext == ".docx" else None), f"{stem}.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def undo(self) -> dict:
        with self.lock:
            try:
                head = self.store.undo()
            except ValueError as exc:
                raise WorkflowError(str(exc), 409)
            return {"head": head}

    def restore(self, vid: int) -> dict:
        with self.lock:
            try:
                return {"head": self.store.restore(int(vid))}
            except StopIteration:
                raise WorkflowError("Unknown version", 404)

    def download(self, vid: int | None) -> tuple[bytes, str]:
        vid = self.store.head if vid is None else int(vid)
        stem = Path(self.store.manifest["filename"]).stem
        name = f"{stem}{'' if vid == 0 else f'_v{vid}'}{self.ext}"
        return self.store.read(vid), name
