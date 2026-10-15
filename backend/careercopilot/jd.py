"""Job description parsing into normalized, weighted requirements."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import textutil as tu
from .taxonomy import taxonomy

SKIP_SECTIONS = re.compile(
    r"^(about\b.*|who we are|our company|company overview|our story|benefits|perks|what we offer|why join us|"
    r"compensation.*|ctc.*|salary.*|onboarding.*|documents.*|equal opportunity.*|eeo.*|thank you|training agreement.*|"
    r"locations? of work.*|how to apply.*)$", re.I)

JD_SECTIONS = {
    "responsibilities": ["responsibilities", "what you'll do", "what you will do", "the role", "duties",
                         "about the role", "your role", "key responsibilities", "day to day", "in this role"],
    "required": ["requirements", "qualifications", "required", "required qualifications", "must have",
                 "must-have", "what you bring", "minimum qualifications", "basic qualifications", "who you are",
                 "required skills", "what we're looking for", "what we are looking for", "pre-requisites",
                 "prerequisites", "eligibility", "eligibility criteria", "skills required", "desired profile",
                 "candidate profile", "key skills", "skills"],
    "preferred": ["preferred", "preferred qualifications", "nice to have", "nice-to-have", "bonus", "bonus points",
                  "pluses", "good to have", "preferred skills"],
}

DEGREE_LEVELS = [
    (3, r"\b(ph\.?\s?d|doctorate|doctoral)\b"),
    (2, r"\b(master'?s?|m\.s\.?|msc|m\.sc|mba|mca|m\.?tech|m\.e\.)\b"),
    (1, r"\b(bachelor'?s?|b\.s\.?|bsc|b\.sc|b\.e\.?|b\.?tech|bca|undergraduate degree|degree)\b"),
]
DEGREE_CUE = re.compile(r"degree|bachelor|master|ph\.?d|b\.s|m\.s|bsc|msc|graduat|b\.?tech|m\.?tech|\bmca\b|\bbca\b|b\.e\.", re.I)
BE_RE = re.compile(r"\bBE\b")  # case-sensitive: "be" is an ordinary word
FIELDS = ["computer science", "computer engineering", "software engineering", "information technology",
          "information systems", "electronics", "electrical", "telecommunication", "engineering", "mathematics",
          "statistics", "data science", "physics", "business"]
FIELD_ALIASES = {"information technology": [r"\bIT\b", r"information technology"],
                 "computer science": [r"\bCSE?\b", r"computer science"],
                 "electronics": [r"\bECE\b", r"electronics"]}


def fields_in(text: str) -> list[str]:
    out = []
    for f in FIELDS:
        pats = FIELD_ALIASES.get(f, [re.escape(f)])
        if any(re.search(p, text, 0 if p.startswith("\\b") and p[2:4].isupper() else re.I) for p in pats):
            out.append(f)
    return out


def _unwrap(text: str) -> list[str]:
    """Rejoin lines that a PDF copy-paste broke mid-sentence."""
    out: list[str] = []
    for raw in re.split(r"[\n\r]+", text):
        line = tu.normalize_ws(raw)
        if not line:
            continue
        prev = out[-1] if out else ""
        wrapped_prev = len(prev) >= 70 or re.search(r"\b(and|or|of|to|the|a|an|for|with|in|from)$|[,–-]$", prev)
        looks_heading = line.endswith(":") or (len(line.split()) <= 4 and not line[:1].islower())
        if out and not re.search(r"[.:;!?]$", prev) and not tu.BULLET_RE.match(line) and not looks_heading and \
                (line[:1].islower() or line[:1] in "(&" or re.match(r"^(and|or|to|of|for|with|in)\b", line) or wrapped_prev):
            out[-1] = out[-1] + " " + line
        else:
            out.append(line)
    return out

PREFERRED_CUES = re.compile(r"\b(preferred|nice to have|nice-to-have|a plus|is a plus|bonus|ideally|desirable)\b", re.I)
ALT_CUES = re.compile(r"\b(or similar|or equivalent|or comparable|or related|such as|e\.g\.?|for example|"
                      r"for instance|like|any (one )?of|one or more of|at least one of|etc\.?)(?=\W|$)", re.I)
CLOUD_PROVIDERS = {"aws", "azure", "gcp"}


def _alternative_groups(sent: str, found: dict) -> list[set[str]]:
    """Groups of skills that the sentence names as interchangeable options (any one is enough)."""
    tax = taxonomy()
    groups = []
    clouds = CLOUD_PROVIDERS & set(found)
    if len(clouds) >= 2:            # jobs listing several clouds almost never need all of them
        groups.append(clouds)
    spans = []
    # "(FAISS, Pinecone, … or similar)" and "libraries such as A, B, C"
    for m in re.finditer(r"\(([^)]*)\)", sent):
        if ALT_CUES.search(m.group(1)) or re.search(r"\bor\b", m.group(1)) or m.group(1).count(",") >= 2:
            spans.append((m.start(1), m.end(1)))
    cue = ALT_CUES.search(sent)
    if cue and not any(a <= cue.start() < b for a, b in spans):
        spans.append((cue.start(), len(sent)))
    for a, b in spans:
        groups.append({sid for sid, sp in found.items() if all(a <= x < b for x, *_ in sp)})
    # "FastAPI, Flask, or Django": a comma/slash chain of same-category skills joined by "or"
    first = sorted((sp[0][0], sp[0][1], sid) for sid, sp in found.items())
    chain = []
    for st, en, sid in first + [(None, None, None)]:
        if chain and sid is not None and tax.get(sid).category == tax.get(chain[-1][2]).category and \
                re.fullmatch(r"[\s,/]*(or\s+)?", sent[chain[-1][1]:st], re.I):
            chain.append((st, en, sid))
            continue
        if len(chain) >= 2 and any(re.search(r"\bor\b|/", sent[x[1]:y[0]]) for x, y in zip(chain, chain[1:])):
            groups.append({c[2] for c in chain})
        chain = [(st, en, sid)] if sid else []
    return [g for g in ({a for a in g if not tax.get(a).generic} for g in groups) if g]


REQUIRED_CUES = re.compile(r"\b(required|must|minimum|at least)\b", re.I)


@dataclass
class SkillReq:
    id: str
    skill_id: str
    name: str
    category: str
    importance: str          # required | preferred
    jd_text: str
    mentions: int = 1
    # Only ever named as one option in a list ("FAISS, Pinecone or similar"; "AWS, Azure or GCP"):
    # any one of the options satisfies it, so it carries half weight and a sibling's evidence counts.
    example_only: bool = False
    alternatives: list[str] = field(default_factory=list)


@dataclass
class ResponsibilityReq:
    id: str
    text: str
    terms: list[str]


@dataclass
class JobRequirements:
    skills: list[SkillReq] = field(default_factory=list)
    certifications: list[SkillReq] = field(default_factory=list)
    responsibilities: list[ResponsibilityReq] = field(default_factory=list)
    min_years: float | None = None
    years_text: str = ""
    degree_level: int | None = None
    degree_fields: list[str] = field(default_factory=list)
    degree_text: str = ""
    degree_importance: str = "required"
    title: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _jd_heading(line: str) -> str | None:
    t = re.sub(r"[^a-z' -]", "", line.lower()).strip()
    if not t or len(t.split()) > 6:
        return None
    for sec, keys in JD_SECTIONS.items():
        if t in keys or any(t.startswith(k) for k in keys if len(k) > 6):
            return sec
    return None


def parse_jd(text: str) -> JobRequirements:
    tax = taxonomy()
    req = JobRequirements()
    lines = _unwrap(text)
    if lines and len(lines[0].split()) <= 8:
        req.title = lines[0]
    section = "general"
    skill_map: dict[str, SkillReq] = {}

    for raw in lines:
        head = raw.rstrip(":").strip()
        if raw.endswith(":") and len(head.split()) <= 6:
            # A true heading: either a known section, a section to skip, or a new general section.
            section = "skip" if SKIP_SECTIONS.match(head) else (_jd_heading(head) or "general")
            continue
        if len(raw.split()) <= 3 and SKIP_SECTIONS.match(head):
            section = "skip"
            continue
        # "Heading: value" on one line ends any skipped section; some labels carry no requirements.
        hv = re.match(r"^([A-Za-z][A-Za-z/ &'-]{1,40}):\s*(\S.*)$", raw)
        if hv and len(hv.group(1).split()) <= 4:
            if re.match(r"(about|ctc|compensation|salary|locations?|training agreement|profile|designation)", hv.group(1), re.I):
                continue
            section = "general"
        sec = _jd_heading(head)
        if sec and (raw.endswith(":") or len(raw.split()) <= 4):
            section = sec
            continue
        if section == "skip":
            continue
        is_bullet = bool(tu.BULLET_RE.match(raw))
        line = tu.strip_bullet(raw)[1]
        # Sentence-level splitting so the "relevant requirement" shown to the user is concise.
        sentences = [s.strip() for s in re.split(r"(?<=[.;])\s+", line) if s.strip()]
        for sent in sentences:
            if PREFERRED_CUES.search(sent) or section == "preferred":
                importance = "preferred"
            else:
                importance = "required"
            found = tax.find(sent)
            groups = _alternative_groups(sent, found)
            alts = set().union(*groups) if groups else set()
            for sid, spans in found.items():
                skill = tax.get(sid)
                bucket = "cert" if skill.category == "certification" else "skill"
                key = f"{bucket}:{sid}"
                is_alt = sid in alts
                siblings = sorted({a for g in groups if sid in g for a in g if tax.get(a).category == skill.category} - {sid})
                if key in skill_map:
                    r = skill_map[key]
                    r.mentions += len(spans)
                    if r.example_only and not is_alt:
                        r.example_only, r.alternatives, r.jd_text = False, [], sent
                    elif r.example_only:
                        r.alternatives = sorted(set(r.alternatives) | set(siblings))
                    if importance == "required" and r.importance == "preferred":
                        r.importance = "required"
                        if not r.example_only or is_alt:
                            r.jd_text = sent
                else:
                    skill_map[key] = SkillReq(key, sid, skill.name, skill.category, importance, sent, len(spans),
                                              is_alt, siblings)

            m = re.search(r"(\d+)\s*\+?\s*(?:-\s*\d+\s*)?(?:years|yrs)", sent, re.I)
            if m and re.search(r"experience|professional|industry|engineering|development", sent, re.I):
                yrs = float(m.group(1))
                if req.min_years is None or (importance == "required" and yrs > req.min_years):
                    req.min_years, req.years_text = yrs, sent

            if DEGREE_CUE.search(sent) or BE_RE.search(sent):
                levels = [lv for lv, pat in DEGREE_LEVELS if re.search(pat, sent, re.I)]
                if BE_RE.search(sent):
                    levels.append(1)
                if levels:
                    level = min(levels)  # "BE/BTech/MCA" -> a bachelor's degree is enough
                    if req.degree_level is None or level < req.degree_level:
                        req.degree_level, req.degree_text, req.degree_importance = level, sent, importance
                    req.degree_fields = sorted(set(req.degree_fields) | set(fields_in(sent)))
                elif req.degree_level is not None and fields_in(sent):
                    req.degree_fields = sorted(set(req.degree_fields) | set(fields_in(sent)))

        if section == "responsibilities" or (section == "general" and is_bullet
                                             and tu.verb_tense(tu.first_word(line)) == "present"):
            terms = sorted(tu.content_terms(line))
            if len(terms) >= 3:
                req.responsibilities.append(ResponsibilityReq(f"resp:{len(req.responsibilities) + 1}", line, terms))

    for r in skill_map.values():
        (req.certifications if r.id.startswith("cert:") else req.skills).append(r)
    req.skills.sort(key=lambda r: (r.importance != "required", -r.mentions, r.name))
    return req
