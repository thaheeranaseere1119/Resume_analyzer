"""Deterministic resume phrasing engine.

Turns free-form candidate text ("I have relevant experience in ..., where I
contributed to UI development, improved ...") into clean resume lines:

* extracts the concrete achievement clauses, drops filler/self-assessment,
* removes first-person subjects and conflicting organisation names,
* puts the leading action verb in the section's tense,
* fixes spelling, articles, spacing and canonical skill spelling,
* trims to a readable length at a clause boundary,
* and only returns lines that pass every grammar/writing check.

It never adds facts: every word it keeps comes from the input text (or the
named skill, when the candidate's text already describes using it).
"""
from __future__ import annotations

import re

from . import textutil as tu
from .grammar import validate_text
from .taxonomy import taxonomy

FILLER = [
    r"^(these|this|those|such) (experiences?|projects?|roles?|opportunities|skills)\b",
    r"\b(strengthened|sharpened|honed|improved|enhanced|developed|deepened|built) (my|our)\b.*\b(skills?|abilit(y|ies)|knowledge|understanding|confidence)\b",
    r"^(i|we) (am|was|were|feel|felt) (passionate|excited|eager|confident|happy|proud|motivated)",
    r"^(i|we) (believe|think|feel|learned|learnt|enjoy|enjoyed|love|loved)\b",
    r"^(i|we) (have|had|possess|gained|bring) (relevant|some|good|strong|solid|hands-on|practical|extensive)? ?(experience|knowledge|exposure|background|familiarity)\b",
    r"^(overall|in summary|additionally|also),?$",
]
ORG_SUFFIX = (r"(?:Technologies|Technology|Pvt\.?|Ltd\.?|Limited|Inc\.?|LLC|Labs|Solutions|Systems|Corp\.?|"
              r"Corporation|Company|Software|Services|Consulting|Infotech|Tech)")
ORG_RE = re.compile(r",?\s*\b(?:at|with|for|in)\s+(?:[A-Z][\w&.'-]*\s+){0,5}" + ORG_SUFFIX + r"(?:\s+(?:Private\s+)?Limited)?\b")
CONTEXT_RE = re.compile(r",?\s*\b(?:during|throughout|as part of|in)\s+my\s+(?:internship|role|time|tenure|job|work)"
                        r"(?:\s+(?:at|with)\s+[^,.;]+)?", re.I)
INNER_CLAUSE = re.compile(r"\b(?:where|in which|through which|wherein)\s+(?:i|we)\s+(.+)$", re.I)
SUBJECT = re.compile(r"^(?:i|we|my team and i|the team and i)\s+(?:(?:have|had|also|successfully|personally|actively|"
                     r"was able to|were able to|helped(?: to)?|got to|would|then|am|was|were|have been|had been|"
                     r"been|currently|recently)\s+)*", re.I)
CONNECTOR_END = re.compile(r"\s*(?:,|\band\b|\bor\b|\bwith\b|\bto\b|\bthe\b|\ba\b|\ban\b|\bof\b|\bfor\b|\bin\b|\bby\b)\s*$", re.I)


def _sentences(text: str) -> list[str]:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[•●▪]\s*", "\n", text)
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Za-z0-9\"(])|\n+", text)
    return [tu.normalize_ws(p) for p in parts if tu.normalize_ws(p)]


def _base_verb(word: str) -> str | None:
    w = word.lower()
    if w in tu.ACTION_VERBS_PRESENT:
        return w
    if w in tu.ACTION_VERBS_PAST:
        return tu.ACTION_VERBS_PAST[w]
    if w in tu.ACTION_VERBS_PRESENT_S:
        return tu.ACTION_VERBS_PRESENT_S[w]
    if w.endswith("ing") and len(w) > 5:
        stem = w[:-3]
        for cand in (stem, stem + "e", stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] else stem):
            if cand in tu.ACTION_VERBS_PRESENT:
                return cand
    return None


def _inflect(base: str, tense: str) -> str:
    return tu.ACTION_VERBS_PRESENT[base] if tense == "past" else base


ACRONYMS = {w: w.upper() for w in ("api", "apis", "aws", "gcp", "ui", "ux", "sql", "html", "css", "json", "xml", "rest",
                                    "crud", "cli", "sdk", "qa", "http", "https", "url", "seo", "erp", "crm", "ml", "ai",
                                    "nlp", "iot", "gpu", "cpu", "kpi", "kpis", "etl", "jwt", "ci", "cd", "ios", "mvp")}
ACRONYMS.update({"apis": "APIs", "kpis": "KPIs", "ios": "iOS", "ui/ux": "UI/UX", "ai/ml": "AI/ML", "ci/cd": "CI/CD"})


