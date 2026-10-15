"""Scoring engine: weights, bounds, no double counting, evidence quality, determinism."""
import pytest

from careercopilot.config import DEFAULT_WEIGHTS, load_weights
from careercopilot.docx_io import read_docx
from careercopilot.jd import parse_jd
from careercopilot.scoring import collect_evidence, is_list_like, score_delta, score_resume
from careercopilot.taxonomy import taxonomy

from conftest import AS_OF, fixture_bytes


@pytest.fixture
def model():
    return read_docx(fixture_bytes("classic.docx"), AS_OF)


def _bullet(m, s):
    return next(b for b in m.blocks if b.kind == "bullet" and s in b.text)


def test_weights_documented_and_normalised():
    assert DEFAULT_WEIGHTS == {"keyword_coverage": .30, "contextual_relevance": .25, "experience_alignment": .20,
                               "education_certifications": .10, "formatting_parsing": .15}
    assert abs(sum(load_weights().values()) - 1) < 1e-9


def test_overall_is_weighted_sum_and_bounded(model, jd, weights):
    s = score_resume(model, jd, weights)
    app = [c for c in s.components.values() if c.applicable]
    expected = 100 * sum(c.score * c.weight for c in app) / sum(c.weight for c in app)
    assert s.overall == round(expected, 1)
    assert 0 <= s.overall <= 100


def test_deterministic(model, jd, weights):
    assert score_resume(model, jd, weights).to_dict() == score_resume(model.clone(), jd, weights).to_dict()


def test_normalised_skill_ids_merge_synonyms():
    t = taxonomy()
    assert t.ids_in("Postgres") == t.ids_in("PostgreSQL") == {"postgresql"}
    assert "java" not in t.ids_in("JavaScript")
    assert t.ids_in("RESTful APIs") == {"rest_api"}


def test_repeating_a_keyword_adds_nothing(model, jd, weights):
    """Python is already demonstrated; mentioning it again must give 0 points."""
    base = score_resume(model, jd, weights)
    b = _bullet(model, "Wrote automated tests")
    sim = model.with_replaced(b.id, b.body.rstrip(".") + " in Python, Python and Python.")
    after = score_resume(sim, jd, weights)
    assert score_delta(base, after)["components"]["keyword_coverage"]["points"] == 0
    assert score_delta(base, after)["components"]["contextual_relevance"]["points"] == 0


def test_adding_existing_skill_to_skills_list_is_zero_impact(model, jd, weights):
    base = score_resume(model, jd, weights)
    line = next(b for b in model.blocks if b.kind == "skills_line" and "Python" in b.text)
    sim = model.with_replaced(line.id, line.body + ", Python")
    # no gain for a duplicate; the duplicate itself is a small language-quality issue
    assert score_delta(base, score_resume(sim, jd, weights))["points"] <= 0.0


def test_skills_list_mention_earns_less_than_demonstrated_use(model, jd, weights):
    """Unsupported (listed-only) claims never get full credit."""
    line = next(b for b in model.blocks if b.kind == "skills_line" and "PostgreSQL" in b.text)
    listed = score_resume(model.with_replaced(line.id, line.body + ", Docker"), jd, weights)
    b = _bullet(model, "Containerized")
    shown = score_resume(model.with_replaced(b.id, "Containerized three legacy services with Docker and migrated them to the cloud."), jd, weights)
    assert collect_evidence(model.with_replaced(line.id, line.body + ", Docker"))["docker"].level == "listed"
    assert shown.overall > listed.overall
    assert shown.components["keyword_coverage"].score == listed.components["keyword_coverage"].score


def test_keyword_list_bullet_is_not_demonstration(model, jd, weights):
    assert is_list_like("Java, REST API, SQL developed application and database.")
    b = _bullet(model, "Wrote automated tests")
    stuffed = model.with_replaced(b.id, "Java, REST API, SQL developed application and database.")
    assert collect_evidence(stuffed)["java"].level == "listed"


def test_implied_skill_partial_credit(model):
    ev = collect_evidence(model)
    assert ev["sql"].level == "implied" and ev["sql"].via == "postgresql"
    assert not ev["sql"].literal


def test_relative_vs_absolute_change():
    class R:  # minimal ScoreResult stand-ins
        def __init__(self, o):
            self.overall, self.components, self.requirements = o, {}, []
    d = score_delta(R(76.0), R(79.0))
    assert d["points"] == 3.0
    assert d["relative_percent"] == pytest.approx(3.95, abs=0.01)


def test_jd_parsing_importance(jd):
    imp = {r.skill_id: r.importance for r in jd.skills}
    assert imp["docker"] == "required" and imp["kubernetes"] == "preferred" and imp["react"] == "preferred"
    assert jd.min_years == 3 and jd.degree_level == 1
    assert [c.skill_id for c in jd.certifications] == ["aws_certified"]


def test_methodology_constant_across_edits(model, jd, weights):
    a = score_resume(model, jd, weights)
    b = score_resume(model.with_inserted_after(_bullet(model, "Containerized").id, "Shipped a Docker image."), jd, weights)
    assert a.methodology == b.methodology and a.weights == b.weights
