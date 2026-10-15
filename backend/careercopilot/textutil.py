"""Deterministic text helpers shared by parsing, scoring and validation."""
from __future__ import annotations

import re
from datetime import date

BULLET_CHARS = "•●▪■◦‣∙·–—-*►▸➢✓✔○"
BULLET_RE = re.compile(r"^\s*([" + re.escape(BULLET_CHARS) + r"])\s+")

STOPWORDS = set(
    """a an the and or but if of to in on for with by at from as into about over under
    is are was were be been being this that these those it its we our you your they their
    i me my he she his her them will would can could should may might must shall do does did
    have has had not no yes than then so such via per using use used within across between
    strong excellent good great ability able experience experienced years year plus etc
    including include includes other new work working related relevant knowledge understanding
    skills skill proficiency proficient familiarity familiar preferred required requirements
    candidate role team teams position job responsibilities responsible ideal looking """.split()
)

# Action verbs in past tense mapped to their present-tense form.
ACTION_VERBS_PAST = {
    "achieved": "achieve", "analyzed": "analyze", "analysed": "analyse", "architected": "architect",
    "automated": "automate", "built": "build", "collaborated": "collaborate", "configured": "configure",
    "containerized": "containerize", "containerised": "containerise", "coordinated": "coordinate",
    "created": "create", "cut": "cut", "debugged": "debug", "decreased": "decrease", "defined": "define",
    "delivered": "deliver", "deployed": "deploy", "designed": "design", "developed": "develop",
    "drove": "drive", "enabled": "enable", "engineered": "engineer", "enhanced": "enhance",
    "established": "establish", "evaluated": "evaluate", "expanded": "expand", "facilitated": "facilitate",
    "fixed": "fix", "founded": "found", "generated": "generate", "guided": "guide", "handled": "handle",
    "headed": "head", "identified": "identify", "implemented": "implement", "improved": "improve",
    "increased": "increase", "integrated": "integrate", "introduced": "introduce", "launched": "launch",
    "led": "lead", "maintained": "maintain", "managed": "manage", "mentored": "mentor",
    "migrated": "migrate", "modernized": "modernize", "monitored": "monitor", "optimized": "optimize",
    "optimised": "optimise", "orchestrated": "orchestrate", "organized": "organize", "owned": "own",
    "partnered": "partner", "performed": "perform", "planned": "plan", "presented": "present",
    "produced": "produce", "programmed": "program", "prototyped": "prototype", "provisioned": "provision",
    "published": "publish", "rebuilt": "rebuild", "reduced": "reduce", "refactored": "refactor",
    "released": "release", "researched": "research", "resolved": "resolve", "reviewed": "review",
    "ran": "run", "scaled": "scale", "secured": "secure", "shipped": "ship", "simplified": "simplify",
    "spearheaded": "spearhead", "standardized": "standardize", "streamlined": "streamline",
    "supported": "support", "taught": "teach", "tested": "test", "trained": "train",
    "transformed": "transform", "troubleshot": "troubleshoot", "tuned": "tune", "upgraded": "upgrade",
    "used": "use", "utilized": "utilize", "validated": "validate", "wrote": "write", "authored": "author",
    "contributed": "contribute", "applied": "apply", "assisted": "assist", "conducted": "conduct",
    "documented": "document", "exposed": "expose", "modeled": "model", "modelled": "model",
    "queried": "query", "processed": "process", "visualized": "visualize", "cleaned": "clean",
    "accelerated": "accelerate", "adopted": "adopt", "consolidated": "consolidate", "converted": "convert",
}
ACTION_VERBS_PRESENT = {v: k for k, v in ACTION_VERBS_PAST.items()}
# Third-person present forms ("develops") are also used in current roles.
ACTION_VERBS_PRESENT_S = {}
for _v in list(ACTION_VERBS_PRESENT):
    if _v.endswith(("s", "sh", "ch", "x", "z")):
        ACTION_VERBS_PRESENT_S[_v + "es"] = _v
    elif _v.endswith("y") and _v[-2:-1] not in "aeiou":
        ACTION_VERBS_PRESENT_S[_v[:-1] + "ies"] = _v
    else:
        ACTION_VERBS_PRESENT_S[_v + "s"] = _v

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_RE = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_RE = rf"(?:{_MONTH_RE}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_RE})\s*(?:-|–|—|to)\s*(?P<end>{_DATE_RE}|present|current|now|today)",
    re.IGNORECASE,
)

