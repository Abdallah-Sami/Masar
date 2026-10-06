"""
Gold: star schema for Power BI and the website stats, rebuilt from silver.

Surrogate keys are a hash of the natural key, so the same company / city /
skill keeps the same key across runs (no IDENTITY drift, no full wipe needed).

Tables (data/lake/gold/<name>.parquet):
  dim_source, dim_company, dim_location, dim_job, dim_skill, dim_date,
  fact_job_posting (one row per posting; is_primary marks cross-source dups),
  bridge_job_posting_skill
"""
from __future__ import annotations

from masar import storage
from masar.pipeline import io


def key(expr: str, case_sensitive: bool = False) -> str:
    """Deterministic BIGINT surrogate key from a text expression."""
    inner = f"coalesce({expr}, 'unknown')"
    return f"CAST(('0x' || substr(md5({inner if case_sensitive else 'lower(' + inner + ')'}), 1, 15)) AS BIGINT)"


TABLES = {
    "dim_source": f"""
        SELECT DISTINCT {key('source')} AS source_key, source AS source_name FROM silver""",

    "dim_company": f"""
        SELECT {key('company_key')} AS company_key, mode(company_name) AS company_name,
               mode(industry) AS industry, count(*) AS postings
        FROM silver WHERE company_key IS NOT NULL GROUP BY company_key
        UNION ALL SELECT {key('NULL')}, 'Unknown', NULL, (SELECT count(*) FROM silver WHERE company_key IS NULL)""",

    "dim_location": f"""
        SELECT DISTINCT {key('city')} AS location_key, 'Saudi Arabia' AS country, city FROM silver""",

    "dim_job": f"""
        SELECT DISTINCT {key("concat_ws('|', job_title, employment_type, work_mode, qualification, experience_needed)", True)} AS job_key,
               job_title, employment_type, work_mode, qualification AS qualification_required,
               experience_needed, experience_band
        FROM silver""",

    "dim_skill": f"""
        SELECT {key('lower(s)')} AS skill_key, mode(s) AS skill_name, count(*) AS postings
        FROM (SELECT unnest(skills) AS s FROM silver) GROUP BY lower(s)""",

    "dim_date": """
        SELECT CAST(strftime(d, '%Y%m%d') AS BIGINT) AS date_key, CAST(d AS DATE) AS date,
               year(d) AS year, month(d) AS month, day(d) AS day, quarter(d) AS quarter,
               dayname(d) AS day_of_week, strftime(d, '%Y-%m') AS year_month
        FROM (SELECT unnest(generate_series(
                  CAST(min(least(posted_at, first_seen_at)) AS DATE),
                  CAST(max(greatest(posted_at, last_seen_at)) AS DATE), INTERVAL 1 DAY)) AS d
              FROM silver)""",

    "fact_job_posting": f"""
        SELECT {key('job_id')} AS job_posting_key, job_id,
               {key("concat_ws('|', job_title, employment_type, work_mode, qualification, experience_needed)", True)} AS job_key,
               {key('company_key')} AS company_key,
               {key('city')} AS location_key,
               {key('source')} AS source_key,
               CAST(strftime(posted_at, '%Y%m%d') AS BIGINT) AS posted_date_key,
               CAST(strftime(first_seen_at, '%Y%m%d') AS BIGINT) AS first_seen_date_key,
               CAST(strftime(last_seen_at, '%Y%m%d') AS BIGINT) AS last_seen_date_key,
               job_url, apply_url, salary_min, salary_max, salary_currency, salary_period, salary_text,
               experience_min, experience_max, len(skills) AS skill_count,
               posted_date_estimated, is_primary, dup_key, times_seen
        FROM silver""",

    "bridge_job_posting_skill": f"""
        SELECT DISTINCT {key('job_id')} AS job_posting_key, {key('lower(s)')} AS skill_key
        FROM (SELECT job_id, unnest(skills) AS s FROM silver)""",
}


def run(log=print) -> dict:
    silver = storage.data_dir() / "lake" / "silver"
    con = io.connect()
    con.execute(f"CREATE VIEW silver AS SELECT * FROM {io.parquet(silver)}")
    out_dir = storage.data_dir() / "lake" / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, sql in TABLES.items():
        target = out_dir / f"{name}.parquet"
        tmp = out_dir / f".{name}.tmp.parquet"
        # ORDER BY ALL: same input -> byte-identical files (clean diffs, real idempotency)
        con.execute(f"COPY (SELECT * FROM ({sql}) ORDER BY ALL) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)")
        tmp.replace(target)
        counts[name] = con.execute(f"SELECT count(*) FROM {io.parquet(target)}").fetchone()[0]
    log(f"[gold] {counts}")
    return counts
