"""
Silver: one clean row per job posting, rebuilt from bronze on every run.

Rebuilding (instead of upserting) makes the step idempotent: running it twice
gives the same table, and fixing a cleaning rule fixes history too.

Fixes vs the old notebooks:
* job_id = hash(source + canonical URL), never the title (two different jobs
  with the same title no longer collapse into one).
* Description is optional; only title, URL and "in Saudi Arabia" are required.
* Unknown work mode stays "Unknown" (no silent On-site default).
* Missing posted date falls back to first-seen date, flagged as estimated.
* Salary and experience are parsed into numbers.
* Skills are split on ';' (and , | bullets) into a list.
* Cross-source duplicates are flagged (is_primary), not deleted.
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

from masar import storage
from masar.pipeline import io
from masar.pipeline import cleaning as c

SILVER_COLUMNS = {
    "job_id": "VARCHAR", "source": "VARCHAR", "native_id": "VARCHAR",
    "job_url": "VARCHAR", "apply_url": "VARCHAR",
    "job_title": "VARCHAR", "title_key": "VARCHAR",
    "company_name": "VARCHAR", "company_key": "VARCHAR",
    "city": "VARCHAR", "work_mode": "VARCHAR", "employment_type": "VARCHAR",
    "qualification": "VARCHAR", "industry": "VARCHAR",
    "experience_min": "DOUBLE", "experience_max": "DOUBLE",
    "experience_needed": "VARCHAR", "experience_band": "VARCHAR",
    "salary_min": "DOUBLE", "salary_max": "DOUBLE", "salary_currency": "VARCHAR",
    "salary_period": "VARCHAR", "salary_text": "VARCHAR",
    "skills": "VARCHAR[]", "description": "VARCHAR",
    "posted_at": "TIMESTAMP", "posted_date_estimated": "BOOLEAN",
    "first_seen_at": "TIMESTAMP", "last_seen_at": "TIMESTAMP", "times_seen": "INTEGER",
    "dup_key": "VARCHAR", "is_primary": "BOOLEAN", "posted_month": "VARCHAR",
}
REJECTED_COLUMNS = {"source": "VARCHAR", "raw_job_url": "VARCHAR", "raw_job_title": "VARCHAR",
                    "raw_city": "VARCHAR", "raw_country": "VARCHAR", "reason": "VARCHAR",
                    "_source_file": "VARCHAR"}

# Fields that may be filled from an older sighting when the newest one lacks them.
FILLABLE = ("native_id", "apply_url", "company_name", "industry", "experience_min", "experience_max",
            "salary_min", "salary_max", "salary_currency", "salary_period", "salary_text",
            "description", "skills")


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return None
    path = parts.path.rstrip("/")
    host = parts.netloc.lower()
    if host.endswith("wuzzuf.net") and path.startswith("/ar/"):
        path = path[3:]
    if host.endswith("sabbar.com") and path.startswith("/ar/"):
        path = "/en/" + path[4:]
    return urlunsplit(("https", host, path, "", ""))


def _fmt_years(n: float | None) -> str:
    return "" if n is None else (f"{n:.0f}" if float(n).is_integer() else f"{n:g}")


def experience_text(lo, hi) -> str:
    if lo is None:
        return "Unknown"
    if hi is None:
        return f"{_fmt_years(lo)}+"
    return _fmt_years(lo) if lo == hi else f"{_fmt_years(lo)}-{_fmt_years(hi)}"


def clean_row(b: dict) -> tuple[dict | None, str | None]:
    """bronze row -> (silver row, None) or (None, reject reason)."""
    url = canonical_url(b.get("raw_job_url"))
    title = c.clean_title(b.get("raw_job_title"))
    if not title:
        return None, "missing_title"
    if not url:
        return None, "missing_url"
    if c.is_non_saudi(b.get("raw_city"), b.get("raw_country"), url):
        return None, "not_saudi"

    company = c.clean_text(b.get("raw_company_name"))
    company = None if c.is_empty(company) or (company or "").lower() in {"confidential", "سري"} else company
    exp_lo, exp_hi = c.parse_experience(b.get("raw_experience"))
    salary = c.parse_salary(b.get("raw_salary"), b.get("raw_salary_min"), b.get("raw_salary_max"),
                            b.get("raw_salary_currency"), b.get("raw_salary_period"))
    collected = (c.parse_datetime(b.get("raw_collected_at")) or c.parse_datetime(b.get("_ingested_at")))
    posted = c.parse_datetime(b.get("raw_posted_date"))
    if posted and collected and posted > collected:
        posted = collected          # posted "in the future" = timezone noise
    source = b.get("source")
    return {
        "job_id": c.stable_id(source, url),
        "source": source,
        "native_id": b.get("raw_native_id"),
        "job_url": url,
        "apply_url": canonical_url(b.get("raw_apply_url")),
        "job_title": title,
        "title_key": c.title_key(title),
        "company_name": company,
        "company_key": c.company_key(company) if company else None,
        "city": c.normalize_city(b.get("raw_city"), b.get("raw_location")),
        "work_mode": c.map_work_mode(b.get("raw_work_mode")),
        "employment_type": c.map_employment_type(b.get("raw_employment_type")),
        "qualification": c.map_qualification(b.get("raw_qualification")),
        "industry": c.clean_text(b.get("raw_industry")),
        "experience_min": exp_lo, "experience_max": exp_hi,
        **salary,
        "skills": c.split_skills(b.get("raw_skills")),
        "description": c.clean_text(b.get("raw_job_description")),
        "_posted": posted,
        "_collected": collected,
    }, None


def merge_sightings(rows: list[dict]) -> dict:
    """Several bronze rows of the same job -> one silver row."""
    rows = sorted(rows, key=lambda r: r["_collected"] or datetime.min, reverse=True)
    out = dict(rows[0])
    for older in rows[1:]:
        for f in FILLABLE:
            if out.get(f) in (None, [], "") and older.get(f) not in (None, [], ""):
                out[f] = older[f]
    seen = [r["_collected"] for r in rows if r["_collected"]]
    posted = [r["_posted"] for r in rows if r["_posted"]]
    out["first_seen_at"] = min(seen) if seen else None
    out["last_seen_at"] = max(seen) if seen else None
    out["times_seen"] = len(rows)
    out["posted_at"] = min(posted) if posted else out["first_seen_at"]
    out["posted_date_estimated"] = not posted
    out["experience_needed"] = experience_text(out["experience_min"], out["experience_max"])
    out["experience_band"] = c.experience_band(out["experience_min"])
    out["posted_month"] = out["posted_at"].strftime("%Y-%m") if out["posted_at"] else "unknown"
    out.pop("_posted"), out.pop("_collected")
    return out


DUP_WINDOW_DAYS = 30


def flag_duplicates(jobs: list[dict]) -> None:
    """The same job on several sources -> one primary, the others flagged.

    Same title + company + city, on a DIFFERENT source, posted within
    DUP_WINDOW_DAYS of each other. Re-posts on the same source are kept as
    separate postings (the source says they are different), and postings with
    an unknown company are never merged.
    """
    groups: dict[str, list[dict]] = {}
    for j in jobs:
        j["dup_key"] = (c.stable_id(j["title_key"], j["company_key"], j["city"])
                        if j["company_key"] else j["job_id"])
        j["is_primary"] = True
        groups.setdefault(j["dup_key"], []).append(j)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda j: (j["posted_at"] or datetime.max, j["source"] or "", j["job_id"]))
        primaries: list[dict] = []
        for j in members:
            match = next((p for p in primaries if p["source"] != j["source"] and p["posted_at"] and j["posted_at"]
                          and abs((j["posted_at"] - p["posted_at"]).days) <= DUP_WINDOW_DAYS), None)
            if match:
                j["is_primary"] = False
            else:
                primaries.append(j)


def build(bronze_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    by_id: dict[str, list[dict]] = {}
    rejected = []
    for b in bronze_rows:
        row, reason = clean_row(b)
        if row is None:
            rejected.append({**{k: b.get(k) for k in REJECTED_COLUMNS}, "reason": reason})
        else:
            by_id.setdefault(row["job_id"], []).append(row)
    jobs = [merge_sightings(v) for v in by_id.values()]
    flag_duplicates(jobs)
    jobs.sort(key=lambda j: j["job_id"])
    return jobs, rejected


def run(log=print) -> dict:
    lake = storage.data_dir() / "lake"
    con = io.connect()
    jobs, rejected = build(io.read_rows(lake / "bronze", con=con))
    # One folder per posted month keeps each file far below GitHub's 100 MB limit.
    io.write_rows(jobs, SILVER_COLUMNS, lake / "silver", con, partition_by="posted_month")
    io.write_rows(rejected, REJECTED_COLUMNS, storage.path("lake", "rejected.parquet"), con)
    reasons: dict[str, int] = {}
    for r in rejected:
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    result = {"silver_rows": len(jobs), "primary_rows": sum(j["is_primary"] for j in jobs),
              "rejected_rows": len(rejected), "rejected_by_reason": reasons}
    log(f"[silver] {result}")
    return result
