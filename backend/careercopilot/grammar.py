"""Rule-based grammar and professional-writing validation.

Every proposed addition or replacement passes through `validate_text`.
Checks marked "fail" block approval; "warn" checks are shown to the user.
`fixed_text` applies only safe, deterministic corrections (capitalisation,
spacing, tense of the leading verb, canonical skill spelling, terminal
punctuation consistency) and is offered to the user, never applied silently.
"""
from __future__ import annotations

import re
from functools import lru_cache

from . import textutil as tu
from .scoring import has_verb, is_list_like
from .taxonomy import taxonomy

INFORMAL = ["stuff", "things", "lots of", "a lot of", "awesome", "super ", "basically", "really", "kind of",
            "pretty much", "gonna", "wanna", "cool ", "tons of", "etc"]
WORDY = {"in order to": "to", "responsible for": "a direct action verb", "successfully ": "(remove)",
         "various ": "a specific number or list", "utilized": "used", "utilize": "use",
         "was able to": "(remove)", "helped to": "a direct action verb", "worked on": "a specific action verb",
         "a number of": "a specific number"}
VOWEL_SOUND_LETTERS = set("AEFHILMNORSX")
A_EXCEPTIONS = ("user", "use", "unique", "unit", "universal", "university", "one", "once", "uniform", "usa",
                "utility", "euro", "usual")
AN_EXCEPTIONS = ("hour", "honest", "honor", "honour", "heir")


@lru_cache(maxsize=1)
def _spell():
    from spellchecker import SpellChecker
    sp = SpellChecker(distance=1)
    tech = set()
    for s in taxonomy().skills.values():
        for a in s.aliases + [s.name]:
            tech.update(w.lower() for w in re.findall(r"[A-Za-z]+", a))
    tech.update("api apis backend frontend fullstack devops microservice microservices kubernetes dockerized "
                "containerized containerised containerize repo repos github gitlab config configs workflow workflows "
                "dataset datasets scalable onboarding refactored refactor deployable runtime middleware "
                "latency throughput uptime webhook webhooks serverless analytics etl pipelines frontend "
                "e-commerce ecommerce saas sdk sdks cli ui ux qa json yaml csv sql nosql orm crud oauth "
                "jwt https http stakeholders mentored mentoring dashboards sprints scrum kanban linting "
                "async realtime chatbot llm llms ml ai prototyped hackathon js ts".split())
    sp.word_frequency.load_words(tech)
    return sp


def _check(name, status, message, fix=None):
    return {"name": name, "status": status, "message": message, **({"fix": fix} if fix else {})}


def _canonical_skill_fixes(text: str) -> list[tuple[str, str]]:
    fixes = []
    for sid, spans in taxonomy().find(text).items():
        sk = taxonomy().get(sid)
        allowed = set(sk.aliases) | {sk.name}
        for _, _, surface in spans:
            if surface not in allowed:
                best = next((a for a in allowed if a.lower() == surface.lower()), sk.name)
                # Only proper names/acronyms have a required spelling; common words ("mentored") don't.
                # Only brand-style spellings are enforced (JavaScript, MongoDB, jQuery); ordinary title or
                # sentence casing of common terms ("Data Science", "mentored") is left alone.
                brand = any(ch.isupper() for ch in best[1:]) or (best[:1].islower() and any(ch.isupper() for ch in best))
                if best.lower() == surface.lower() and not brand and surface[:1].isupper():
                    continue  # already capitalised common term ("Data Science", "Mentored")
                fixes.append((surface, best))
    return fixes


def expected_tense(neighbors: list[str], current_entry: bool) -> str | None:
    tenses = [tu.verb_tense(tu.first_word(n)) for n in neighbors]
    tenses = [t for t in tenses if t]
    if tenses:
        return max(set(tenses), key=tenses.count)
    return None if current_entry else "past"


