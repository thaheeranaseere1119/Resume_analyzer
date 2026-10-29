"""Deterministic compatibility scoring engine.

Overall score = sum(component_score * weight) over applicable components,
normalised by the applicable weight, scaled to 0-100 and clamped.

The same `score_resume` function is used for:
  * the current score,
  * the predicted score of a proposed edit (on an in-memory ResumeModel), and
  * the actual score after an edit (on a model re-parsed from the exported file).
No LLM is involved, so a score increase can never be invented.
"""
from __future__ import annotations

import functools

import re
from dataclasses import asdict, dataclass, field

from . import textutil as tu
from .config import COMPONENT_LABELS, EVIDENCE_LEVELS, IMPORTANCE_WEIGHTS, METHODOLOGY_VERSION
from .jd import DEGREE_LEVELS, FIELDS, JobRequirements
from .model import Block, ResumeModel
from .taxonomy import taxonomy

DEMONSTRATING_SECTIONS = ("experience", "projects")


@dataclass
class Evidence:
    skill_id: str
    level: str
    block_id: str | None = None
    quote: str = ""
    via: str | None = None  # implying skill id for "implied"
    mentions: int = 0       # informational only; never adds credit
    literal: bool = False   # the skill itself (or a synonym) appears in the text


@dataclass
class ComponentScore:
    key: str
    label: str
    weight: float
    applicable: bool
    score: float            # 0..1
    details: dict = field(default_factory=dict)


@dataclass
class ScoreResult:
    overall: float
    components: dict[str, ComponentScore]
    requirements: list[dict]
    evidence: dict[str, Evidence]
    methodology: str = METHODOLOGY_VERSION
    weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "methodology": self.methodology,
            "weights": self.weights,
            "components": {k: asdict(c) for k, c in self.components.items()},
            "requirements": self.requirements,
            "evidence": {k: asdict(v) for k, v in self.evidence.items()},
        }


# ---------------------------------------------------------------------------
# Evidence quality
# ---------------------------------------------------------------------------
def is_list_like(text: str) -> bool:
    """True for keyword lists such as 'Java, REST API, SQL developed application'."""
    body = tu.strip_bullet(text)[1]
    head = re.split(r"\b(?:" + "|".join(tu.ACTION_VERBS_PAST) + r")\b", body, maxsplit=1, flags=re.I)[0]
    segments = [s.strip() for s in head.split(",")]
    short = [s for s in segments if s and len(s.split()) <= 3]
    return len(segments) >= 3 and len(short) >= 3


def has_verb(text: str) -> bool:
    for w in tu.words(text):
        lw = w.lower()
        if tu.verb_tense(lw) or (len(lw) > 4 and (lw.endswith("ed") or lw.endswith("ing"))):
            return True
    return False


def is_meaningful_sentence(text: str) -> bool:
    body = tu.strip_bullet(text)[1]
    ws = tu.words(body)
    if len(ws) < 6 or is_list_like(body) or not has_verb(body):
        return False
    skill_chars = sum(e - s for spans in taxonomy().find(body).values() for s, e, _ in spans)
    return skill_chars <= 0.5 * len(body)


def _block_level(b: Block) -> str:
    if b.kind in ("heading", "contact"):
        return "none"
    if b.section in DEMONSTRATING_SECTIONS:
        if b.kind in ("bullet", "paragraph") and is_meaningful_sentence(b.text):
            return "demonstrated"
        return "contextual" if b.kind in ("entry_header", "paragraph") and not is_list_like(b.text) else "listed"
    if b.section == "summary":
        return "contextual" if is_meaningful_sentence(b.text) else "listed"
    return "listed"


