"""Scoring configuration.

The weights below are initial project assumptions, not universal ATS rules.
They are configurable through the CAREERCOPILOT_WEIGHTS environment variable
(JSON object) but are frozen per analysis session so that the score before
and after an edit is always computed with the same methodology.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

METHODOLOGY_VERSION = "ccai-score-1.2"  # 1.1: language-quality check; 1.2: "any one of" skill lists

DEFAULT_WEIGHTS = {
    "keyword_coverage": 0.30,
    "contextual_relevance": 0.25,
    "experience_alignment": 0.20,
    "education_certifications": 0.10,
    "formatting_parsing": 0.15,
}

COMPONENT_LABELS = {
    "keyword_coverage": "Keyword and skill coverage",
    "contextual_relevance": "Contextual skill relevance",
    "experience_alignment": "Experience and responsibility alignment",
    "education_certifications": "Education and certifications",
    "formatting_parsing": "Formatting and parsing quality",
}

# Evidence levels used by the contextual-relevance component. A skill earns
# credit once, at the strongest level it reaches (no double counting).
EVIDENCE_LEVELS = {
    "demonstrated": 1.0,   # used in an experience/project sentence
    "contextual": 0.7,     # summary sentence or a role's tech-stack line
    "implied": 0.5,        # not named, but a skill that implies it is demonstrated
    "listed": 0.35,        # skills list / keyword list only (unsupported claim)
    "none": 0.0,
}

IMPORTANCE_WEIGHTS = {"required": 1.0, "preferred": 0.5}

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("CAREERCOPILOT_DATA", ROOT / "data"))


def load_weights() -> dict[str, float]:
    raw = os.environ.get("CAREERCOPILOT_WEIGHTS")
    weights = dict(DEFAULT_WEIGHTS)
    if raw:
        weights.update({k: float(v) for k, v in json.loads(raw).items() if k in weights})
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("Scoring weights must sum to a positive number")
    return {k: v / total for k, v in weights.items()}
