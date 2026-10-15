"""Normalized skill identifiers and matching.

Every skill mention is mapped to a single canonical id so that synonyms
("Postgres", "PostgreSQL") and repeated mentions never earn extra credit.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).with_name("data") / "skills.json"


@dataclass
class Skill:
    id: str
    name: str
    category: str
    aliases: list[str]
    case_sensitive: bool = False
    implies: list[str] = field(default_factory=list)
    implied_phrase: dict[str, str] = field(default_factory=dict)
    weak_evidence: list[dict] = field(default_factory=list)
    learning_project: str = ""
    generic: bool = False  # broad term (e.g. "programming") shown through other skills; never injected as a keyword
    patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    excludes: list[re.Pattern] = field(default_factory=list, repr=False)


def _alias_pattern(alias: str, case_sensitive: bool) -> re.Pattern:
    body = re.escape(alias).replace(r"\ ", r"\s+")
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![A-Za-z0-9+#.])" + body + r"(?![A-Za-z0-9+#]|\.[A-Za-z])", flags)


class Taxonomy:
    def __init__(self, path: Path = _DATA):
        raw = json.loads(path.read_text())
        self.skills: dict[str, Skill] = {}
        for s in raw["skills"]:
            skill = Skill(
                id=s["id"], name=s["name"], category=s["category"], aliases=s["aliases"],
                case_sensitive=s.get("case_sensitive", False), implies=s.get("implies", []),
                implied_phrase=s.get("implied_phrase", {}), weak_evidence=s.get("weak_evidence", []),
                learning_project=s.get("learning_project", ""), generic=s.get("generic", False),
            )
            # Longest aliases first so "REST API" wins over "REST".
            skill.excludes = [re.compile(x, re.I) for x in s.get("exclude", [])]
            skill.patterns = [_alias_pattern(a, skill.case_sensitive)
                              for a in sorted(set(skill.aliases) | {skill.name}, key=len, reverse=True)]
            self.skills[skill.id] = skill
        # Reverse implication index: target -> [sources]
        self.implied_by: dict[str, list[str]] = {}
        for s in self.skills.values():
            for t in s.implies:
                self.implied_by.setdefault(t, []).append(s.id)

    def get(self, skill_id: str) -> Skill:
        return self.skills[skill_id]

    def find(self, text: str) -> dict[str, list[tuple[int, int, str]]]:
        """Return {skill_id: [(start, end, surface), ...]} for all mentions in text."""
        found: dict[str, list[tuple[int, int, str]]] = {}
        for skill in self.skills.values():
            spans: list[tuple[int, int, str]] = []
            blocked = [(m.start(), m.end()) for x in skill.excludes for m in x.finditer(text)]
            for pat in skill.patterns:
                for m in pat.finditer(text):
                    if any(m.start() < e and s < m.end() for s, e, _ in spans) or \
                            any(m.start() < e and s < m.end() for s, e in blocked):
                        continue
                    spans.append((m.start(), m.end(), m.group(0)))
            if spans:
                found[skill.id] = sorted(spans)
        return found

    def ids_in(self, text: str) -> set[str]:
        return set(self.find(text))


@lru_cache(maxsize=1)
def taxonomy() -> Taxonomy:
    return Taxonomy()