def collect_evidence(model: ResumeModel) -> dict[str, Evidence]:
    tax = taxonomy()
    ev: dict[str, Evidence] = {}
    for b in model.blocks:
        level = _block_level(b)
        if level == "none":
            continue
        for sid, spans in tax.find(b.text).items():
            cur = ev.get(sid)
            if cur is None:
                ev[sid] = Evidence(sid, level, b.id, b.body, mentions=len(spans), literal=True)
                continue
            cur.mentions += len(spans)
            if EVIDENCE_LEVELS[level] > EVIDENCE_LEVELS[cur.level]:
                cur.level, cur.block_id, cur.quote = level, b.id, b.body
    # Implication: e.g. demonstrated PostgreSQL work implies SQL.
    for target, sources in tax.implied_by.items():
        cur = ev.get(target)
        if cur and EVIDENCE_LEVELS[cur.level] >= EVIDENCE_LEVELS["implied"]:
            continue
        for src in sources:
            s = ev.get(src)
            if s and s.level == "demonstrated":
                if cur is None:
                    ev[target] = Evidence(target, "implied", s.block_id, s.quote, via=src, mentions=0)
                else:
                    cur.level, cur.block_id, cur.quote, cur.via = "implied", s.block_id, s.quote, src
                break
    return ev


def literal_present(ev: Evidence | None) -> bool:
    return ev is not None and ev.literal


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def _skill_components(jd: JobRequirements, ev: dict[str, Evidence]):
    reqs, total_w, cov, ctx = [], 0.0, 0.0, 0.0
    for r in jd.skills:
        w = IMPORTANCE_WEIGHTS[r.importance]
        e = ev.get(r.skill_id)
        present = literal_present(e)
        level = e.level if e else "none"
        if taxonomy().get(r.skill_id).generic and level == "implied":
            # Broad skills ("programming") are evidenced by concrete ones (Python, Java); no keyword needed.
            present, level = True, "demonstrated"
        via = e.via if e else None
        if r.example_only:
            # One option in an "X, Y or similar" list: half weight, and any listed alternative satisfies it.
            w *= 0.5
            if not present:
                alt = max((ev[a] for a in r.alternatives if literal_present(ev.get(a))),
                          key=lambda x: EVIDENCE_LEVELS[x.level], default=None)
                if alt is not None:
                    e, present, level = alt, True, alt.level
                    via = next(a for a in r.alternatives if ev.get(a) is alt)
        total_w += w
        cov += w * (1.0 if present else 0.0)
        ctx += w * EVIDENCE_LEVELS[level]
        status = "met" if level == "demonstrated" else ("missing" if level == "none" else "partial")
        if r.example_only and via in r.alternatives:
            status = "met"  # an equivalent option is already on the resume
        reqs.append({"id": r.id, "type": "skill", "skill_id": r.skill_id, "name": r.name,
                     "importance": r.importance, "jd_text": r.jd_text, "evidence_level": level,
                     "status": status, "block_id": e.block_id if e else None, "via": via,
                     "example_only": r.example_only, "alternatives": r.alternatives})
    coverage = ComponentScore("keyword_coverage", COMPONENT_LABELS["keyword_coverage"], 0, bool(jd.skills),
                              cov / total_w if total_w else 0.0,
                              {"matched_weight": round(cov, 3), "total_weight": total_w})
    context = ComponentScore("contextual_relevance", COMPONENT_LABELS["contextual_relevance"], 0, bool(jd.skills),
                             ctx / total_w if total_w else 0.0,
                             {"evidence_weight": round(ctx, 3), "total_weight": total_w})
    return coverage, context, reqs


def _resp_terms(text: str) -> set[str]:
    return tu.content_terms(text) | {"skill:" + s for s in taxonomy().ids_in(text)}