def validate_text(text: str, *, kind: str = "bullet", neighbors: list[str] | None = None,
                  current_entry: bool = False, known_words: set[str] | None = None,
                  jd_skill_ids: set[str] | None = None, jd_terms: set[str] | None = None,
                  original_text: str | None = None) -> dict:
    neighbors = neighbors or []
    known = {w.lower() for w in (known_words or set())}
    checks: list[dict] = []
    fixed = text
    t = text.strip()
    letter = kind == "letter"  # cover-letter prose: first person is expected
    if letter:
        kind = "paragraph"
    sentence_kind = kind in ("bullet", "paragraph")

    # --- empty / length -----------------------------------------------------
    if not t:
        return {"passed": False, "checks": [_check("content", "fail", "Text is empty")], "fixed_text": text}

    # --- spelling -------------------------------------------------------------
    sp = _spell()
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", t)
    unknown = []
    for w in tokens:
        lw = w.lower().strip("'-")
        if len(lw) < 3 or lw in known or not lw.isalpha():
            continue
        if w[0].isupper() and w.lower() not in sp and (w in (known_words or set()) or not sp.unknown([lw])):
            continue
        if sp.unknown([lw]):
            unknown.append(w)
    proper = [w for w in unknown if w[0].isupper()]
    misspelled = [w for w in unknown if not w[0].isupper()]
    if misspelled:
        sugg = {w: (sp.correction(w.lower()) or w) for w in misspelled}
        checks.append(_check("spelling", "fail", "Possible misspelling: " +
                             ", ".join(f"'{w}' → '{s}'" for w, s in sugg.items())))
        for w, s in sugg.items():
            if s and s != w.lower():
                fixed = re.sub(rf"\b{re.escape(w)}\b", s, fixed)
    elif proper:
        checks.append(_check("spelling", "warn", f"Unrecognised name(s) {proper}; confirm the spelling"))
    else:
        checks.append(_check("spelling", "pass", "No spelling issues found"))

    # --- punctuation ----------------------------------------------------------
    issues = []
    if "  " in t:
        issues.append("double spaces"); fixed = re.sub(r" {2,}", " ", fixed)
    if re.search(r"\s[,.;:!?]", t):
        issues.append("space before punctuation"); fixed = re.sub(r"\s+([,.;:!?])", r"\1", fixed)
    if re.search(r"([,;:])\1|\.\.(?!\.)|,\.|\.,", t):
        issues.append("repeated punctuation"); fixed = re.sub(r"([,;:])\1+", r"\1", fixed)
    if t.count("(") != t.count(")"):
        issues.append("unbalanced parentheses")
    if t.count('"') % 2:
        issues.append("unbalanced quotes")
    if re.search(r",(?=[A-Za-z])", t):
        issues.append("missing space after comma"); fixed = re.sub(r",(?=[A-Za-z])", ", ", fixed)
    checks.append(_check("punctuation", "fail" if issues else "pass",
                         ("Punctuation issues: " + ", ".join(issues)) if issues else "Punctuation is correct"))

    # terminal punctuation consistency with neighbouring bullets
    if sentence_kind and neighbors:
        ends = [n.rstrip().endswith(".") for n in neighbors if n.strip()]
        want_period = sum(ends) > len(ends) / 2
        has_period = t.endswith(".")
        if want_period != has_period:
            checks.append(_check("consistency", "warn",
                                 "Neighbouring bullets " + ("end" if want_period else "do not end") +
                                 " with a period; match them"))
            fixed = fixed.rstrip().rstrip(".") + ("." if want_period else "")
        else:
            checks.append(_check("consistency", "pass", "Terminal punctuation matches neighbouring bullets"))

    # --- capitalisation -------------------------------------------------------
    if sentence_kind and t[0].isalpha() and not t[0].isupper():
        checks.append(_check("capitalization", "fail", "Start the sentence with a capital letter"))
        fixed = fixed[0].upper() + fixed[1:]

    # --- tense & action verb ---------------------------------------------------
    if kind == "bullet":
        fw = tu.first_word(t)
        tense = tu.verb_tense(fw)
        want = expected_tense(neighbors, current_entry)
        if tense is None:
            checks.append(_check("action_verb", "warn",
                                 f"Bullets read best when they start with an action verb (starts with '{fw}')"))
        else:
            checks.append(_check("action_verb", "pass", f"Starts with the action verb '{fw}'"))
        if tense and want and tense != want:
            repl = tu.to_past(fw) if want == "past" else tu.to_present(fw)
            checks.append(_check("verb_tense", "fail",
                                 f"Use {want} tense to match this role's other bullets ('{fw}' → '{repl}')"))
            fixed = re.sub(rf"\b{re.escape(fw)}\b", repl[0].upper() + repl[1:] if fixed[:1].isupper() else repl,
                           fixed, count=1, flags=re.I)
        else:
            checks.append(_check("verb_tense", "pass", "Verb tense is consistent with this section"))

    # --- sentence structure ------------------------------------------------------
    if sentence_kind:
        if is_list_like(t):
            checks.append(_check("sentence_structure", "fail",
                                 "Reads as a keyword list rather than a sentence; describe what you did"))
        elif not has_verb(t):
            checks.append(_check("sentence_structure", "fail", "No verb found; write a complete statement"))
        else:
            checks.append(_check("sentence_structure", "pass", "Complete, well-formed statement"))

    # --- subject-verb agreement & articles --------------------------------------
    agr = []
    if re.search(r"\bI\s+(is|has)\b|\b(we|they|you)\s+(is|was|has)\b", t, re.I):
        agr.append("plural subject with singular verb")
    if re.search(r"\b(he|she|it|this|that)\s+(are|were|have)\b", t, re.I):
        agr.append("singular subject with plural verb")
    if re.search(r"\b(data|services|teams|systems|users)\s+(is|was|has)\b", t, re.I) and \
            not re.search(r"\bdata\s+(is|was)\b", t, re.I):
        agr.append("plural noun with singular verb")
    checks.append(_check("subject_verb_agreement", "fail" if agr else "pass",
                         ("Agreement issue: " + ", ".join(agr)) if agr else "Subjects and verbs agree"))
    art = []
    for m in re.finditer(r"\b(a|an)\s+([A-Za-z][\w/.-]*)", t, re.I):
        art_w, nxt = m.group(1).lower(), m.group(2)
        if nxt.isupper() and len(nxt) > 1:
            vowel = nxt[0] in VOWEL_SOUND_LETTERS
        else:
            ln = nxt.lower()
            vowel = ln[0] in "aeiou" and not ln.startswith(A_EXCEPTIONS)
            vowel = vowel or ln.startswith(AN_EXCEPTIONS)
        if art_w == "a" and vowel:
            art.append(f"'a {nxt}' → 'an {nxt}'"); fixed = fixed.replace(m.group(0), f"{m.group(1)[0]}n {nxt}", 1)
        elif art_w == "an" and not vowel:
            art.append(f"'an {nxt}' → 'a {nxt}'"); fixed = fixed.replace(m.group(0), f"{m.group(1)[0]} {nxt}", 1)
    if art:
        checks.append(_check("articles", "fail", "Article usage: " + "; ".join(art)))
    if re.search(r"\b(\w+)\s+\1\b", t, re.I):
        checks.append(_check("repeated_word", "fail", "A word is repeated"))
        fixed = re.sub(r"\b(\w+)\s+\1\b", r"\1", fixed, flags=re.I)

    # --- professional tone ------------------------------------------------------
    tone = []
    if sentence_kind and not letter and re.search(r"\b(I|me|my|we|our)\b", t, re.I):
        tone.append("first-person pronoun (resume bullets omit 'I'/'we')")
    if "!" in t:
        tone.append("exclamation mark")
    low = " " + t.lower() + " "
    tone += [f"informal wording '{w.strip()}'" for w in INFORMAL if f" {w}" in low]
    checks.append(_check("professional_tone", "fail" if tone else "pass",
                         ("Tone: " + "; ".join(tone)) if tone else "Professional tone"))

    # --- conciseness & redundancy -----------------------------------------------
    n = len(t.split())
    if sentence_kind:
        limit = 120 if letter else 35
        if n > limit:
            checks.append(_check("conciseness", "warn", f"{n} words; aim for at most {limit}"))
        elif kind == "bullet" and n < 6:
            checks.append(_check("conciseness", "warn", f"Only {n} words; add the outcome or context"))
        else:
            checks.append(_check("conciseness", "pass", f"{n} words"))
    wordy = [f"'{k.strip()}' → {v}" for k, v in WORDY.items() if k in low]
    if wordy:
        checks.append(_check("redundant_wording", "warn", "Tighten wording: " + "; ".join(wordy)))
    else:
        checks.append(_check("redundant_wording", "pass", "No redundant phrases"))

    # --- terminology & keyword stuffing -----------------------------------------
    term_fixes = _canonical_skill_fixes(t)
    if term_fixes:
        checks.append(_check("technical_terminology", "fail",
                             "Use standard spelling: " + ", ".join(f"'{a}' → '{b}'" for a, b in term_fixes)))
        for a, b in term_fixes:
            fixed = re.sub(rf"(?<![\w]){re.escape(a)}(?![\w])", b, fixed)
    else:
        checks.append(_check("technical_terminology", "pass", "Technical terms use standard spelling"))
    found = taxonomy().find(t)
    before = taxonomy().find(original_text) if original_text else {}
    def _same_surface_repeats(spans):
        surfaces = [sp[2].lower() for sp in spans]
        return max(surfaces.count(x) for x in surfaces) if surfaces else 0
    # Stuffing = the same phrase repeated (not related wording such as "test cases" + "testing").
    repeated = [taxonomy().get(s).name for s, spans in found.items()
                if _same_surface_repeats(spans) > 1
                and _same_surface_repeats(spans) > _same_surface_repeats(before.get(s, []))]
    skill_chars = sum(e - s for spans in found.values() for s, e, _ in spans)
    stuffing = []
    if repeated and sentence_kind:
        stuffing.append(f"repeats {', '.join(repeated)}")
    if sentence_kind and skill_chars > 0.5 * len(t):
        stuffing.append("more than half of the text is keywords")
    if sentence_kind and original_text is not None:
        new_ids = set(found) - taxonomy().ids_in(original_text)
        if len(new_ids) >= 3:
            stuffing.append(f"adds {len(new_ids)} keywords to one sentence")
    paren = re.findall(r"\(([^)]*)\)", t)
    if sentence_kind and any(len(taxonomy().ids_in(p)) > 1 for p in paren):
        stuffing.append("several keywords packed into parentheses")
    if kind == "skills_line":
        items = [i.strip().lower() for i in re.split(r"[,|;•]", t.split(":", 1)[-1]) if i.strip()]
        if len(items) != len(set(items)):
            stuffing.append("duplicate item in skills line")
    checks.append(_check("keyword_stuffing", "fail" if stuffing else "pass",
                         ("Awkward keyword insertion: " + "; ".join(stuffing)) if stuffing
                         else "Keywords are integrated naturally"))

    # --- relevance -----------------------------------------------------------------
    if jd_skill_ids is not None:
        rel = set(found) & jd_skill_ids or (jd_terms and tu.content_terms(t) & jd_terms)
        checks.append(_check("relevance", "pass" if rel else "warn",
                             "Relevant to the target job" if rel else "Does not mention a requirement of the target job"))

    passed = not any(c["status"] == "fail" for c in checks)
    return {"passed": passed, "checks": checks, "fixed_text": fixed if fixed != text else None}
