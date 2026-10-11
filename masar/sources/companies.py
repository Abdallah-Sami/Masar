"""
Company career pages that use Greenhouse or Lever. Both publish an official,
open jobs feed (JSON) meant for job boards, so no scraping or blocking issues.

Add a company: put (system, board name, display name) in BOARDS. The board
name is the last part of the careers link:
  job-boards.greenhouse.io/<board>   -> ("greenhouse", "<board>", "Name")
  jobs.lever.co/<board>              -> ("lever", "<board>", "Name")
Only jobs located in Saudi Arabia are kept.
"""
from __future__ import annotations

import html
import re
import sys
import time
from datetime import datetime, timezone

from masar.sources.base import SourceRun, get_logger, make_session

SOURCE = "companies"
log = get_logger(SOURCE)

BOARDS = [
    ("greenhouse", "tamara", "Tamara"),
    ("greenhouse", "careem", "Careem"),
    ("lever", "trendyol", "Trendyol"),
]

SAUDI_WORDS = ("saudi", "ksa", "riyadh", "jeddah", "jedda", "dammam", "khobar", "dhahran", "makkah", "mecca",
               "medina", "madinah", "neom", "jubail", "tabuk", "abha", "qassim", "buraydah",
               "الرياض", "جدة", "الدمام", "الخبر", "السعودية")
CITY_WORDS = ("Riyadh", "Jeddah", "Dammam", "Khobar", "Dhahran", "Makkah", "Medina", "NEOM", "Jubail", "Tabuk",
              "Abha", "Buraydah")


def is_saudi(text: str | None, country: str | None = None) -> bool:
    if (country or "").upper() == "SA":
        return True
    t = (text or "").lower()
    return any(w in t for w in SAUDI_WORDS)


def city_of(text: str | None) -> str | None:
    for c in CITY_WORDS:
        if c.lower() in (text or "").lower():
            return c
    return None


def from_greenhouse(slug: str, name: str, session) -> list[dict]:
    r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        where = " / ".join([(j.get("location") or {}).get("name") or ""]
                           + [o.get("name") or "" for o in j.get("offices") or []])
        if not is_saudi(where):
            continue
        out.append({
            "id": f"gh-{j.get('id')}", "url": j.get("absolute_url"), "title": j.get("title"),
            "company": name, "city": city_of(where), "country": "Saudi Arabia", "location": where,
            "work_mode": "remote" if "remote" in where.lower() else None,
            "description": html.unescape(j.get("content") or "") or None,
            "posted_date": j.get("first_published") or j.get("updated_at"),
        })
    return out


def from_lever(slug: str, name: str, session) -> list[dict]:
    r = session.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}, timeout=30)
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        where = " / ".join([cat.get("location") or ""] + list(cat.get("allLocations") or []))
        if not is_saudi(where, j.get("country")):
            continue
        lists = "\n".join(f"{x.get('text', '')}\n{re.sub('<[^>]+>', ' ', x.get('content') or '')}"
                          for x in j.get("lists") or [])
        created = j.get("createdAt")
        out.append({
            "id": f"lever-{j.get('id')}", "url": j.get("hostedUrl"), "apply_url": j.get("applyUrl"),
            "title": j.get("text"), "company": name, "city": city_of(where), "country": "Saudi Arabia",
            "location": where, "work_mode": j.get("workplaceType"), "employment_type": cat.get("commitment"),
            "department": cat.get("team"),
            "description": "\n".join(p for p in (j.get("descriptionPlain"), lists, j.get("additionalPlain")) if p) or None,
            "posted_date": (datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None),
        })
    return out


READERS = {"greenhouse": from_greenhouse, "lever": from_lever}


def run() -> int:
    run_ = SourceRun(SOURCE, log=log)
    session = make_session()
    jobs, complete = [], True
    try:
        for system, slug, name in BOARDS:
            try:
                found = READERS[system](slug, name, session)
                log.info("%s (%s/%s): %d Saudi jobs", name, system, slug, len(found))
                jobs += found
            except Exception as e:      # one company down must not stop the rest
                complete = False
                log.warning("%s (%s/%s) failed: %s", name, system, slug, str(e)[:120])
            time.sleep(1)
        run_.listings_seen = len(jobs) or (1 if complete else 0)   # "0 Saudi jobs today" is still healthy
        if complete:
            run_.active_urls = {j["url"] for j in jobs if j.get("url")}
        new = [j for j in jobs if j.get("url") and j["url"] not in run_.known_urls]
        run_.discovered = len(new)
        for j in new:
            run_.success(j["url"], j, required=("title",))
    finally:
        code = run_.finish()
    return code


if __name__ == "__main__":
    sys.exit(run())