def _experience_component(model: ResumeModel, jd: JobRequirements):
    reqs = []
    parts: list[float] = []
    details: dict = {}
    if jd.min_years:
        ranges = [(a, b) for e in model.entries.values() if e.section == "experience" for a, b, _ in e.ranges]
        years = tu.total_years(ranges)
        ys = min(1.0, years / jd.min_years)
        parts.append(ys)
        details.update(resume_years=years, required_years=jd.min_years, years_score=round(ys, 3))
        reqs.append({"id": "years", "type": "years", "name": f"{jd.min_years:g}+ years of experience",
                     "jd_text": jd.years_text, "status": "met" if ys >= 1 else ("partial" if ys > 0 else "missing"),
                     "importance": "required", "value": years})
    if jd.responsibilities:
        cands = [b for b in model.blocks if b.section in DEMONSTRATING_SECTIONS + ("summary",)
                 and b.kind in ("bullet", "paragraph")]
        cand_terms = [(b, _resp_terms(b.text)) for b in cands]
        credits = []
        for r in jd.responsibilities:
            terms = set(r.terms) | {"skill:" + s for s in taxonomy().ids_in(r.text)}
            best, best_b = 0.0, None
            for b, bt in cand_terms:
                recall = len(terms & bt) / len(terms)
                if recall > best:
                    best, best_b = recall, b
            credit = min(1.0, best / 0.6)
            credits.append(credit)
            reqs.append({"id": r.id, "type": "responsibility", "name": r.text, "jd_text": r.text,
                         "importance": "required", "credit": round(credit, 3),
                         "status": "met" if credit >= 0.99 else ("partial" if credit >= 0.34 else "missing"),
                         "block_id": best_b.id if best_b else None})
        rs = sum(credits) / len(credits)
        parts.append(rs)
        details["responsibility_score"] = round(rs, 3)
    applicable = bool(parts)
    return ComponentScore("experience_alignment", COMPONENT_LABELS["experience_alignment"], 0, applicable,
                          sum(parts) / len(parts) if parts else 0.0, details), reqs


def resume_degree(model: ResumeModel) -> tuple[int, list[str]]:
    from .jd import fields_in
    level, fields = 0, []
    for b in model.blocks:
        if b.section == "summary":
            fields += fields_in(b.text)
        if b.section != "education":
            continue
        for lv, pat in DEGREE_LEVELS:
            if lv == 1 and not re.search(r"bachelor|b\.s|bsc|b\.sc|b\.e|b\.?tech|b\.a\b|\bBE\b|\bBCA\b", b.text, re.I):
                continue
            if re.search(pat, b.text, re.I):
                level = max(level, lv)
                break
        fields += fields_in(b.text)
    return level, fields


def _education_component(model: ResumeModel, jd: JobRequirements, ev: dict[str, Evidence]):
    parts, reqs, details = [], [], {}
    if jd.degree_level:
        level, fields = resume_degree(model)
        s = 1.0 if level >= jd.degree_level else (0.5 if level == jd.degree_level - 1 and level > 0 else 0.0)
        if jd.degree_fields:
            field_ok = any(f in fields for f in jd.degree_fields) or "related field" in jd.degree_text.lower() and level > 0
            s = 0.7 * s + 0.3 * (1.0 if field_ok else 0.0)
        parts.append(s)
        details.update(resume_degree_level=level, required_degree_level=jd.degree_level, degree_score=round(s, 3))
        reqs.append({"id": "degree", "type": "education", "name": "Degree requirement", "jd_text": jd.degree_text,
                     "importance": jd.degree_importance, "status": "met" if s >= 0.99 else ("partial" if s > 0 else "missing")})
    if jd.certifications:
        tw = sum(IMPORTANCE_WEIGHTS[c.importance] for c in jd.certifications)
        got = 0.0
        for c in jd.certifications:
            present = literal_present(ev.get(c.skill_id))
            got += IMPORTANCE_WEIGHTS[c.importance] * present
            reqs.append({"id": c.id, "type": "certification", "skill_id": c.skill_id, "name": c.name,
                         "jd_text": c.jd_text, "importance": c.importance, "status": "met" if present else "missing"})
        parts.append(got / tw)
        details["certification_score"] = round(got / tw, 3)
    return ComponentScore("education_certifications", COMPONENT_LABELS["education_certifications"], 0,
                          bool(parts), sum(parts) / len(parts) if parts else 0.0, details), reqs


GRAMMAR_ERRORS = ("subject_verb_agreement", "articles", "repeated_word", "professional_tone")


@functools.lru_cache(maxsize=4096)
def _language_clean(body: str, kind: str) -> bool:
    """A line is clean when the wording rules have nothing to fix and it has no grammar errors
    (subject-verb agreement, articles, repeated words, unprofessional tone)."""
    from .grammar import validate_text
    from .polish import improve_line
    if improve_line(body, kind=kind, tense="past") != body:
        return False
    if kind == "skills_line":
        return True
    checks = validate_text(body, kind="bullet" if kind == "bullet" else "paragraph")["checks"]
    return not any(c["status"] == "fail" and c["name"] in GRAMMAR_ERRORS for c in checks)