def _acronyms(text: str) -> str:
    return re.sub(r"(?<![\w.])(ui/ux|ai/ml|ci/cd|[a-z]{2,5})(?![\w])",
                  lambda m: ACRONYMS.get(m.group(1), m.group(1)) if m.group(1) in ACRONYMS else m.group(1), text)


def _canon_skills(text: str) -> str:
    text = _acronyms(text)
    tax = taxonomy()
    for sid, spans in sorted(tax.find(text).items(), key=lambda kv: -kv[1][0][0]):
        sk = tax.get(sid)
        allowed = set(sk.aliases) | {sk.name}
        for s, e, surface in sorted(spans, reverse=True):
            if surface not in allowed:
                best = next((a for a in allowed if a.lower() == surface.lower()), sk.name)
                brand = any(ch.isupper() for ch in best[1:]) or (best[:1].islower() and any(ch.isupper() for ch in best))
                if best.lower() == surface.lower() and not brand and surface[:1].isupper():
                    continue  # already capitalised common term ("Data Science", "Mentored")
                text = text[:s] + best + text[e:]
    return text


def _trim(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words])
    # prefer the last clause boundary inside the limit
    m = list(re.finditer(r",\s|;\s|\s(?:and|while|by)\s", cut))
    if m and m[-1].start() > len(cut) * 0.45:
        cut = cut[: m[-1].start()]
    cut = CONNECTOR_END.sub("", cut)
    return cut.rstrip(" ,;:-")


def clean_clause(clause: str, *, tense: str, max_words: int, entry_org: str = "") -> str | None:
    c = clause.strip().strip('"').rstrip(".;!? ")
    c = re.sub(r"^(yes|yeah|yep|sure|ok(ay)?)[,.!]?\s+", "", c, flags=re.I)
    c = SUBJECT.sub("", c)
    c = CONTEXT_RE.sub("", c)
    for m in list(ORG_RE.finditer(c)):
        org = m.group(0)
        if not entry_org or not any(w.lower() in entry_org.lower() for w in re.findall(r"[A-Z][\w&.-]{2,}", org)):
            c = c.replace(org, "")
    c = re.sub(r"^(including|such as|like)\s+", "", c, flags=re.I)
    c = re.sub(r"\b(?:our|my)\s+", "the ", c, flags=re.I)          # "our Flask APIs" -> "the Flask APIs"
    # mid-sentence first person: "the bugs I found" -> "the bugs found", "and I fixed" -> "and fixed"
    c = re.sub(r"\s(?:that|which|whom)\s+(?:I|we)\s+", " ", c)
    c = re.sub(r"\s(?:I|we)\s+(?:have\s+|had\s+|also\s+)?(?=[a-z])", " ", c)
    c = re.sub(r"\bme\b", "", c, flags=re.I)
    c = tu.normalize_ws(c)
    if not c:
        return None
    first = tu.words(c)[0] if tu.words(c) else ""
    base = _base_verb(first)
    if base is None:
        return None  # not an action statement; never invent a verb
    c = _inflect(base, tense) + c[len(first):]
    c = c[0].upper() + c[1:]
    c = re.sub(r"\s+([,.;:])", r"\1", c)
    c = re.sub(r",\s*,", ",", c)
    c = _canon_skills(c)
    return _trim(c, max_words)


NEGATION = re.compile(
    r"\b(?:not|n't|never|no (?:prior |hands-on |direct |real |professional )?(?:experience|exposure)|yet to|"
    r"eager to|plan(?:ning)? to|would like to|hope to|want to|wish to|looking forward to|aspire|intend to|"
    r"am learning|currently learning|will learn|fundamentals? of|basic understanding|familiar with the concepts?)\b",
    re.I)


DENIAL = re.compile(
    r"\b(?:(?:have|has|had|did|do)\s*(?:not|n't)|haven't|hasn't|didn't|don't|never|not yet|yet to)\s+(?:\w+\s+){0,2}?"
    r"(?:used|use|worked|work|tried|done|built|deployed|implemented|set up|configured|had|got|learn\w*|experience\w*)\b"
    r"|\bno (?:prior |hands-on |direct |real |professional |practical )?(?:experience|exposure)\b"
    r"|\b(?:eager|planning|plan|hoping|hope|want|wish|would like|intend|looking forward) to (?:learn|use|work|try|gain|explore)\b"
    r"|^\s*no\b[.,!]?", re.I)


def is_denial(sentence: str) -> bool:
    """True only for clear statements of NOT having used something (or only planning to).
    Plain words like "not" ("so broken code was not deployed") or "basic understanding" do not count."""
    return bool(DENIAL.search(sentence))


