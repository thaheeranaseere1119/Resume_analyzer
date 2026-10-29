"""Live job postings (JSearch): normalising, source links, ranking by ATS score, caching, errors."""
import pytest
from fastapi.testclient import TestClient

from careercopilot import api, jobsearch
from careercopilot.service import Session

from conftest import fixture_bytes

STRONG = ("We are hiring a backend engineer. Requirements: 3+ years of Python and Flask, PostgreSQL and SQL, "
          "REST APIs, Git, Docker. Bachelor's degree in Computer Science.")
WEAK = ("UI/UX Designer. Requirements: Figma, Adobe XD, Sketch, user research, wireframing, prototyping, "
        "design systems. Bachelor's degree in Design.")


def _raw(i, desc, publisher, options=()):
    return {"job_id": f"j{i}", "job_title": f"Job {i}", "employer_name": f"Co {i}", "job_publisher": publisher,
            "job_apply_link": f"https://example.com/{publisher.lower()}/{i}", "job_city": "Chennai",
            "job_country": "IN", "job_description": desc, "job_posted_at_datetime_utc": "2026-10-20T00:00:00Z",
            "apply_options": [{"publisher": p, "apply_link": u} for p, u in options]}


RAW = [
    _raw(1, WEAK, "LinkedIn"),
    _raw(2, STRONG, "Naukri.com", [("Indeed", "https://in.indeed.com/viewjob?jk=2"),
                                   ("LinkedIn", "https://www.linkedin.com/jobs/view/2")]),
    _raw(3, STRONG.replace("Docker", "Kubernetes, Terraform, Go"), "Indeed"),
    _raw(4, STRONG, "Glassdoor"),  # another board -> kept under "other"
]


@pytest.fixture
def fake_api(monkeypatch):
    calls = []
    monkeypatch.setenv("JSEARCH_API_KEY", "test-key")
    monkeypatch.setattr(jobsearch, "_fetch", lambda q, c, p: calls.append((q, c)) or (list(RAW) if "via" not in q else []))
    return calls


def _session(jd_text):
    return Session.create(fixture_bytes("classic.docx"), "classic.docx", jd_text)


def test_links_are_collected_per_site():
    j = jobsearch.normalise(RAW[1])
    assert set(j["links"]) == {"naukri", "indeed", "linkedin"}
    assert j["links"]["linkedin"] == "https://www.linkedin.com/jobs/view/2"
    other = jobsearch.normalise(RAW[3])
    assert other["links"] == {"other": "https://example.com/glassdoor/4"} and other["other_site"] == "Glassdoor"


def test_jobs_ranked_eligible_first_then_by_score(data_dir, jd_text, fake_api):
    r = _session(jd_text).live_jobs("Backend Developer", "Chennai")
    ids = [j["id"] for j in r["jobs"]]
    assert ids[0] in ("j2", "j4") and ids[-1] == "j1" and "j4" in ids
    assert r["jobs"][0]["eligible"] and not r["jobs"][-1]["eligible"]
    keys = [(not j["eligible"], j["low_detail"], -j["score"]) for j in r["jobs"]]
    assert keys == sorted(keys)
    assert r["counts"] == {"linkedin": 2, "indeed": 2, "naukri": 1, "other": 1}
    assert fake_api == [("Backend Developer jobs in Chennai", "in"), ("Backend Developer jobs in Chennai via LinkedIn", "in")]
    assert r["site_search"]["naukri"] == "https://www.naukri.com/backend-developer-jobs-in-chennai"
    assert r["site_search"]["indeed"].startswith("https://in.indeed.com/jobs?q=Backend+Developer&l=Chennai")


def test_filter_by_site_and_cache(data_dir, jd_text, fake_api):
    s = _session(jd_text)
    naukri = s.live_jobs("Backend Developer", "Chennai", "naukri")["jobs"]
    assert [j["id"] for j in naukri] == ["j2"]
    s.live_jobs("Backend Developer", "Chennai", "linkedin")
    assert len(fake_api) == 2  # one search (+ LinkedIn top-up); the second click is served from the SQLite cache


def test_tailor_creates_session_for_posting(data_dir, jd_text, fake_api):
    s = _session(jd_text)
    s.live_jobs("Backend Developer", "Chennai")
    s2 = s.tailor_for_posting("Backend Developer", "Chennai", "j2")
    assert s2.sid != s.sid and s2.state["company"] == "Co 2" and "Flask" in s2.state["jd_text"]


def test_no_key_and_api_errors_are_friendly(data_dir, jd_text, monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    sid = _session(jd_text).sid
    c = TestClient(api.app, raise_server_exceptions=False)
    r = c.get(f"/api/sessions/{sid}/live-jobs", params={"role": "Backend Developer"})
    assert r.status_code == 503 and "JSEARCH_API_KEY" in r.json()["error"]
    monkeypatch.setenv("JSEARCH_API_KEY", "k")

    def boom(*a):
        raise jobsearch.JobSearchError("Live job search limit reached for now (API quota). Try again later.", 429)
    monkeypatch.setattr(jobsearch, "_fetch", boom)
    r = c.get(f"/api/sessions/{sid}/live-jobs", params={"role": "Backend Developer", "location": "Pune"})
    assert r.status_code == 429 and "limit" in r.json()["error"]
    assert c.get("/api/meta").json()["live_jobs"]["configured"]
