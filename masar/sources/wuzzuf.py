"""
Wuzzuf (wuzzuf.net, Saudi Arabia filter) scraper.

Discovery: paginate Saudi search results, collect /jobs/p/<slug> links.
Details:   each job page embeds `Wuzzuf.initialStoreState` JSON; we read the job
           whose slug matches the URL.

Fix vs the old scraper: if the slug is not found we record a failure instead of
silently taking the first/"similar" job in the page (which attached the wrong
job's data to the URL).

Run:  python -m masar.sources.wuzzuf
Env:  WUZZUF_MAX_JOBS (default 300), WUZZUF_MAX_PAGES (default 0 = no cap),
      WUZZUF_STOP_AFTER_KNOWN_PAGES (default 9), PROXY_* (optional)
"""
from __future__ import annotations

import json
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from masar.sources.base import SourceRun, env_int, get_logger, make_session

SOURCE = "wuzzuf"
SEARCH_URL = "https://wuzzuf.net/saudi/search/jobs?filters%5Bcountry%5D%5B0%5D=Saudi%20Arabia"
SEARCH_PARAMS = {"q": "", "a": "spbg"}
LISTING_DELAY = 3.0
DETAIL_DELAY = 1.0
TIMEOUT = 20

STORE_RE = re.compile(r"Wuzzuf\.initialStoreState\s*=\s*(\{.*?\})\s*;\s*\n", re.DOTALL)
STORE_FALLBACK_RE = re.compile(r"Wuzzuf\.initialStoreState\s*=\s*(\{.*\})", re.DOTALL)
SLUG_RE = re.compile(r"/jobs/p/([^/?#]+)")

log = get_logger(SOURCE)


def canonical_url(href: str) -> str:
    url = urljoin("https://wuzzuf.net", href).replace("https://wuzzuf.net/ar/", "https://wuzzuf.net/")
    return url.split("?")[0].split("#")[0]


def job_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = [canonical_url(a["href"]) for a in soup.find_all("a", href=True) if "/jobs/p/" in a["href"]]
    return list(dict.fromkeys(urls))


def _balanced_json(text: str) -> str:
    start = text.find("{")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("unbalanced JSON")


def extract_store_state(html: str) -> dict:
    m = STORE_RE.search(html) or STORE_FALLBACK_RE.search(html)
    if not m:
        raise ValueError("Wuzzuf.initialStoreState not found")
    raw = m.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_balanced_json(raw))


def parse_job_page(html: str, url: str) -> dict | None:
    """Pure function: HTML -> raw record, or None if this URL's job isn't in the page."""
    store = extract_store_state(html)
    slug_m = SLUG_RE.search(url)
    slug = slug_m.group(1) if slug_m else None
    jobs = store.get("entities", {}).get("job", {}).get("collection", {}) or {}

    job_id, entity = next(
        ((jid, ent) for jid, ent in jobs.items() if slug and (ent.get("attributes") or {}).get("slug") == slug),
        (None, None),
    )
    if entity is None:
        return None

    company = None
    ref = (entity.get("relationships") or {}).get("company", {}).get("data") or {}
    if ref.get("id"):
        company = store.get("entities", {}).get("company", {}).get("collection", {}).get(ref["id"])

    return {
        "id": str(job_id),
        "url": url,
        "job_attributes": entity.get("attributes") or {},
        "company_attributes": (company or {}).get("attributes") or {},
    }


def fetch(session: requests.Session, url: str, params=None) -> requests.Response | None:
    for attempt in range(1, 4):
        try:
            resp = session.get(url, params=params, timeout=TIMEOUT)
        except (requests.RequestException, OSError) as e:
            log.warning("Request failed (%s) attempt %d: %s", url, attempt, e)
            time.sleep(5 * attempt)
            continue
        if resp.status_code in (403, 429):
            time.sleep(5 * attempt)
            continue
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp
    return None


def discover(run: SourceRun, session: requests.Session) -> list[str]:
    max_pages = env_int("WUZZUF_MAX_PAGES", 0) or None
    stop_after = env_int("WUZZUF_STOP_AFTER_KNOWN_PAGES", 9)
    cap = env_int("WUZZUF_MAX_JOBS", 300)
    new_urls, seen, known_streak, page = [], set(), 0, 0

    while max_pages is None or page < max_pages:
        params = dict(SEARCH_PARAMS, **({"start": page} if page else {}))
        resp = fetch(session, SEARCH_URL, params)
        if resp is None or resp.status_code != 200:
            log.error("Search page %d failed. Stopping discovery.", page + 1)
            break

        links = job_links_from_html(resp.text)
        run.listings_seen += len(links)
        if not links:
            log.info("No job links on page %d. End of results.", page + 1)
            break

        fresh = [u for u in links if u not in seen and u not in run.known_urls]
        seen.update(links)
        new_urls.extend(fresh)
        log.info("Page %d: %d links, %d new (total new %d)", page + 1, len(links), len(fresh), len(new_urls))

        known_streak = 0 if fresh else known_streak + 1
        if known_streak >= stop_after or (cap and len(new_urls) >= cap):
            break
        page += 1
        time.sleep(LISTING_DELAY)
    return new_urls[:cap] if cap else new_urls


def run() -> int:
    run_ = SourceRun(SOURCE, log=log)
    session = make_session({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})

    urls = discover(run_, session)
    run_.discovered = len(urls)
    try:
        for i, url in enumerate(urls, 1):
            resp = fetch(session, url)
            if resp is None:
                run_.failure(url, "fetch_failed")
            elif resp.status_code in (404, 410):
                run_.gone(url)
            elif resp.status_code != 200:
                run_.failure(url, f"http_{resp.status_code}")
            else:
                try:
                    record = parse_job_page(resp.text, url)
                except Exception as e:
                    run_.failure(url, f"parse_error: {str(e)[:80]}")
                else:
                    if record is None:
                        run_.soft_gone(url, "slug_not_in_page")
                    else:
                        record["title"] = record["job_attributes"].get("title")
                        if run_.success(url, record, required=("title",)):
                            log.info("[%d/%d] OK %s", i, len(urls), record["title"])
            time.sleep(DETAIL_DELAY)
    finally:
        code = run_.finish()
    return code


if __name__ == "__main__":
    sys.exit(run())
