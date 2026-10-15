"""Factual validation of proposed text before it can be applied.

Each material claim (skill/technology mention, numeric metric) in the
proposed text is checked against the original resume, other verified user
input and explicit confirmations.
"""
from __future__ import annotations

import re

from . import textutil as tu
from .model import ResumeModel
from .scoring import collect_evidence
from .taxonomy import taxonomy

STATUS_ORDER = ["stylistic", "evidence", "confirmation", "clarification", "unsupported"]
STATUS_LABELS = {
    "evidence": "Supported by existing evidence",
    "confirmation": "Supported by user confirmation",
    "stylistic": "Stylistic improvement without new factual claims",
    "clarification": "Requires clarification",
    "unsupported": "Unsupported or contradicted",
}
APPLICABLE = {"evidence", "confirmation", "stylistic"}
NUM_RE = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|[kKmMbB]\b|x\b)?")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group(0)).lower().replace("percent", "%") for m in NUM_RE.finditer(text)}


def validate_claims(new_text: str, *, model: ResumeModel, mode: str, original_text: str | None,
                    confirmations: dict, denials: set, kind: str = "bullet") -> dict:
    tax = taxonomy()
    evidence = collect_evidence(model)
    resume_text = "\n".join(b.text for b in model.blocks if b.kind != "contact")
    confirm_text = " ".join(c.get("text", "") for c in confirmations.values())
    orig = original_text or ""
    claims: list[dict] = []

    new_skills = tax.ids_in(new_text) - (tax.ids_in(orig) if mode == "replace" else set())
    orig_ids = tax.ids_in(orig)
    for sid in sorted(new_skills):
        sk = tax.get(sid)
        ev = evidence.get(sid)
        if sid in denials:
            claims.append({"claim": sk.name, "type": "skill", "status": "unsupported",
                           "evidence": ["You told us you have not used this."]})
            continue
        if sid in confirmations:
            claims.append({"claim": sk.name, "type": "skill", "status": "confirmation",
                           "evidence": [f"Your confirmation: “{confirmations[sid].get('text') or 'Yes'}”"]})
            continue
        own_words = next((c.get("text") for c in confirmations.values()
                          if sid in tax.ids_in(c.get("text", ""))), None)
        if own_words:
            claims.append({"claim": sk.name, "type": "skill", "status": "confirmation",
                           "evidence": [f"Named in your own description: “{own_words}”"]})
            continue
        implying = [s for s in orig_ids if sid in tax.get(s).implies]
        if implying:
            claims.append({"claim": sk.name, "type": "skill", "status": "evidence",
                           "evidence": [f"This sentence already names {tax.get(implying[0]).name}, which "
                                        f"implies {sk.name} experience: “{orig}”"]})
            continue
        if kind == "skills_line" and ev is not None and ev.level != "none":
            how = (f"{tax.get(ev.via).name} work shown in: “{ev.quote}”" if ev.level == "implied"
                   else f"Already in your resume: “{ev.quote}”")
            claims.append({"claim": sk.name, "type": "skill", "status": "evidence", "evidence": [how]})
            continue
        if ev is not None and ev.level == "demonstrated" and mode == "insert_after":
            claims.append({"claim": sk.name, "type": "skill", "status": "evidence",
                           "evidence": [f"Already demonstrated: “{ev.quote}”"]})
            continue
        claims.append({"claim": sk.name, "type": "skill", "status": "clarification",
                       "evidence": ["No evidence in the resume that you used this here; please confirm."]})

    known_nums = _numbers(resume_text) | _numbers(confirm_text)
    for num in sorted(_numbers(new_text) - (_numbers(orig) if mode == "replace" else set())):
        if num in known_nums:
            claims.append({"claim": num, "type": "metric", "status": "evidence",
                           "evidence": ["This figure already appears in your resume or your confirmation."]})
        else:
            claims.append({"claim": num, "type": "metric", "status": "clarification",
                           "evidence": ["New metric not found in your resume; confirm it is accurate."]})

    if mode == "insert_after" and not claims:
        terms = tu.content_terms(new_text)
        source = next((c.get("text", "") for c in confirmations.values()
                       if len(terms & tu.content_terms(c.get("text", ""))) >= min(2, len(terms))), None)
        status = "confirmation" if source else "clarification"
        claims.append({"claim": "new statement", "type": "statement", "status": status,
                       "evidence": [f"Based on your own words: \u201c{source}\u201d" if source
                                    else "New content needs your confirmation."]})
    if claims:
        status = max((c["status"] for c in claims), key=STATUS_ORDER.index)
    else:
        status = "stylistic"
    return {"status": status, "label": STATUS_LABELS[status], "applicable": status in APPLICABLE,
            "claims": claims}
