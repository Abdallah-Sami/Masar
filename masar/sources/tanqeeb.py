"""
Tanqeeb (saudi.tanqeeb.com) scraper.

Discovery: paginate the Saudi search results with plain requests.
Details:   fetch each new job page and parse it with BeautifulSoup.

Run:  python -m masar.sources.tanqeeb
Env:  TANQEEB_MAX_JOBS (default 300), TANQEEB_MAX_PAGES (default 1000),
      TANQEEB_STOP_AFTER_KNOWN_PAGES (default 40), PROXY_* (optional)
"""
from __future__ import annotations

import sys
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil.parser import parse as parse_date

from masar.sources.base import SourceRun, env_int, get_logger, make_session
from masar.sources.fetcher import Fetcher

SOURCE = "tanqeeb"
BASE_URL = "https://saudi.tanqeeb.com"
SEARCH_PATH = f"{BASE_URL}/jobs/search"
QUERY_STRING = ("keywords=&country=54&state=0&category=-1&"
                "workplace=0&search_period=0&lang=all&change_lang=1")
ALLOWED_NETLOC = "saudi.tanqeeb.com"

# Ordered from most to least specific. The old catch-all `a[href*='/jobs/']`
# was removed: it matched nav/footer links once real listings ran out.
JOB_LINK_SELECTORS = (
    "h2.search-job-title a.search-job-title-link",
    "h2 a[href*='/jobs/']",
    "h3 a[href*='/jobs/']",
)
WORK_MODES = {"on-site", "remote", "hybrid"}
JOB_TYPES = {"full time", "part time", "contract", "internship", "temporary", "freelance"}

PAGE_DELAY = 1.0
DETAIL_DELAY = 1.0
TIMEOUT = 30

log = get_logger(SOURCE)


def build_page_url(page_num: int) -> str:
    if page_num <= 1:
        return f"{SEARCH_PATH}?{QUERY_STRING}"
    return f"{SEARCH_PATH}/page/{page_num}?{QUERY_STRING}"


def job_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for selector in JOB_LINK_SELECTORS:
        links = soup.select(selector)
        if links:
            urls = []
            for a in links:
                href = a.get("href")
                if not href:
                    continue
                url = urljoin(BASE_URL, href).split("#")[0]
                if urlparse(url).netloc == ALLOWED_NETLOC:
                    urls.append(url)
            return list(dict.fromkeys(urls))
    return []


def _text(el):
    return el.get_text(" ", strip=True) if el else None


def parse_job_page(html: str, url: str) -> dict:
    """Pure function: HTML -> raw record (no network). Covered by tests."""
    soup = BeautifulSoup(html, "html.parser")

    apply_btn = soup.select_one(".apply-btn")
    company_el = soup.select_one("a.job-meta-company, .job-company-name, .company-name, a.company-name-link")

    tags = [t.get_text(" ", strip=True) for t in soup.select(".job-tags .job-tag") if t.get_text(strip=True)]
    work_mode = next((t for t in tags if t.lower() in WORK_MODES), None)
    job_type = next((t for t in tags if t.lower() in JOB_TYPES), None)

    posted_date = None
    date_el = soup.select_one(".job-date")
    if date_el and date_el.get("data-datetime"):
        raw = date_el["data-datetime"]
        try:
            posted_date = parse_date(raw).isoformat()
        except (ValueError, OverflowError):
            posted_date = raw

    details = {}
    for row in soup.select(".meta-data .meta"):
        spans = row.find_all("span")
        if len(spans) >= 2:
            details[spans[0].get_text(" ", strip=True)] = spans[1].get_text(" ", strip=True)

    skills = [s.get_text(" ", strip=True)
              for s in soup.select(".job-skills .skill, .skills-list .skill-tag") if s.get_text(strip=True)]

    return {
        "id": apply_btn.get("data-job-id") if apply_btn else None,
        "title": _text(soup.select_one("h3.job-title-text")),
        "company": _text(company_el),
        "description": _text(soup.select_one("#jobDescriptionBody")),
        "url": url,
        "apply_url": (apply_btn.get("href") or apply_btn.get("data-url")) if apply_btn else None,
        "location": _text(soup.select_one(".job-meta-item span")),
        "work_mode": work_mode,
        "job_type": details.get("Employment") or job_type,
        "career_level": details.get("Career Level"),
        "experience": details.get("Experience"),
        "education": details.get("Education"),
        "industry": details.get("Industry"),
        "skills": "; ".join(skills) if skills else None,
        "salary": details.get("Salary"),
        "posted_date": posted_date,
    }


def discover(run: SourceRun, fetcher: Fetcher) -> list[str]:
    max_pages = env_int("TANQEEB_MAX_PAGES", 1000)
    stop_after = env_int("TANQEEB_STOP_AFTER_KNOWN_PAGES", 40)
    new_urls, queued, known_streak = [], set(), 0

    for page in range(1, max_pages + 1):
        resp = fetcher.get(build_page_url(page))
        if resp is None or resp.status_code != 200:
            log.warning("Search page %d failed (%s). Stopping discovery.", page,
                        resp.status_code if resp else "no response")
            break

        links = job_links_from_html(resp.text)
        run.listings_seen += len(links)
        if not links:
            log.info("Page %d has no job links. End of results.", page)
            break

        fresh = [u for u in links if u not in run.known_urls and u not in queued]
        queued.update(fresh)
        new_urls.extend(fresh)
        log.info("Page %d: %d links, %d new (total new %d)", page, len(links), len(fresh), len(new_urls))

        known_streak = 0 if fresh else known_streak + 1
        if known_streak >= stop_after:
            log.info("%d pages in a row with nothing new. Caught up.", stop_after)
            break
        time.sleep(PAGE_DELAY)
    return new_urls


def run() -> int:
    run_ = SourceRun(SOURCE, log=log)
    fetcher = Fetcher(make_session(), log, timeout=TIMEOUT)

    urls = discover(run_, fetcher)
    cap = env_int("TANQEEB_MAX_JOBS", 300)
    if cap and len(urls) > cap:
        log.info("Capping to %d of %d new jobs; the rest will be picked up next run.", cap, len(urls))
        urls = urls[:cap]
    run_.discovered = len(urls)

    try:
        for i, url in enumerate(urls, 1):
            if run_.should_stop():
                break
            resp = fetcher.get(url)
            if resp is None:
                run_.failure(url, "fetch_failed")
            elif resp.status_code in (404, 410):
                run_.gone(url)
            elif resp.status_code != 200:
                run_.failure(url, f"http_{resp.status_code}")
            else:
                try:
                    record = parse_job_page(resp.text, url)
                except Exception as e:  # parser bug on one page must not kill the run
                    run_.failure(url, f"parse_error: {str(e)[:80]}")
                else:
                    if run_.success(url, record, required=("title",)):
                        log.info("[%d/%d] OK %s", i, len(urls), record["title"])
            time.sleep(DETAIL_DELAY)
    finally:
        fetcher.close()
        code = run_.finish()
    return code


if __name__ == "__main__":
    sys.exit(run())
