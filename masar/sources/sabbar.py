"""
Sabbar (sabbar.com) scraper.

Discovery: the public job-details sitemap.
Details:   job pages are rendered by Next.js, so we load them with Playwright
           and read the embedded job JSON (plus the visible description).

The full embedded job JSON is kept under `raw`, so fields we don't map today
(for example the company's own apply link) can be added later without
re-scraping.

Run:  python -m masar.sources.sabbar        (needs: playwright install chromium)
Env:  SABBAR_MAX_JOBS (default 900), SABBAR_CONCURRENCY (default 4), PROXY_* (optional)
"""
from __future__ import annotations

import asyncio
import json
import random
import sys

import requests
from bs4 import BeautifulSoup

from masar.sources.base import SourceRun, env_int, get_logger, make_session, proxy_settings, USER_AGENT

SOURCE = "sabbar"
SITEMAP_URL = "https://sabbar.com/en/jobs/sitemaps/job-details.xml"
RETRIES = 3
MIN_DELAY, MAX_DELAY = 1.0, 3.0
LAUNCH_ARGS = ["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox",
               "--disable-extensions", "--disable-background-networking"]

log = get_logger(SOURCE)


def collect_urls(session: requests.Session) -> list[str]:
    resp = session.get(SITEMAP_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "xml")
    return [loc.text.strip() for loc in soup.find_all("loc") if loc.text.strip()]


def extract_job_details(html: str) -> dict | None:
    """Find the escaped job JSON object that contains `jobPositionValue`."""
    marker = '\\"jobPositionValue\\"'
    pos = html.find(marker)
    if pos == -1:
        return None
    start = html.rfind('{\\"id\\":', 0, pos)
    if start == -1:
        start = html.rfind("{", 0, pos)
    depth, i, in_string = 0, start, False
    while i < len(html):
        if html[i] == "\\" and i + 1 < len(html) and html[i + 1] == '"':
            in_string = not in_string
            i += 2
            continue
        ch = html[i]
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    clean = html[start:i + 1].replace('\\"', '"').replace("\\\\", "\\")
                    try:
                        return json.loads(clean)
                    except json.JSONDecodeError:
                        return None
        i += 1
    return None


def to_record(details: dict, url: str, description: str | None) -> dict:
    return {
        "id": details.get("id"),
        "url": url,
        "title": details.get("jobPositionValue"),
        "company": (details.get("partnerName") or "").strip() or None,
        "industries": ", ".join(details.get("industries") or []) or None,
        "salary": details.get("salary"),
        "max_salary": details.get("maxSalary"),
        "currency": details.get("currency"),
        "salary_frequency": details.get("salaryFrequency"),
        "contract_type": details.get("contractType"),
        "workplace_type": details.get("workplaceType"),
        "experience_years": details.get("experienceYears"),
        "city": details.get("cityValue"),
        "country": details.get("countryValue"),
        "created_date": details.get("createdDate"),
        "job_status": details.get("jobStatus"),
        "description": description,
        "raw": details,
    }


async def scrape_all(run: SourceRun, urls: list[str]) -> None:
    from playwright.async_api import async_playwright  # imported here so tests don't need it

    _, pw_proxy = proxy_settings()
    queue: asyncio.Queue = asyncio.Queue()
    for u in urls:
        queue.put_nowait(u)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=LAUNCH_ARGS, proxy=pw_proxy)
        context = await browser.new_context(user_agent=USER_AGENT)

        async def worker():
            page = await context.new_page()
            while not queue.empty():
                url = queue.get_nowait()
                for attempt in range(RETRIES):
                    try:
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        if resp is not None and resp.status in (404, 410):
                            run.gone(url)
                            break
                        description = None
                        try:
                            await page.wait_for_selector("div.desc", timeout=15000)
                            description = (await page.inner_text("div.desc")).strip() or None
                        except Exception:
                            pass
                        details = extract_job_details(await page.content())
                        if not details:
                            run.soft_gone(url, "no_details_in_page")
                        elif run.success(url, to_record(details, url, description), required=("title",)):
                            log.info("OK %s", details.get("jobPositionValue"))
                        break
                    except Exception as e:
                        if attempt == RETRIES - 1:
                            run.failure(url, f"{type(e).__name__}: {str(e)[:80]}")
                        else:
                            await asyncio.sleep(2 * (attempt + 1))
                await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
            await page.close()

        await asyncio.gather(*[worker() for _ in range(env_int("SABBAR_CONCURRENCY", 4))])
        await browser.close()


def run() -> int:
    run_ = SourceRun(SOURCE, log=log, flush_every=100)
    try:
        all_urls = collect_urls(make_session())
    except (requests.RequestException, OSError) as e:
        log.error("Sitemap request failed: %s", e)
        all_urls = []
    run_.listings_seen = len(all_urls)

    new_urls = [u for u in all_urls if u not in run_.known_urls]
    cap = env_int("SABBAR_MAX_JOBS", 900)
    log.info("Sitemap %d | already handled %d | new %d | cap %d",
             len(all_urls), len(run_.known_urls), len(new_urls), cap)
    new_urls = new_urls[:cap] if cap else new_urls
    run_.discovered = len(new_urls)

    try:
        if new_urls:
            asyncio.run(scrape_all(run_, new_urls))
    finally:
        code = run_.finish()
    return code


if __name__ == "__main__":
    sys.exit(run())
