"""Resume-wide wording & grammar scan.

Finds unprofessional, weak or incorrect wording in every resume line and
proposes a corrected line. Only safe, meaning-preserving rewrites are made:
canonical technical spelling, spacing/punctuation, spelling, articles, repeated
words, casing, informal words, and weak openers such as "Responsible for
managing X" -> "Managed X". No skill, number or claim is ever added.
"""
from __future__ import annotations

import re

from . import textutil as tu
from .grammar import validate_text
from .model import Block, ResumeModel
from .phrasing import _acronyms, _base_verb, _canon_skills, _inflect, _sentences

INFORMAL = {
    r"\blots of\b": "many", r"\ba lot of\b": "many", r"\btons of\b": "many", r"\bkinda\b": "somewhat",
    r"\bgonna\b": "going to", r"\bwanna\b": "want to", r"\bstuff\b": "work", r"\bthings\b": "tasks",
    r"\bawesome\b": "excellent", r"\bcool\b": "effective", r"\bbasically\b": "", r"\breally\b": "",
    r"\bvery\b": "", r"\betc\.?": "and more",
}
WEAK_OPENERS = [
    # "Responsible for managing X" -> "Managed X"; "Worked on developing X" -> "Developed X"
    re.compile(r"^(?:was |were )?(?:responsible for|in charge of|tasked with|involved in|worked on|working on|"
               r"duties included|helped (?:to |with )?)\s+([a-z]+ing|[a-z]+)\b", re.I),
]
SPACING = [
    (re.compile(r"\s+,"), ","), (re.compile(r",(?=\S)"), ", "), (re.compile(r"\s+\."), "."),
    (re.compile(r"\(\s+"), "("), (re.compile(r"\s+\)"), ")"), (re.compile(r":(?=[A-Za-z])"), ": "),
    (re.compile(r"(?<=[A-Za-z0-9])\((?=[a-z])"), " ("), (re.compile(r" {2,}"), " "),
]


def _fix_spacing(t: str) -> str:
    for pat, rep in SPACING:
        t = pat.sub(rep, t)
    return t.strip()


def _fix_informal(t: str) -> str:
    for pat, rep in INFORMAL.items():
        t = re.sub(pat, rep, t, flags=re.I)
    return re.sub(r" {2,}", " ", t).strip()


def _fix_weak_opener(sentence: str, tense: str) -> str:
    for pat in WEAK_OPENERS:
        m = pat.match(sentence)
        if m:
            base = _base_verb(m.group(1))
            if base:
                rest = sentence[m.end():]
                verb = _inflect(base, tense)
                return verb[0].upper() + verb[1:] + rest
    return sentence


def _fix_midsentence_caps(t: str) -> str:
    """"Structured workflows and Designing an interface" -> "and designed" (parallel with the sentence's verb)."""
    first = tu.first_word(t)
    tense = None
    if not first.lower().endswith("ing"):
        tense = tu.verb_tense(first) or ("past" if first.lower().endswith("ed") else None)

    def fix(m):
        w = m.group(2)
        base = _base_verb(w)
        if not base or w.isupper():
            return m.group(0)
        if tense:
            return m.group(1) + _inflect(base, tense)
        return m.group(1) + w.lower()
    return re.sub(r"(\b(?:and|or|while|then)\s+)([A-Z][a-z]+ing|[A-Z][a-z]+ed|[a-z]+ing)\b",
                  lambda m: fix(m) if (m.group(2)[0].isupper() or tense) else m.group(0), t)


def _cap_after_label(t: str) -> str:
    return re.sub(r"^((?:Outcome|Result|Impact|Achievement|Tech(?:nologies)?|Tools)\s*:\s*)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), t)


def improve_line(text: str, *, kind: str, tense: str) -> str:
    """Return a cleaned, professional version of `text` (unchanged if nothing to fix)."""
    if kind == "skills_line":
        label, sep, items = text.partition(":")
        if not sep:
            label, items = "", text
        parts = [p.strip() for p in re.split(r"\s*,\s*|\s*\|\s*", items) if p.strip()]
        seen, clean = set(), []
        for p in parts:
            p = _fix_spacing(_canon_skills(_acronyms(p)))
            if p.lower() not in seen:
                seen.add(p.lower())
                clean.append(p)
        body = ", ".join(clean)
        return f"{label.strip()}: {body}" if sep else body
    if kind in ("entry_header", "contact") or "•" in text or " | " in text:
        return _fix_spacing(text)  # list-style lines: keep separators, fix spacing only
    out = []
    for s in _sentences(text):
        s = _fix_spacing(_fix_informal(s))
        s = _fix_weak_opener(s, tense)
        s = _fix_midsentence_caps(s)
        s = _cap_after_label(s)
        s = _canon_skills(_acronyms(s))
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
        out.append(s)
    result = " ".join(out)
    if text.rstrip().endswith(".") and not result.endswith("."):
        result += "."
    return result


def _tense_for(model: ResumeModel, b: Block) -> str:
    entry = model.entries.get(b.entry_id) if b.entry_id else None
    if entry and entry.current:
        first = tu.verb_tense(tu.first_word(b.body))
        return first or "present"
    return "past"


def scan(model: ResumeModel, known_words: set[str]) -> list[dict]:
    """One finding per line that has wording or grammar problems."""
    findings = []
    for b in model.blocks:
        if b.kind in ("heading", "contact") or b.section == "contact":
            continue
        kind = b.kind if b.kind in ("bullet", "paragraph", "skills_line") else "line"
        tense = _tense_for(model, b)
        body = b.body
        if body[:1].islower() or len(body.split()) < 3:
            continue  # a fragment (e.g. a wrapped line) is never rewritten on its own
        improved = improve_line(body, kind=b.kind, tense=tense)
        vkind = {"bullet": "bullet", "paragraph": "paragraph", "skills_line": "skills_line"}.get(b.kind, "line")
        check = validate_text(improved, kind=vkind, known_words=known_words)
        if check.get("fixed_text"):
            fixed = check["fixed_text"]
            if validate_text(fixed, kind=vkind, known_words=known_words)["passed"]:
                improved = fixed
        before = validate_text(body, kind=vkind, known_words=known_words)
        issues = [c["message"] for c in before["checks"] if c["status"] in ("fail", "warn")
                  and c["name"] not in ("relevance", "action_verb", "conciseness")]
        if improved.strip() != body.strip():
            if not issues:
                issues = ["Inconsistent spacing, casing or technical spelling"]
            findings.append({"block_id": b.id, "section": b.section, "kind": b.kind, "original": body,
                             "improved": improved, "issues": issues})
    return findings
