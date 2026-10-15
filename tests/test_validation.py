"""Grammar / professional-writing validation and factual verification."""
import pytest

from careercopilot.docx_io import read_docx
from careercopilot.factual import validate_claims
from careercopilot.grammar import validate_text

from conftest import AS_OF, fixture_bytes

NEIGHBORS = ["Built order-processing services in Python and Flask.", "Designed PostgreSQL schemas for reports."]


def _fails(r):
    return {c["name"] for c in r["checks"] if c["status"] == "fail"}


def test_good_sentence_passes():
    r = validate_text("Developed a Java application with REST API integration and SQL-based data persistence.",
                      neighbors=NEIGHBORS)
    assert r["passed"], r


def test_keyword_list_rejected():
    r = validate_text("Java, REST API, SQL developed application and database.", neighbors=NEIGHBORS)
    assert "sentence_structure" in _fails(r)


@pytest.mark.parametrize("text,check", [
    ("Containerized three legacy servces with Docker.", "spelling"),
    ("Containerized services with Docker ,and AWS.", "punctuation"),
    ("containerized services with Docker.", "capitalization"),
    ("Containerize services with Docker for the platform team.", "verb_tense"),
    ("Deployed a AWS stack for the analytics team.", "articles"),
    ("Deployed services that we has built for the analytics team.", "subject_verb_agreement"),
    ("I deployed Docker services for the analytics team.", "professional_tone"),
    ("Deployed awesome stuff with Docker for the team!", "professional_tone"),
    ("Deployed services with docker for the analytics team.", "technical_terminology"),
    ("Deployed Docker services and Docker images with Docker.", "keyword_stuffing"),
    ("Deployed the the services for the analytics team.", "repeated_word"),
])
def test_specific_failures(text, check):
    r = validate_text(text, neighbors=NEIGHBORS)
    assert check in _fails(r), r["checks"]
    assert not r["passed"]


def test_fixes_are_offered_not_applied():
    r = validate_text("containerize three legacy servces with docker  to a AWS cloud", neighbors=NEIGHBORS)
    assert r["fixed_text"] == "Containerized three legacy services with Docker to an AWS cloud."


def test_present_tense_allowed_for_current_role():
    r = validate_text("Lead migration of services to Docker for the platform team.", neighbors=[], current_entry=True)
    assert "verb_tense" not in _fails(r)


def test_redundant_wording_warned():
    r = validate_text("Responsible for deploying services in order to improve uptime.", neighbors=NEIGHBORS)
    assert any(c["name"] == "redundant_wording" and c["status"] == "warn" for c in r["checks"])


# ---------------------------------------------------------------------- factual
@pytest.fixture
def model():
    return read_docx(fixture_bytes("classic.docx"), AS_OF)


def _orig(model, s):
    return next(b for b in model.blocks if s in b.text).body


def test_supported_by_existing_evidence(model):
    orig = _orig(model, "PostgreSQL schemas")
    r = validate_claims(orig.replace("PostgreSQL", "PostgreSQL (SQL)"), model=model, mode="replace",
                        original_text=orig, confirmations={}, denials=set())
    assert r["status"] == "evidence" and r["applicable"]


def test_requires_clarification_without_evidence(model):
    orig = _orig(model, "Containerized")
    r = validate_claims("Containerized three legacy services with Docker and migrated them to the cloud.",
                        model=model, mode="replace", original_text=orig, confirmations={}, denials=set())
    assert r["status"] == "clarification" and not r["applicable"]


def test_supported_by_user_confirmation(model):
    orig = _orig(model, "Containerized")
    r = validate_claims("Containerized three legacy services with Docker and migrated them to the cloud.",
                        model=model, mode="replace", original_text=orig,
                        confirmations={"docker": {"text": "Yes, Docker Compose"}}, denials=set())
    assert r["status"] == "confirmation" and r["applicable"]


def test_stylistic_only(model):
    orig = _orig(model, "Wrote automated tests")
    r = validate_claims("Wrote automated tests that increased coverage from 45% to 80%.", model=model,
                        mode="replace", original_text=orig, confirmations={}, denials=set())
    assert r["status"] == "stylistic" and r["applicable"]


def test_unsupported_when_denied(model):
    orig = _orig(model, "Containerized")
    r = validate_claims("Containerized three legacy services with Kubernetes.", model=model, mode="replace",
                        original_text=orig, confirmations={}, denials={"kubernetes"})
    assert r["status"] == "unsupported" and not r["applicable"]


def test_invented_metric_requires_clarification(model):
    orig = _orig(model, "Containerized")
    r = validate_claims("Containerized three legacy services, cutting costs by 63%.", model=model, mode="replace",
                        original_text=orig, confirmations={}, denials=set())
    assert any(c["type"] == "metric" and c["status"] == "clarification" for c in r["claims"])
    assert not r["applicable"]