SECTION_KEYWORDS = {
    "summary": ["summary", "professional summary", "profile", "objective", "about me", "career summary", "about"],
    "experience": ["experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "relevant experience", "internships", "internship", "internship experience",
                   "experience and internships", "internships and experience"],
    "projects": ["projects", "personal projects", "academic projects", "selected projects", "key projects"],
    "skills": ["skills", "technical skills", "technologies", "core competencies", "tools", "tech stack",
               "skills & tools", "skills and tools", "technical proficiencies"],
    "education": ["education", "academic background", "education & training", "academics"],
    "certifications": ["certifications", "certificates", "licenses & certifications", "licenses and certifications",
                       "certifications & awards", "certification"],
    "other": ["awards", "honors", "publications", "volunteer", "volunteering", "interests", "languages",
              "activities", "leadership", "achievements"],
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_bullet(text: str) -> tuple[str, str]:
    """Return (bullet_glyph, text_without_bullet)."""
    m = BULLET_RE.match(text)
    if m:
        return m.group(1), text[m.end():]
    return "", text


def classify_heading(text: str) -> str | None:
    t = re.sub(r"[^a-z& ]", "", text.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or len(t.split()) > 5:
        return None
    for section, keys in SECTION_KEYWORDS.items():
        if t in keys:
            return section
    return None


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9+#./-]*", text)


def stem(word: str) -> str:
    w = word.lower().strip(".,;:")
    if w in ACTION_VERBS_PAST:
        w = ACTION_VERBS_PAST[w]
    elif w in ACTION_VERBS_PRESENT_S:
        w = ACTION_VERBS_PRESENT_S[w]
    for suf in ("ization", "isation", "ations", "ation", "ments", "ment", "ings", "ing", "ers", "er", "ies",
                "ed", "es", "ly", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w


def content_terms(text: str) -> set[str]:
    return {stem(w) for w in words(text) if w.lower() not in STOPWORDS and len(w) > 2}


def first_word(text: str) -> str:
    ws = words(strip_bullet(text)[1])
    return ws[0] if ws else ""


def verb_tense(word: str) -> str | None:
    w = word.lower()
    if w in ACTION_VERBS_PAST:
        return "past"
    if w in ACTION_VERBS_PRESENT or w in ACTION_VERBS_PRESENT_S:
        return "present"
    return None


def to_past(word: str) -> str:
    w = word.lower()
    if w in ACTION_VERBS_PRESENT:
        out = ACTION_VERBS_PRESENT[w]
    elif w in ACTION_VERBS_PRESENT_S:
        out = ACTION_VERBS_PRESENT[ACTION_VERBS_PRESENT_S[w]]
    else:
        return word
    return out[0].upper() + out[1:] if word[:1].isupper() else out


def to_present(word: str) -> str:
    w = word.lower()
    if w in ACTION_VERBS_PAST:
        out = ACTION_VERBS_PAST[w]
    elif w in ACTION_VERBS_PRESENT_S:
        out = ACTION_VERBS_PRESENT_S[w]
    else:
        return word
    return out[0].upper() + out[1:] if word[:1].isupper() else out


def _parse_date(s: str, as_of: date, is_end: bool) -> tuple[int, int] | None:
    s = s.strip().lower().rstrip(".")
    if s in ("present", "current", "now", "today"):
        return as_of.year, as_of.month
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    if m:
        return int(m.group(2)), max(1, min(12, int(m.group(1))))
    m = re.match(r"([a-z]+)\.?\s+(\d{4})", s)
    if m:
        return int(m.group(2)), MONTHS.get(m.group(1)[:3], 1)
    m = re.match(r"(\d{4})", s)
    if m:
        return int(m.group(1)), 12 if is_end else 1
    return None


def date_ranges(text: str, as_of: date) -> list[tuple[tuple[int, int], tuple[int, int], bool]]:
    out = []
    for m in DATE_RANGE_RE.finditer(text):
        a = _parse_date(m.group("start"), as_of, False)
        b = _parse_date(m.group("end"), as_of, True)
        if a and b and a <= b:
            current = m.group("end").strip().lower() in ("present", "current", "now", "today")
            out.append((a, b, current))
    return out


def total_years(ranges: list[tuple[tuple[int, int], tuple[int, int]]]) -> float:
    """Total experience in years with overlapping periods merged."""
    months = sorted((a[0] * 12 + a[1], b[0] * 12 + b[1]) for a, b in ranges)
    merged: list[list[int]] = []
    for s, e in months:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return round(sum(e - s + 1 for s, e in merged) / 12.0, 2)