def _formatting_component(model: ResumeModel):
    checks = {}
    if model.scanned:
        conf = (model.ocr_confidence or 0) / 100.0
        checks["text_extractable"] = (0.25, 0.5 * conf)
    else:
        checks["text_extractable"] = (0.25, 1.0)
    text = "\n".join(b.text for b in model.blocks)
    has_email, has_phone = bool(tu.EMAIL_RE.search(text)), bool(tu.PHONE_RE.search(text))
    checks["contact_info"] = (0.15, (has_email + has_phone) / 2)
    sections = {b.section for b in model.blocks if b.kind == "heading"}
    std = [s for s in ("experience", "education", "skills") if s in sections]
    checks["standard_headings"] = (0.20, len(std) / 3)
    exp_entries = [e for e in model.entries.values() if e.section == "experience"]
    checks["parseable_dates"] = (0.15, (sum(1 for e in exp_entries if e.ranges) / len(exp_entries)) if exp_entries else 0.0)
    bullets = [b for b in model.blocks if b.kind == "bullet"]
    good = [b for b in bullets if 6 <= len(b.body.split()) <= 40]
    checks["bullet_length"] = (0.15, len(good) / len(bullets) if bullets else 0.5)
    if model.page_count:
        checks["page_count"] = (0.10, 1.0 if model.page_count <= 2 else 0.5)
    # Language quality: share of content lines with no spelling/punctuation/casing/wording problems
    # (the same rules the "Wording & grammar" fixes apply), so approved fixes are reflected in the score.
    content = [b for b in model.blocks if b.kind in ("bullet", "paragraph", "skills_line")
               and b.section != "contact" and not b.body[:1].islower() and len(b.body.split()) >= 3]
    if content:
        clean = sum(1 for b in content if _language_clean(b.body, b.kind))
        checks["language_quality"] = (0.15, clean / len(content))
    tw = sum(w for w, _ in checks.values())
    score = sum(w * v for w, v in checks.values()) / tw
    return ComponentScore("formatting_parsing", COMPONENT_LABELS["formatting_parsing"], 0, True, score,
                          {k: {"weight": w, "score": round(v, 3)} for k, (w, v) in checks.items()})


def score_resume(model: ResumeModel, jd: JobRequirements, weights: dict[str, float]) -> ScoreResult:
    ev = collect_evidence(model)
    coverage, context, skill_reqs = _skill_components(jd, ev)
    exp, exp_reqs = _experience_component(model, jd)
    edu, edu_reqs = _education_component(model, jd, ev)
    fmt = _formatting_component(model)
    comps = {c.key: c for c in (coverage, context, exp, edu, fmt)}
    applicable_w = sum(weights[k] for k, c in comps.items() if c.applicable)
    total = 0.0
    for k, c in comps.items():
        c.weight = weights[k]
        c.score = round(max(0.0, min(1.0, c.score)), 4)
        if c.applicable:
            total += c.score * weights[k]
    overall = round(max(0.0, min(100.0, 100.0 * total / applicable_w)), 1) if applicable_w else 0.0
    return ScoreResult(overall, comps, skill_reqs + exp_reqs + edu_reqs, ev, METHODOLOGY_VERSION, dict(weights))


def score_delta(before: ScoreResult, after: ScoreResult) -> dict:
    """Absolute point change is primary; relative % is labelled separately."""
    diff = round(after.overall - before.overall, 1)
    rel = round(100.0 * diff / before.overall, 2) if before.overall else None
    comps = {}
    for k, c in before.components.items():
        a = after.components[k]
        if c.applicable:
            comps[k] = {"label": c.label, "before": round(100 * c.score, 1), "after": round(100 * a.score, 1),
                        "points": round(100 * (a.score - c.score) * c.weight /
                                        sum(x.weight for x in before.components.values() if x.applicable), 2)}
    before_status = {r["id"]: r["status"] for r in before.requirements}
    changed = [{"id": r["id"], "name": r["name"], "from": before_status.get(r["id"]), "to": r["status"]}
               for r in after.requirements if before_status.get(r["id"]) != r["status"]]
    return {"before": before.overall, "after": after.overall, "points": diff, "relative_percent": rel,
            "components": comps, "requirement_changes": changed}
