"""Recommendation generation.

For every missing or partially matched requirement this module proposes
concrete, located edits to the existing resume. Each option is validated
(grammar + factual support) and scored by simulating the edit on an
in-memory copy of the resume with the same scoring engine. Nothing here
modifies a document.
"""
from __future__ import annotations

import hashlib
import re

import logging

from . import llm
from . import textutil as tu
from .config import COMPONENT_LABELS
from .factual import validate_claims
from .grammar import validate_text
from .jd import JobRequirements
from .model import Block, ResumeModel
from .scoring import ScoreResult, score_delta, score_resume
from .taxonomy import taxonomy

CATEGORY_LABELS = {
    "language": ["language", "programming"],
    "framework": ["framework", "librar", "stack"],
    "database": ["database", "data", "storage"],
    "cloud": ["cloud", "devops", "platform", "infrastructure", "tool"],
    "devops": ["devops", "cloud", "tool", "infrastructure", "platform"],
    "tool": ["tool", "software", "platform"],
    "data": ["data", "machine", "ml", "analytic"],
    "frontend": ["frontend", "front-end", "web", "framework"],
    "architecture": ["architecture", "concept", "practice", "framework", "tool"],
    "practice": ["practice", "method", "concept", "tool"],
    "process": ["method", "process", "practice", "tool"],
    "security": ["security", "tool"],
}

log = logging.getLogger("careercopilot")


def _rid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def _entry_label(model: ResumeModel, b: Block) -> str:
    sec = b.section_title or b.section.title()
    if b.entry_id and b.entry_id in model.entries:
        return f"{sec.title()} › {model.entries[b.entry_id].title.split(' | ')[0][:60]}"
    return sec.title()


def _neighbors(model: ResumeModel, b: Block, exclude_self=True) -> list[str]:
    if b.entry_id:
        sib = [x for x in model.entry_blocks(b.entry_id) if x.kind == "bullet"]
    else:
        sib = [x for x in model.section_blocks(b.section) if x.kind == b.kind]
    return [x.body for x in sib if not (exclude_self and x.id == b.id)]


def _known_words(model: ResumeModel, jd_text: str, extra: str = "") -> set[str]:
    return set(re.findall(r"[A-Za-z][A-Za-z'-]+", model.full_text() + " " + jd_text + " " + extra))


