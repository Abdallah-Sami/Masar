"""
Workable job board (jobs.workable.com): every company that hires through
Workable and posts a job in Saudi Arabia shows up here, in one public list.

Discovery + details in one go: the search API already returns the full job
(title, company, city, work mode, description). One request per 20 jobs, so a
full pass over ~3,500 Saudi jobs takes a few minutes.

* Only jobs not seen before are saved (checkpoint), like the other sources.
* The full list of open jobs is saved too (data/active/workable.json), so jobs
  that disappear from Workable are hidden from the site.

Env: WORKABLE_MAX_PAGES (default 400), WORKABLE_MAX_JOBS (default 0 = no cap).
"""
from __future__ import annotations

import sys
import time

from masar.sources.base import SourceRun, env_int, get_logger, make_session

SOURCE = "workable"
API = "https://jobs.workable.com/api/v1/jobs"
COUNTRY = "Saudi Arabia"
PAGE_DELAY = 1.5
log = get_logger(SOURCE)


def to_record(job: dict) -> dict:
    loc = job.get("location") or {}
    company = job.get("company") or {}
    parts = [job.get("description"), job.get("requirementsSection"), job.get("benefitsSection")]
    return {
        "id": job.get("id"),
        "url": job.get("url"),
        "title": job.get("title"),
        "company": company.get("title"),
        "city": loc.get("city") or loc.get("subregion"),
        "country": loc.get("countryName"),
        "work_mode": job.get("workplace"),            # on_site / remote / hybrid
        "employment_type": job.get("employmentType"),
        "department": job.get("department"),
        "description": "\n".join(p for p in parts if p) or None,
        "posted_date": job.get("created"),
    }


def is_saudi(job: dict) -> bool:
    loc = job.get("location") or {}
    if (loc.get("countryName") or "") == COUNTRY:
        return True
    return any(COUNTRY in str(x) for x in job.get("locations") or [])


def fetch_all(run: SourceRun) -> tuple[list[dict], bool]:
    """All Saudi jobs on the board -> (jobs, complete?)."""
    session = make_session()
    max_pages = env_int("WORKABLE_MAX_PAGES", 400)
    jobs, seen_ids, token, total = [], set(), None, None
    for page in range(1, max_pages + 1):
        params = {"location": COUNTRY}
        if token:
            params["pageToken"] = token
        try:
            r = session.get(API, params=params, timeout=30)
        except Exception as e:
            log.warning("Page %d failed: %s", page, e)
            return jobs, False
        if r.status_code != 200:
            log.warning("Page %d: HTTP %s. Stopping.", page, r.status_code)
            return jobs, False
        data = r.json()
        total = data.get("totalSize", total)
        batch = data.get("jobs") or []
        fresh = [j for j in batch if j.get("id") not in seen_ids]
        if batch and not fresh:
            log.warning("Page %d repeats earlier jobs: paging stopped working.", page)
            return jobs, False
        seen_ids.update(j.get("id") for j in fresh)
        jobs.extend(j for j in fresh if is_saudi(j))
        log.info("Page %d: %d jobs (%d Saudi so far, board total %s)", page, len(batch), len(jobs), total)
        token = data.get("nextPageToken")
        if not batch or not token:
            return jobs, True
        if run.should_stop():
            return jobs, False
        time.sleep(PAGE_DELAY)
    return jobs, False


def run() -> int:
    run_ = SourceRun(SOURCE, log=log, flush_every=200)
    try:
        jobs, complete = fetch_all(run_)
        run_.listings_seen = len(jobs)
        if complete and jobs:
            run_.active_urls = {j["url"] for j in jobs if j.get("url")}
        new = [j for j in jobs if j.get("url") and j["url"] not in run_.known_urls]
        cap = env_int("WORKABLE_MAX_JOBS", 0)
        new = new[:cap] if cap else new
        run_.discovered = len(new)
        log.info("Saudi jobs %d | new %d | full list: %s", len(jobs), len(new), complete)
        for j in new:
            run_.success(j["url"], to_record(j), required=("title",))
    finally:
        code = run_.finish()
    return code


if __name__ == "__main__":
    sys.exit(run())