def is_negated(sentence: str) -> bool:
    """True for statements of not having done something, or wanting to do it later."""
    return bool(NEGATION.search(sentence))


def skill_mentions(text: str, skill_id: str, strict: bool = False) -> tuple[bool, bool]:
    """(mentioned positively, mentioned only negatively) for a skill in free text.
    strict=True counts only clear denials as negative (used to read the candidate's yes/no answers)."""
    from .taxonomy import taxonomy
    neg_test = is_denial if strict else is_negated
    pos = neg = False
    for sent in _sentences(text):
        if skill_id in taxonomy().ids_in(sent):
            if neg_test(sent):
                neg = True
            else:
                pos = True
    return pos, (neg and not pos)


def candidate_clauses(text: str) -> list[str]:
    out = []
    for sent in _sentences(text):
        if is_negated(sent):
            continue  # never turn "I have not used X" or "I am eager to learn X" into a claim
        inner = INNER_CLAUSE.search(sent)
        if inner:
            out.append(inner.group(1))
            continue
        if any(re.search(p, sent, re.I) for p in FILLER):
            continue
        out.append(sent)
    return out


def _score(line: str, skill: str | None, jd_terms: set[str]) -> float:
    s = 0.0
    if re.search(r"\d", line):
        s += 2
    if skill and skill.lower() in line.lower():
        s += 2
    n = len(line.split())
    s += 1.5 if 8 <= n <= 28 else 0
    s += 0.3 * len(tu.content_terms(line) & jd_terms)
    return s


def polish(line: str, **validate_kw) -> tuple[str, dict]:
    """Apply the validator's safe fixes until the line passes (max 3 rounds)."""
    result = validate_text(line, **validate_kw)
    for _ in range(3):
        if result["passed"] or not result.get("fixed_text"):
            break
        line = result["fixed_text"]
        result = validate_text(line, **validate_kw)
    return line, result


def phrase_bullets(text: str, *, skill: str | None = None, tense: str = "past", period: bool = True,
                   entry_org: str = "", neighbors: list[str] | None = None, current_entry: bool = False,
                   known_words: set[str] | None = None, jd_skill_ids: set[str] | None = None,
                   jd_terms: set[str] | None = None, max_words: int = 30, limit: int = 3,
                   confirmed_skill_id: str | None = None) -> list[str]:
    """Return up to `limit` clean, validated resume lines built only from `text`."""
    jd_terms = jd_terms or set()
    lines: list[str] = []
    for clause in candidate_clauses(text):
        line = clean_clause(clause, tense=tense, max_words=max_words, entry_org=entry_org)
        if not line or len(line.split()) < 4:
            continue
        from .taxonomy import taxonomy as _tax
        has_skill = confirmed_skill_id in _tax().ids_in(line) if confirmed_skill_id else skill and skill.lower() in line.lower()
        tool_like = confirmed_skill_id and _tax().get(confirmed_skill_id).category not in ("practice", "process", "soft", "architecture")
        if skill and not has_skill and not is_negated(clause) and (skill.lower() in clause.lower() or tool_like):
            # attach the keyword only where the candidate said (or confirmed) they used it
            line = line + (f" with {skill}" if " using " in line else f" using {skill}")
        line = line.rstrip(".") + ("." if period else "")
        line, res = polish(line, kind="bullet", neighbors=neighbors or [], current_entry=current_entry,
                           known_words=(known_words or set()) | set(re.findall(r"[A-Za-z][A-Za-z'-]+", text)),
                           jd_skill_ids=jd_skill_ids, jd_terms=jd_terms)
        if res["passed"] and line not in lines:
            lines.append(line)
    lines.sort(key=lambda l: -_score(l, skill, jd_terms))
    return lines[:limit]


def polish_paragraph(text: str, *, tense: str, known_words: set[str] | None = None) -> str | None:
    """Clean a multi-sentence description paragraph sentence by sentence (keeps every sentence)."""
    out = []
    for sent in _sentences(text):
        s = sent.rstrip(".;!? ")
        s = re.sub(r"^(outcome|result|impact)\s*:\s*", lambda m: m.group(1).capitalize() + ": ", s, flags=re.I)
        first = tu.words(s.split(": ", 1)[-1])[0] if tu.words(s) else ""
        base = _base_verb(first)
        if base and not s.lower().startswith(("outcome", "result", "impact")):
            s = _inflect(base, tense) + s[len(first):]
        s = _canon_skills(s[0].upper() + s[1:]) + "."
        s, res = polish(s, kind="paragraph", known_words=(known_words or set()) | set(re.findall(r"[A-Za-z][A-Za-z'-]+", text)))
        if not res["passed"]:
            return None
        out.append(s)
    return " ".join(out) if out else None