class Recommender:
    def __init__(self, model: ResumeModel, jd: JobRequirements, jd_text: str, weights: dict, base: ScoreResult,
                 confirmations: dict, denials: set, answers: dict):
        self.model, self.jd, self.jd_text, self.weights = model, jd, jd_text, weights
        self.base, self.conf, self.denials, self.answers = base, confirmations, denials, answers
        # The candidate's own written answers are first-hand confirmations of what they describe.
        self.conf_all = dict(confirmations)
        self.conf_all.update({f"answer:{k}": {"text": v.get("text", "")} for k, v in answers.items() if v.get("text")})
        self.tax = taxonomy()
        self.jd_skill_ids = {r.skill_id for r in jd.skills} | {c.skill_id for c in jd.certifications}
        self.jd_terms = set().union(*[set(r.terms) for r in jd.responsibilities]) if jd.responsibilities else set()

    # ------------------------------------------------------------------
    def evaluate(self, option: dict) -> dict:
        """Validate and simulate one option in memory. Never touches a file."""
        m = self.model
        b = m.block(option["block_id"])
        kind = option.get("kind") or ("skills_line" if b.kind == "skills_line" else
                                      ("bullet" if b.kind in ("bullet", "entry_header") else b.kind))
        text = option["proposed_text"].strip()
        if option["mode"] == "replace":
            sim = m.with_replaced(b.id, text)
            neighbors = _neighbors(m, b)
            original = b.body
        else:
            sim = m.with_inserted_after(b.id, text, kind=None if kind != "line" else "entry_header")
            neighbors = _neighbors(m, b, exclude_self=False) if kind == "bullet" else []
            original = None
        current_entry = bool(b.entry_id and m.entries.get(b.entry_id) and m.entries[b.entry_id].current)
        vkw = dict(kind=kind if kind != "line" else "line", neighbors=neighbors, current_entry=current_entry,
                   known_words=_known_words(m, self.jd_text, text), jd_skill_ids=self.jd_skill_ids,
                   jd_terms=self.jd_terms)
        grammar = validate_text(text, original_text=original, **vkw)
        if original is not None:
            # Judge only what the edit changes: problems already present in the user's own line are
            # reported as optional fixes, never a reason to block the edit.
            before = validate_text(original, original_text=original, **vkw)
            pre = {c["name"] for c in before["checks"] if c["status"] == "fail"}
            for c in grammar["checks"]:
                if c["status"] == "fail" and c["name"] in pre:
                    c["status"] = "warn"
                    c["message"] = "Already in your original line (optional fix): " + c["message"]
            grammar["passed"] = not any(c["status"] == "fail" for c in grammar["checks"])
        factual = validate_claims(text, model=m, mode=option["mode"], original_text=original,
                                  confirmations=self.conf_all, denials=self.denials, kind=kind)
        after = score_resume(sim, self.jd, self.weights)
        delta = score_delta(self.base, after)
        if option.get("strategy") == "wording":
            # a wording card only fixes wording; length/relevance notes about the user's own line are noise here
            grammar["checks"] = [c for c in grammar["checks"] if c["name"] not in ("conciseness", "relevance")]
        duplicate = None
        if option["mode"] == "insert_after":
            import difflib
            norm = lambda x: re.sub(r"\W+", " ", x.lower()).strip()
            duplicate = next((x for x in m.blocks if x.kind != "heading" and
                              difflib.SequenceMatcher(None, norm(x.body), norm(text)).ratio() >= 0.85), None)
        option.update(proposed_text=text, kind=kind, grammar=grammar, factual=factual, score=delta,
                      can_apply=grammar["passed"] and factual["applicable"] and duplicate is None,
                      original_text=original, target_label=_entry_label(m, b))
        if duplicate is not None:
            option["blocked_reason"] = ("This text is already in your resume. Use “Replace Existing Text” to change "
                                        "that line instead of adding a second copy.")
            return option
        if not option["can_apply"]:
            reasons = []
            if not grammar["passed"]:
                reasons.append("fix the writing issues marked as errors")
            if not factual["applicable"]:
                reasons.append("confirm the unsupported claims" if factual["status"] == "clarification"
                               else "remove claims you have not confirmed")
            option["blocked_reason"] = "To apply, " + " and ".join(reasons) + "."
        return option

    # ------------------------------------------------------------------
    def _demo_blocks(self) -> list[Block]:
        return [b for b in self.model.blocks if b.section in ("experience", "projects") and b.kind in ("bullet", "paragraph")]

    def _implication_options(self, sid: str) -> list[dict]:
        sk = self.tax.get(sid)
        out = []
        for b in self._demo_blocks():
            found = self.tax.find(b.body)
            if sid in found:
                continue
            for src in found:
                src_sk = self.tax.get(src)
                if sid not in src_sk.implies:
                    continue
                start, end, surface = found[src][0]
                tmpl = src_sk.implied_phrase.get(sid, "{T} ({S})")
                phrase = tmpl.replace("{T}", surface).replace("{S}", sk.name)
                new = b.body[:start] + phrase + b.body[end:]
                out.append({"mode": "replace", "action": "replace", "block_id": b.id, "proposed_text": new,
                            "strategy": "implied",
                            "rationale": f"Your bullet already names {src_sk.name}, which demonstrates {sk.name}. "
                                         f"Naming {sk.name} explicitly lets an ATS match the requirement."})
                break
        return out[:2]

    def _weak_options(self, sid: str) -> list[dict]:
        sk = self.tax.get(sid)
        out = []
        for b in self._demo_blocks():
            if sid in self.tax.ids_in(b.body):
                continue
            for rule in sk.weak_evidence:
                if not re.search(rule["pattern"], b.body, re.I):
                    continue
                rw = rule["rewrite"]
                if rw.get("suffix_clause"):
                    m = re.search(rw["pattern"], b.body, re.I)
                    clause_end = re.search(r"\s+(?:and|while|to)\s+|[,.;]|$", b.body[m.end():])
                    pos = m.end() + clause_end.start()
                    tail = b.body[pos:]
                    connector = "with" if " with " not in b.body[m.start():pos] else "using"
                    new = b.body[:pos].rstrip() + f" {connector} {sk.name}" + tail
                else:
                    new = re.sub(rw["pattern"], rw["replace"], b.body, count=1, flags=re.I)
                if new == b.body:
                    continue
                matched = re.search(rule["pattern"], b.body, re.I).group(0)
                out.append({"mode": "replace", "action": "replace", "block_id": b.id, "proposed_text": new,
                            "strategy": "weak", "weak_term": matched,
                            "rationale": f"Your bullet mentions “{matched}”, which suggests {sk.name} but does "
                                         f"not prove it. Confirm you used {sk.name} here before adding it."})
                break
        return out[:2]

    def _skills_line_option(self, sid: str) -> dict | None:
        sk = self.tax.get(sid)
        lines = [b for b in self.model.blocks if b.kind == "skills_line"]
        if not lines:
            return None
        if any(sid in self.tax.ids_in(b.text) for b in lines):
            return None
        keys = CATEGORY_LABELS.get(sk.category, ["tool"])
        technical = sk.category not in ("soft",)
        candidates = []
        for i, b in enumerate(lines):
            label = b.text.split(":", 1)[0].lower() if ":" in b.text else ""
            if not label:
                prev = self.model.blocks[self.model.blocks.index(b) - 1]
                label = prev.text.lower() if prev.kind == "skills_line" and len(prev.text.split()) <= 3 else ""
            items = self.tax.ids_in(b.text)
            if not items and ("," not in b.text):
                continue  # a label-only cell
            same_cat = sum(1 for x in items if self.tax.get(x).category == sk.category)
            score = 4 * min(same_cat, 3)
            score += 3 if any(kw in label for kw in keys) else 0
            score += 2 if technical and "technical" in label else 0
            # never put a technology on a soft-skills or design-tools line (or vice versa)
            if technical and re.search(r"soft|interpersonal|language(?!s?\s*&)", label) and "programming" not in label:
                score -= 10
            if "design" in label and sk.category not in ("frontend",) and sid not in ("figma", "ui_ux"):
                score -= 6
            if not technical and "soft" in label:
                score += 6
            score += 0.1 * len(items)
            candidates.append((-score, i, b))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (c[0], c[1]))
        b = candidates[0][2]
        text = b.body.rstrip()
        end_punct = text[-1] if text[-1] in ".;" else ""
        core = text[:-1] if end_punct else text
        sep = " | " if " | " in core else (" • " if " • " in core else (", " if "," in core or ":" in core else ", "))
        new = core + sep + sk.name + end_punct if (":" not in core or core.split(":", 1)[1].strip()) else core + " " + sk.name
        return {"mode": "replace", "action": "add", "block_id": b.id, "proposed_text": new, "strategy": "skills_line",
                "rationale": f"Adds {sk.name} to your existing skills list in the same format. A skills-list "
                             f"mention earns keyword credit but only partial evidence credit."}

    def _best_entry_anchor(self, sid: str | None = None, terms: set | None = None) -> list[Block]:
        """Last bullet of each experience/project entry, best match first."""
        anchors = []
        for eid, e in self.model.entries.items():
            if e.section not in ("experience", "projects"):
                continue
            blocks = self.model.entry_blocks(eid)
            bullets = [b for b in blocks if b.kind == "bullet"]
            anchor = bullets[-1] if bullets else blocks[-1]
            text = " ".join(b.text for b in blocks)
            rel = 0
            if sid:
                cat = self.tax.get(sid).category
                rel += sum(1 for s in self.tax.ids_in(text) if self.tax.get(s).category == cat)
            if terms:
                rel += len(tu.content_terms(text) & terms) + 3 * len(tu.content_terms(e.title) & terms)
                if e.section == "projects" and terms & {tu.stem(w) for w in ("project", "game", "app", "application",
                                                                              "website", "built", "developed")}:
                    rel += 1
            # Tie-break toward technical entries, so a tech bullet never lands under e.g. an outreach role.
            tech = sum(1 for s in self.tax.ids_in(text) if self.tax.get(s).category not in ("soft", "process"))
            rel += min(tech, 5) * 0.2
            anchors.append((-rel, -int(e.current), anchor))
        anchors.sort(key=lambda a: (a[0], a[1]))
        return [a[2] for a in anchors]

    # ------------------------------------------------------------------
    def compose_bullet(self, user_text: str, skill_name: str | None, anchor: Block) -> str:
        t = tu.normalize_ws(tu.strip_bullet(user_text)[1])
        t = re.sub(r"^(i|we)\s+(have\s+|had\s+|also\s+)*", "", t, flags=re.I)
        t = re.sub(r"^(yes|yeah|yep)[,.!]?\s*", "", t, flags=re.I)
        if not t:
            return ""
        t = t[0].upper() + t[1:]
        neighbors = _neighbors(self.model, anchor, exclude_self=False)
        entry = self.model.entries.get(anchor.entry_id) if anchor.entry_id else None
        from .grammar import expected_tense
        want = expected_tense(neighbors, bool(entry and entry.current)) or "past"
        fw = tu.first_word(t)
        if tu.verb_tense(fw) and tu.verb_tense(fw) != want:
            repl = tu.to_past(fw) if want == "past" else tu.to_present(fw)
            t = re.sub(rf"^{re.escape(fw)}", repl, t)
        if skill_name and not re.search(re.escape(skill_name), t, re.I):
            t = t.rstrip(".") + f" using {skill_name}"
        # canonical capitalisation of skills
        for sid, spans in self.tax.find(t).items():
            sk = self.tax.get(sid)
            for _, _, surface in spans:
                if surface not in set(sk.aliases) | {sk.name}:
                    t = t.replace(surface, next((a for a in sk.aliases if a.lower() == surface.lower()), sk.name))
        ends = [n.rstrip().endswith(".") for n in neighbors]
        t = t.rstrip(".")
        if ends and sum(ends) > len(ends) / 2:
            t += "."
        return t

    def answer_options(self, text: str, skill_name: str | None, anchor: Block, rationale: str) -> list[dict]:
        """Turn the candidate's description into up to three clean, validated resume lines."""
        from .grammar import expected_tense
        from .phrasing import phrase_bullets
        m = self.model
        neighbours = _neighbors(m, anchor, exclude_self=False)
        entry = m.entries.get(anchor.entry_id) if anchor.entry_id else None
        tense = expected_tense(neighbours, bool(entry and entry.current)) or "past"
        ends = [n.rstrip().endswith(".") for n in neighbours if n.strip()]
        sid_for = next((k for k, v in self.tax.skills.items() if v.name == skill_name), None) if skill_name else None
        lines = phrase_bullets(text, skill=skill_name, tense=tense, confirmed_skill_id=sid_for, period=(sum(ends) > len(ends) / 2) if ends else True,
                               entry_org=entry.title if entry else "", neighbors=neighbours,
                               current_entry=bool(entry and entry.current), known_words=_known_words(m, self.jd_text, text),
                               jd_skill_ids=self.jd_skill_ids, jd_terms=self.jd_terms)
        opts = [{"mode": "insert_after", "action": "add", "block_id": anchor.id, "proposed_text": line,
                 "strategy": "confirmed_bullet", "writer": "phraser", "rationale": rationale} for line in lines]
        evaluated = [self.evaluate(o) for o in opts]
        if not evaluated and llm.configured():
            # The rules could not find an action in the answer: let the AI writer phrase it from the answer.
            base = {"mode": "insert_after", "action": "add", "block_id": anchor.id, "proposed_text": text,
                    "kind": "bullet", "strategy": "confirmed_bullet", "writer": "phraser", "rationale": rationale}
            ai = self.claude_variant(self.evaluate(base), mode="from_notes", source=text, skill=skill_name)
            if ai.get("writer") == "ai" and ai["can_apply"]:
                evaluated = [ai]
        return evaluated

    def claude_variant(self, opt: dict, *, mode: str, source: str, skill: str | None) -> dict:
        """Ask the AI writer (Groq or Claude) for better wording; keep it only if it passes the same validation
        and adds no new claims. Returns the option (the AI version when accepted). Never raises."""
        opt.setdefault("writer", "template")
        if not llm.configured():
            return opt
        m = self.model
        b = m.block(opt["block_id"])
        neighbours = _neighbors(m, b, exclude_self=opt["mode"] == "replace")
        entry = m.entries.get(b.entry_id) if b.entry_id else None
        from .grammar import expected_tense
        tense = expected_tense(neighbours, bool(entry and entry.current)) or "past"
        jd_excerpt = "\n".join(r.jd_text for r in self.jd.skills[:12])
        resume = "\n".join(x.text for x in m.blocks if x.kind != "contact")
        try:
            text = llm.write_bullet(resume_text=resume, neighbours=neighbours, tense=tense, skill=skill,
                                    source_text=source, mode=mode, jd_excerpt=jd_excerpt)
        except llm.LLMUnavailable as exc:
            opt["writer_note"] = f"{exc}. Showing the built-in draft."
            return opt
        text = llm.finish(text, kind="bullet", tense=tense)
        problems = llm.grounding_problems(text, [source, resume, skill or "", opt.get("original_text") or ""])
        if problems:
            opt["writer_note"] = (f"{llm.label()}'s draft added things you didn't state ({'; '.join(problems)}), "
                                  "so the built-in draft is shown.")
            return opt
        cand = self.evaluate(dict(opt, proposed_text=text, writer="ai", writer_label=llm.label()))
        lower = opt.get("can_apply") and cand["score"]["points"] < opt["score"]["points"]
        if (cand["can_apply"] or not opt.get("can_apply")) and not lower:
            cand["template_text"] = opt["proposed_text"]
            return cand
        if lower:
            opt["writer_note"] = (f"{llm.label()}'s wording would score lower ({cand['score']['points']:+.1f} vs "
                                  f"{opt['score']['points']:+.1f} pts), so the higher-scoring draft is shown.")
            return opt
        opt["writer_note"] = f"{llm.label()}'s draft did not pass the factual or grammar checks, so the built-in draft is shown."
        return opt

    # ------------------------------------------------------------------
    def why(self, req: dict) -> str:
        imp = "required" if req["importance"] == "required" else "preferred (nice-to-have)"
        if req["type"] == "skill":
            sk = self.tax.get(req["skill_id"])
            jr = next((r for r in self.jd.skills if r.skill_id == req["skill_id"]), None)
            mentions = f" and is mentioned {jr.mentions} time(s)" if jr else ""
            lvl = {"none": "Your resume does not mention it, so it earns no credit for keyword coverage (30%) or contextual relevance (25%).",
                   "listed": "It appears only in a skills list, which earns keyword credit but only 35% of the contextual-relevance credit because no experience shows you used it.",
                   "implied": f"Your experience implies it (via {self.tax.get(req['via']).name if req.get('via') else 'a related skill'}) but never names it, so ATS keyword matching misses it.",
                   "contextual": "It appears in your summary or a role's tech line, but not in an achievement bullet."}
            if req.get("example_only"):
                others = ", ".join(self.tax.get(a).name for a in req.get("alternatives", []))
                return (f"The job names {sk.name} as one option in a list{f' (with {others})' if others else ''}, "
                        f"so any one of them is enough and it counts for half weight. Only add it if you have "
                        f"really used it; otherwise skip it — this is not a hard requirement.")
            return f"{sk.name} is a {imp} {sk.category} skill in this job description{mentions}. " + lvl.get(req["evidence_level"], "")
        if req["type"] == "certification":
            return f"The job lists {req['name']} as {imp}. Certifications count toward the education and certifications component (10%)."
        if req["type"] == "responsibility":
            return ("This is a core responsibility of the role. None of your bullets clearly shows comparable work, "
                    "which lowers experience and responsibility alignment (20%).")
        return ""

    def build(self) -> list[dict]:
        recs = []
        for req in self.base.requirements:
            if req["status"] == "met":
                continue
            if req["type"] == "skill":
                if self.tax.get(req["skill_id"]).generic:
                    continue  # broad terms are shown through concrete skills; no keyword edit needed
                recs.append(self._skill_rec(req))
            elif req["type"] == "certification":
                recs.append(self._cert_rec(req))
        resp = sorted([r for r in self.base.requirements if r["type"] == "responsibility" and r["status"] != "met"],
                      key=lambda r: r["credit"])[:2]
        for r in resp:
            recs.append(self._resp_rec(r))
        for r in self.base.requirements:
            if r["type"] in ("years", "education") and r["status"] != "met":
                recs.append(self._gap_card(r))
        recs.extend(self._wording_recs())
        recs = [r for r in recs if r]
        order = {"required": 0, "preferred": 1}
        recs.sort(key=lambda r: (r["kind"] == "wording", r["kind"] == "gap", order.get(r["requirement"]["importance"], 2),
                                 -max([o["score"]["points"] for o in r["options"]] or [0])))
        return recs

    def _wording_recs(self) -> list[dict]:
        """One card per resume line whose wording or grammar can be made professional."""
        from .polish import scan, _tense_for
        out = []
        findings = scan(self.model, _known_words(self.model, self.jd_text))
        ai_text = self._ai_polish(findings, _tense_for)
        by_block = {f["block_id"]: f for f in findings}
        for bid, text in ai_text.items():
            if bid not in by_block:   # the AI found a line the rules missed
                b = self.model.block(bid)
                by_block[bid] = {"block_id": bid, "section": b.section, "kind": b.kind, "original": b.body,
                                 "improved": b.body, "issues": ["Unclear or unprofessional phrasing"]}
        findings = [by_block[b.id] for b in self.model.blocks if b.id in by_block]
        for f in findings:
            req = {"id": f"wording:{f['block_id']}", "type": "wording", "name": "Wording & grammar",
                   "importance": "polish", "jd_text": "", "status": "partial"}
            rec = self._base_rec(req, "wording")
            rec["keyword"] = "Wording & grammar"
            rec["why"] = "Professional, correctly spelled wording is easier for ATS parsers and recruiters to read. " \
                         "Issues found: " + "; ".join(dict.fromkeys(f["issues"]))
            opt = {"mode": "replace", "action": "replace", "block_id": f["block_id"], "proposed_text": f["improved"],
                   "strategy": "wording", "writer": "phraser",
                   "rationale": "Corrects spelling, punctuation, technical terms and weak or informal wording. "
                                "Your facts are unchanged."}
            opt = self.evaluate(opt)
            ai = ai_text.get(f["block_id"])
            if ai and ai != opt["proposed_text"]:
                cand = self.evaluate(dict(opt, proposed_text=ai, writer="ai", writer_label=llm.label(),
                                          rationale=f"Rewritten by {llm.label()} in clear, professional English, then "
                                                    "checked: grammar, no new skills or numbers, no invented details."))
                better = cand["score"]["points"] >= opt["score"]["points"]   # ties go to the AI wording
                if cand["can_apply"] and cand["factual"]["status"] == "stylistic" and better:
                    cand["template_text"] = opt["proposed_text"]
                    opt = cand
            if opt["proposed_text"].strip() == f["original"].strip():
                continue
            if opt["score"]["points"] <= 0:
                continue   # only changes that raise the score are offered
            if opt.get("writer") == "ai":
                rec["why"] = (f"{llm.label()} rewrote this line in clearer, more professional English. "
                              + ("Issues found: " + "; ".join(dict.fromkeys(f["issues"])) if f["issues"] else ""))
            if opt["can_apply"]:
                rec["options"] = [opt]
                rec["selected"] = 0
                out.append(rec)
        return out

    def _ai_polish(self, findings: list[dict], tense_for) -> dict[str, str]:
        """One batched AI request correcting the English of the flagged lines plus every bullet and paragraph.
        Returns {block_id: text} for lines the AI changed and that add no new claims."""
        if not llm.configured():
            return {}
        flagged = {f["block_id"] for f in findings}
        lines = []
        for b in self.model.blocks:
            body = b.body
            if b.kind not in ("bullet", "paragraph") and b.id not in flagged:
                continue
            if b.section == "contact" or b.kind in ("heading", "contact") or body[:1].islower() or len(body.split()) < 4:
                continue
            lines.append({"id": b.id, "kind": b.kind, "tense": tense_for(self.model, b) or "", "text": body})
        try:
            out = llm.polish_lines(lines[:45])
        except llm.LLMUnavailable as exc:
            log.info("AI polish unavailable: %s", exc)
            return {}
        kept = {}
        for bid, text in out.items():
            blk = self.model.block(bid)
            orig = blk.body
            text = llm.finish(text, kind=blk.kind, tense=tense_for(self.model, blk))
            if tu.normalize_ws(text) == tu.normalize_ws(orig):
                continue
            if llm.grounding_problems(text, [orig], max_new=1):  # polishing may add at most one new word
                continue
            kept[bid] = tu.normalize_ws(text)
        return kept

    def _base_rec(self, req: dict, kind: str) -> dict:
        return {"id": _rid(req["id"]), "key": req["id"], "kind": kind, "requirement": req,
                "keyword": req["name"], "why": self.why(req), "match_status": req["status"],
                "options": [], "question": None, "status": "pending", "learning_project": None,
                "current_score": self.base.overall}

    def _skill_rec(self, req: dict) -> dict:
        sid = req["skill_id"]
        sk = self.tax.get(sid)
        rec = self._base_rec(req, "skill")
        if sid in self.denials:
            rec.update(status="unsupported", learning_project=sk.learning_project,
                       gap=f"You told us you have not used {sk.name} yet, so it was not added to your resume. "
                           f"Consider a learning project to build real evidence.")
            return rec
        options = self._implication_options(sid)
        weak = self._weak_options(sid)
        options += weak
        sl = self._skills_line_option(sid)
        ev = self.base.evidence.get(sid)
        if sl and (ev is not None or sid in self.conf):
            options.append(sl)
        answer = self.answers.get(req["id"])
        for o in options:
            self.evaluate(o)
        if answer and answer.get("text"):
            # Place the new line under the role/project the candidate's own answer is about.
            anchors = [self.model.block(answer["anchor_id"])] if answer.get("anchor_id") else \
                self._best_entry_anchor(None, terms=tu.content_terms(answer["text"]))
            if anchors:
                phrased = self.answer_options(answer["text"], sk.name, anchors[0],
                                              f"Written from your description of how you used {sk.name}.")
                if phrased:
                    phrased[0] = self.claude_variant(phrased[0], mode="from_notes", source=answer["text"], skill=sk.name)
                else:
                    rec["answer_error"] = ("We couldn't find a concrete action in your answer. Describe what you did, "
                                           "starting with a verb, e.g. 'Built …', 'Tested …', 'Presented …'.")
                options = phrased + options
            if sl and sl not in options:
                options.append(self.evaluate(sl))
        # A keyword option that cannot raise the score (e.g. the keyword is already counted) is not offered.
        # Only score gains are offered; a bullet written from the candidate's own answer is also kept when it
        # is neutral (never when it would lower the score), so a confirmed answer always produces a card.
        useful = [o for o in options if o.get("score", {}).get("points", 0) > 0
                  or (o.get("strategy") == "confirmed_bullet" and o.get("score", {}).get("points", 0) >= 0)]
        if options and not useful and ev is not None and ev.literal:
            rec.update(status="info", gap=f"{sk.name} already appears in your resume, so adding it again would not "
                                          f"change the score. Showing it inside an achievement would.")
        options = useful
        rec["options"] = options
        needs_conf = (sid not in self.conf) and (not options or all(o["factual"]["status"] == "clarification" for o in options))
        if needs_conf:
            weak_terms = [o["weak_term"] for o in options if o.get("strategy") == "weak"]
            if weak_terms:
                prompt = f"Your resume mentions “{weak_terms[0]}”. Did that work involve {sk.name}?"
            elif ev is not None and ev.literal:
                prompt = (f"Your resume lists {sk.name}, but no experience or project shows how you used it. "
                          f"Have you used {sk.name} in a role or project?")
            elif sk.category in ("practice", "process", "soft", "architecture"):
                prompt = (f"The job asks for {sk.name.lower() if sk.category != 'soft' and not any(c.isupper() for c in sk.name[1:]) else sk.name}, and your resume "
                          f"doesn't show it yet. Have you done this in a project, internship or role?")
            else:
                prompt = (f"The job asks for {sk.name}, and your resume shows no {sk.name} experience. "
                          f"Have you used {sk.name}?")
            rec["question"] = {"type": "skill_usage", "skill_id": sid, "text": prompt,
                               "detail_prompt": f"If yes, briefly describe what you did with {sk.name} and where "
                                                f"(role or project). We will draft a bullet from your words.",
                               "anchors": [{"id": a.id, "label": _entry_label(self.model, a)}
                                           for a in self._best_entry_anchor(sid)]}
            rec["status"] = "needs_clarification"
        rec["selected"] = 0
        return rec

    def _cert_rec(self, req: dict) -> dict:
        sid = req["skill_id"]
        sk = self.tax.get(sid)
        rec = self._base_rec(req, "certification")
        if sid in self.denials:
            rec.update(status="unsupported", learning_project=sk.learning_project,
                       gap=f"You do not hold {sk.name}; it will not be added.")
            return rec
        answer = self.answers.get(req["id"])
        target = [b for b in self.model.blocks if b.section == "certifications" and b.kind != "heading"] or \
                 [b for b in self.model.blocks if b.section == "education" and b.kind != "heading"]
        if answer and answer.get("text") and target:
            text = tu.normalize_ws(answer["text"]).rstrip(".")
            o = {"mode": "insert_after", "action": "add", "block_id": target[-1].id, "proposed_text": text,
                 "kind": "line", "strategy": "certification",
                 "rationale": "Adds the certification you confirmed, in the style of the neighbouring line."}
            rec["options"] = [self.evaluate(o)]
        else:
            rec["status"] = "needs_clarification"
            rec["question"] = {"type": "certification", "skill_id": sid,
                               "text": f"Do you hold {sk.name}?",
                               "detail_prompt": "If yes, enter the exact certification name and year "
                                                "(e.g. “AWS Certified Developer – Associate, 2024”).",
                               "anchors": []}
        rec["selected"] = 0
        return rec

    def _resp_rec(self, req: dict) -> dict:
        rec = self._base_rec(req, "responsibility")
        rec["keyword"] = "Responsibility"
        answer = self.answers.get(req["id"])
        if req["id"] in self.denials:
            rec.update(status="unsupported", gap="You indicated you have not done this; nothing will be added.")
            return rec
        terms = set(tu.content_terms(req["jd_text"]))
        anchors = self._best_entry_anchor(terms=terms)
        if answer and answer.get("text") and anchors:
            anchor = self.model.block(answer["anchor_id"]) if answer.get("anchor_id") else anchors[0]
            phrased = self.answer_options(answer["text"], None, anchor, "Written from your description.")
            if phrased:
                phrased[0] = self.claude_variant(phrased[0], mode="from_notes", source=answer["text"], skill=None)
                rec["options"] = phrased
            else:
                rec["answer_error"] = ("We couldn't find a concrete action in your answer. Describe what you did, "
                                       "starting with a verb, e.g. 'Built …', 'Tested …', 'Presented …'.")
        if not rec["options"]:
            rec["status"] = "needs_clarification"
            rec["question"] = {"type": "responsibility", "skill_id": None,
                               "text": f"The role emphasises: “{req['jd_text']}”. Have you done comparable work?",
                               "detail_prompt": "If yes, describe what you did, the scale and the result. "
                                                "We will draft a bullet from your words.",
                               "anchors": [{"id": a.id, "label": _entry_label(self.model, a)} for a in anchors]}
        rec["selected"] = 0
        return rec

    def _gap_card(self, req: dict) -> dict:
        rec = self._base_rec(req, "gap")
        if req["type"] == "years":
            rec["gap"] = (f"The job asks for {req['name']}; your dated roles add up to {req.get('value', 0):g} years. "
                          "Dates cannot be changed to close this gap. If you have relevant experience that is not "
                          "listed (freelance, research, internships), add it with accurate dates.")
        else:
            rec["gap"] = ("The degree requirement is not fully met by the education section as written. If your "
                          "degree or field is missing or abbreviated, edit the education line; otherwise address it in "
                          "a cover letter.")
        rec["status"] = "info"
        return rec
