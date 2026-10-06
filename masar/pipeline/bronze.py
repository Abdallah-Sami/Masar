"""
Bronze: load every new landing file into lake/bronze/ (append only: one new
parquet file per run, old files are never rewritten).

Fixes vs the old notebook:
* ALL landing files are loaded, not only the latest one per source.
* A file is loaded once: its content hash is stored with its rows, and a file
  whose hash is already in bronze is skipped (safe to re-run).
* Both the new scraper format and the old Azure-era format are understood, so
  old files can be backfilled by dropping them into data/landing/<source>/.
* `data/landing/seed/*.json` accepts a website jobs.json export, so the
  history already on the site is not lost when switching to v2.
* Wuzzuf `collected_at` is read correctly (the old code read a missing key).

After loading, each file is gzipped into data/archive/<source>/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path

from masar import storage
from masar.pipeline import io

BRONZE_COLUMNS = {c: "VARCHAR" for c in (
    "source", "raw_native_id", "raw_job_title", "raw_company_name", "raw_job_description",
    "raw_job_url", "raw_apply_url", "raw_country", "raw_city", "raw_location",
    "raw_work_mode", "raw_employment_type", "raw_experience", "raw_qualification",
    "raw_industry", "raw_skills", "raw_salary", "raw_salary_min", "raw_salary_max",
    "raw_salary_currency", "raw_salary_period", "raw_posted_date", "raw_collected_at",
    "_source_file", "_file_hash", "_ingested_at",
)}

SOURCE_NAMES = {"sabbar": "Sabbar", "tanqeeb": "Tanqeeb", "wuzzuf": "Wuzzuf"}


def _s(v):
    """Stringify scalars, keep None. Lists/dicts become JSON."""
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False) if v else None
    s = str(v).strip()
    return s or None


def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


# ---------------------------------------------------------------------------
# Per-source normalizers (raw item -> bronze row). Each handles old + new format.
# ---------------------------------------------------------------------------
def from_wuzzuf(item: dict) -> dict:
    a = item.get("job_attributes") or {}
    c = item.get("company_attributes") or {}
    sal = a.get("salary") or {}
    cur = sal.get("currency")
    cur = cur.get("code") if isinstance(cur, dict) else cur
    period = sal.get("period")
    period = period.get("name") if isinstance(period, dict) else period
    keywords = [k.get("name") for k in (a.get("keywords") or []) if isinstance(k, dict) and k.get("name")]
    loc = a.get("location") or {}
    wp = a.get("workplaceArrangement") or {}
    work_mode = _get(wp, "translations", "displayedName", "en") or wp.get("displayedName") or wp.get("name")
    wt = a.get("workTypes") or []
    emp = wt[0].get("name") if wt and isinstance(wt[0], dict) else None
    exp = a.get("workExperienceYears") or {}
    exp_s = None
    if exp.get("min") is not None or exp.get("max") is not None:
        exp_s = f"{exp.get('min') if exp.get('min') is not None else ''}-{exp.get('max') if exp.get('max') is not None else ''}".strip("-")
        if exp.get("min") is not None and exp.get("max") is None:
            exp_s = f"{exp['min']}+"
    edu = _get(a, "candidatePreferences", "educationLevel") or {}
    return {
        "raw_native_id": _s(item.get("id") or item.get("wuzzuf_job_id") or item.get("job_id")),
        "raw_job_title": _s(a.get("title") or item.get("title")),
        "raw_company_name": _s(c.get("name")),
        "raw_job_description": _s(a.get("description")),
        "raw_job_url": _s(item.get("url") or item.get("job_url")),
        "raw_country": _s(_get(loc, "country", "name")) or "Saudi Arabia",
        "raw_city": _s(_get(loc, "city", "name")),
        "raw_location": _s(_get(loc, "area", "name")),
        "raw_work_mode": _s(work_mode),
        "raw_employment_type": _s(emp),
        "raw_experience": _s(exp_s),
        "raw_qualification": _s(edu.get("name") if isinstance(edu, dict) else edu),
        "raw_industry": None,
        "raw_skills": "; ".join(keywords) or None,
        "raw_salary": None,
        "raw_salary_min": _s(sal.get("min")),
        "raw_salary_max": _s(sal.get("max")),
        "raw_salary_currency": _s(cur),
        "raw_salary_period": _s(period),
        "raw_posted_date": _s(a.get("postedAt")),
        "raw_collected_at": _s(item.get("collected_at") or item.get("scraped_at")),
    }


def from_sabbar(item: dict) -> dict:
    raw = item.get("raw") or {}
    return {
        "raw_native_id": _s(item.get("id")),
        "raw_job_title": _s(item.get("title")),
        "raw_company_name": _s(item.get("company")),
        "raw_job_description": _s(item.get("description")),
        "raw_job_url": _s(item.get("url")),
        "raw_apply_url": _s(raw.get("applyUrl") or raw.get("externalUrl") or raw.get("jobUrl")),
        "raw_country": _s(item.get("country")),
        "raw_city": _s(item.get("city")),
        "raw_work_mode": _s(item.get("workplace_type")),
        "raw_employment_type": _s(item.get("contract_type")),
        "raw_experience": _s(item.get("experience_years")),
        "raw_industry": _s(item.get("industries")),
        "raw_salary_min": _s(item.get("salary")),
        "raw_salary_max": _s(item.get("max_salary")),
        "raw_salary_currency": _s(item.get("currency")),
        "raw_salary_period": _s(item.get("salary_frequency")),
        "raw_posted_date": _s(item.get("created_date")),
        "raw_collected_at": _s(item.get("collected_at")),
    }


def from_tanqeeb(item: dict) -> dict:
    return {
        "raw_native_id": _s(item.get("id") or item.get("job_id")),
        "raw_job_title": _s(item.get("title") or item.get("job_title")),
        "raw_company_name": _s(item.get("company") or item.get("company_name")),
        "raw_job_description": _s(item.get("description") or item.get("job_description")),
        "raw_job_url": _s(item.get("url") or item.get("job_url")),
        "raw_apply_url": _s(item.get("apply_url")),
        "raw_country": _s(item.get("country")),
        "raw_city": _s(item.get("city")),
        "raw_location": _s(item.get("location")),
        "raw_work_mode": _s(item.get("work_mode")),
        "raw_employment_type": _s(item.get("job_type") or item.get("employment_type")),
        "raw_experience": _s(item.get("experience") or item.get("experience_needed")),
        "raw_qualification": _s(item.get("education") or item.get("qualification_required")),
        "raw_industry": _s(item.get("industry")),
        "raw_skills": _s(item.get("skills")),
        "raw_salary": _s(item.get("salary") or item.get("estimated_salary")),
        "raw_posted_date": _s(item.get("posted_date")),
        "raw_collected_at": _s(item.get("collected_at")),
    }


def from_seed(item: dict) -> dict:
    """A row of the old website jobs.json (already cleaned by the old pipeline)."""
    key = item.get("posted_date_key")
    posted = f"{str(key)[:4]}-{str(key)[4:6]}-{str(key)[6:8]}" if key else None
    return {
        "source": _s(item.get("source_name")),
        "raw_native_id": None,
        "raw_job_title": _s(item.get("job_title")),
        "raw_company_name": _s(item.get("company_name")),
        "raw_job_url": _s(item.get("job_url")),
        "raw_country": "Saudi Arabia",
        "raw_city": _s(item.get("city")),
        "raw_work_mode": _s(item.get("work_mode")),
        "raw_employment_type": _s(item.get("employment_type")),
        "raw_experience": _s(item.get("experience_needed")),
        "raw_qualification": _s(item.get("qualification_required")),
        "raw_salary": _s(item.get("salary")),
        "raw_posted_date": posted,
        "raw_collected_at": posted,
    }


NORMALIZERS = {"wuzzuf": from_wuzzuf, "sabbar": from_sabbar, "tanqeeb": from_tanqeeb, "seed": from_seed}


# ---------------------------------------------------------------------------
def landing_files() -> list[tuple[str, Path]]:
    root = storage.data_dir() / "landing"
    out = []
    for folder, _ in NORMALIZERS.items():
        out += [(folder, p) for p in sorted((root / folder).glob("*.json"))]
    return out


def file_hash(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def rows_from_file(folder: str, p: Path, ingested_at: str) -> list[dict]:
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):           # tolerate {"jobs": [...]} wrappers
        data = data.get("jobs") or data.get("data") or []
    norm = NORMALIZERS[folder]
    digest = file_hash(p)
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        r = norm(item)
        r.setdefault("source", SOURCE_NAMES.get(folder))
        if not r.get("source"):
            r["source"] = SOURCE_NAMES.get(folder, folder.title())
        r.update(_source_file=f"{folder}/{p.name}", _file_hash=digest, _ingested_at=ingested_at)
        rows.append(r)
    return rows


def run(log=print) -> dict:
    bronze_dir = storage.data_dir() / "lake" / "bronze"
    bronze_dir.mkdir(parents=True, exist_ok=True)
    con = io.connect()
    loaded = {r["_file_hash"] for r in io.read_rows(bronze_dir, "SELECT DISTINCT _file_hash FROM {t}", con)}

    ingested_at = storage.iso()
    new_rows, to_archive, files_loaded, files_skipped = [], [], 0, 0
    for folder, p in landing_files():
        try:
            digest = file_hash(p)
            if digest in loaded:
                files_skipped += 1           # already in bronze (e.g. archive move failed last time)
            else:
                new_rows += rows_from_file(folder, p, ingested_at)
                loaded.add(digest)
                files_loaded += 1
            to_archive.append((folder, p))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log(f"[bronze] cannot read {p}: {e}; moved to data/failed/landing")
            p.replace(storage.path("failed", "landing", p.name))

    if new_rows:
        io.write_rows(new_rows, BRONZE_COLUMNS, bronze_dir / f"bronze_{storage.run_stamp()}.parquet", con)

    # Archive only after bronze is safely written.
    for folder, p in to_archive:
        storage.gzip_move(p, storage.data_dir() / "archive" / folder)
    pruned = prune_archive()

    total = io.read_rows(bronze_dir, "SELECT count(*) AS n FROM {t}", con)
    result = {"files_loaded": files_loaded, "files_skipped": files_skipped, "rows_loaded": len(new_rows),
              "bronze_rows": total[0]["n"] if total else 0, "archives_pruned": pruned}
    log(f"[bronze] {result}")
    return result


_STAMP_RE = re.compile(r"(\d{8})T\d{6}Z")


def prune_archive() -> int:
    """Raw archives are a safety copy of what is already in bronze. Keep the
    last ARCHIVE_KEEP_DAYS days (default 60, 0 = keep forever) so the data
    branch does not grow without limit. Age comes from the run stamp in the
    file name (file times are reset by every git checkout)."""
    keep = int(os.environ.get("ARCHIVE_KEEP_DAYS", "60") or 0)
    if not keep:
        return 0
    cutoff = (storage.utc_now() - timedelta(days=keep)).strftime("%Y%m%d")
    removed = 0
    for p in (storage.data_dir() / "archive").rglob("*.gz"):
        m = _STAMP_RE.search(p.name)
        if m and m.group(1) < cutoff:
            p.unlink()
            removed += 1
    return removed
