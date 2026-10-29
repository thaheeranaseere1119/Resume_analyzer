"""Live job listings via the JSearch API (RapidAPI), which searches Google for Jobs.

LinkedIn, Indeed and Naukri do not offer public job-search APIs (and their terms forbid scraping).
JSearch returns real postings with their apply links: LinkedIn postings come through well, plus
other boards (Shine, Foundit, Glassdoor, company career pages). Naukri and Indeed listings are
currently not indexed by Google for Jobs in India, so for those the app links to the site's own
live search for the role and location. Set JSEARCH_API_KEY (your RapidAPI key) to enable it.

Results are cached in the SQLite table `job_search_cache` for CACHE_TTL seconds so repeated
clicks do not use up the API quota.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db

log = logging.getLogger("careercopilot")

API_HOST = "jsearch.p.rapidapi.com"
API_URL = f"https://{API_HOST}/search-v2"
CACHE_TTL = 6 * 3600
TIMEOUT = 20

# Publisher names as JSearch reports them, mapped to the three supported sources.
SOURCES = {
    "linkedin": ("LinkedIn", re.compile(r"linkedin", re.I)),
    "indeed": ("Indeed", re.compile(r"indeed", re.I)),
    "naukri": ("Naukri", re.compile(r"naukri", re.I)),
}


class JobSearchError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def api_key() -> str | None:
    return os.environ.get("JSEARCH_API_KEY") or os.environ.get("RAPIDAPI_KEY")


def configured() -> bool:
    return bool(api_key())


def _fetch(query: str, country: str, pages: int) -> list[dict]:
    params = {"query": query, "page": "1", "num_pages": str(pages), "date_posted": "month"}
    if country:
        params["country"] = country
    req = urllib.request.Request(f"{API_URL}?{urllib.parse.urlencode(params)}",
                                 headers={"X-RapidAPI-Key": api_key() or "", "X-RapidAPI-Host": API_HOST})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise JobSearchError("The job-search API key was rejected. Check JSEARCH_API_KEY and that you "
                                 "are subscribed to JSearch on RapidAPI.", 502)
        if exc.code == 429:
            raise JobSearchError("Live job search limit reached for now (API quota). Try again later.", 429)
        raise JobSearchError(f"Live job search failed (API error {exc.code}). Try again shortly.", 502)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise JobSearchError("Couldn't reach the live job-search service. Check your internet connection.", 503)
    except json.JSONDecodeError:
        raise JobSearchError("The job-search service returned an unreadable response.", 502)
    if not isinstance(payload, dict) or payload.get("status") not in (None, "OK"):
        raise JobSearchError("The job-search service returned an error. Try again shortly.", 502)
    data = payload.get("data") or []
    return data.get("jobs") or [] if isinstance(data, dict) else data   # v2: {"jobs": [...], "cursor": ...}


def _source_of(name: str) -> str | None:
    return next((k for k, (_, pat) in SOURCES.items() if pat.search(name or "")), None)


def normalise(raw: dict) -> dict:
    """Keep the fields the app needs; collect the apply link for each site that lists the posting."""
    links: dict[str, str] = {}
    other_name, other_url = "", ""
    for opt in [{"publisher": raw.get("job_publisher"), "apply_link": raw.get("job_apply_link")},
                *(raw.get("apply_options") or [])]:
        name, url = opt.get("publisher") or "", opt.get("apply_link") or ""
        if not url.startswith(("https://", "http://")):
            continue
        src = _source_of(name) or _source_of(urllib.parse.urlparse(url).netloc)
        if src and src not in links:
            links[src] = url
        elif not src and not other_url:
            other_name, other_url = name or urllib.parse.urlparse(url).netloc, url
    if other_url:
        links["other"] = other_url
    hl = raw.get("job_highlights") or {}
    description = raw.get("job_description") or ""
    # Qualifications are where requirements live; add them so short descriptions still score fairly.
    extra = "\n".join(f"- {q}" for q in (hl.get("Qualifications") or []) if q not in description)
    location = raw.get("job_location") or ", ".join(
        x for x in (raw.get("job_city"), raw.get("job_state"), raw.get("job_country")) if x)
    return {
        "id": raw.get("job_id") or "",
        "title": raw.get("job_title") or "Untitled role",
        "company": raw.get("employer_name") or "",
        "logo": raw.get("employer_logo") or "",
        "location": location + (" · Remote" if raw.get("job_is_remote") else ""),
        "employment_type": raw.get("job_employment_type") or "",
        "posted": raw.get("job_posted_at_datetime_utc") or "",
        "publisher": raw.get("job_publisher") or "",
        "salary": raw.get("job_salary_string") or "",
        "links": links,
        "other_site": other_name,
        "description": (description + ("\nRequirements:\n" + extra if extra else ""))[:20000],
    }


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


INDEED_DOMAINS = {"in": "in.indeed.com", "us": "www.indeed.com", "gb": "uk.indeed.com", "ca": "ca.indeed.com",
                  "au": "au.indeed.com", "sg": "sg.indeed.com", "ae": "ae.indeed.com", "de": "de.indeed.com"}


def site_searches(role: str, location: str, country: str = "in") -> dict[str, str]:
    """Each site's own live search page for this role and location (used where no API data exists)."""
    q = urllib.parse.quote_plus
    naukri = f"https://www.naukri.com/{_slug(role)}-jobs" + (f"-in-{_slug(location)}" if location else "")
    return {"linkedin": f"https://www.linkedin.com/jobs/search/?keywords={q(role)}&location={q(location)}",
            "indeed": f"https://{INDEED_DOMAINS.get(country, 'www.indeed.com')}/jobs?q={q(role)}&l={q(location)}",
            "naukri": naukri}


def search(role: str, location: str, country: str = "in", pages: int = 2) -> list[dict]:
    """Live postings for `role` in `location` that can be applied to on LinkedIn, Indeed or Naukri."""
    if not configured():
        raise JobSearchError("Live jobs need a JSearch API key. Get a free key at rapidapi.com (JSearch), "
                             "then restart the server with JSEARCH_API_KEY set.", 503)
    role = re.sub(r"\s+", " ", role or "").strip()[:120]
    location = re.sub(r"\s+", " ", location or "").strip()[:80]
    if not role:
        raise JobSearchError("Choose a role to search for", 400)
    query = f"{role} jobs in {location}" if location else f"{role} jobs"
    key = f"v2|{query.lower()}|{country.lower()}|{pages}"
    cached = db.cache_get("job_search_cache", key, CACHE_TTL)
    if cached is not None:
        return cached
    # The general search mixes many boards; a second "via LinkedIn" search fills in LinkedIn postings.
    raw = _fetch(query, country, pages)
    try:
        raw += _fetch(f"{query} via LinkedIn", country, 1)
    except JobSearchError as exc:
        log.warning("LinkedIn-focused search failed: %s", exc)
    jobs, seen = [], set()
    for j in (normalise(r) for r in raw):
        keys = {j["id"], (re.sub(r"\W+", " ", j["title"].lower()).strip(), j["company"].lower())} - {""}
        if j["links"] and not keys & seen:
            seen |= keys
            jobs.append(j)
    db.cache_put("job_search_cache", key, jobs)
    return jobs
