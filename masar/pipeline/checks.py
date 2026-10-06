"""
Quality checks after each pipeline run. Any failed check fails the run, so a
broken export never replaces the live jobs.json.
"""
from __future__ import annotations

from masar import storage
from masar.pipeline import io


def run_checks(min_export: int = 1) -> list[dict]:
    con = io.connect()
    lake = storage.data_dir() / "lake"
    gold = lake / "gold"
    con.execute(f"CREATE VIEW silver AS SELECT * FROM {io.parquet(lake / 'silver')}")
    for t in ("fact_job_posting", "dim_job", "dim_company", "dim_location", "dim_source", "dim_skill",
              "dim_date", "bridge_job_posting_skill"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {io.parquet(gold / (t + '.parquet'))}")

    def one(sql):
        return con.execute(sql).fetchone()[0]

    checks = [
        ("silver job_id unique", "SELECT count(*) - count(DISTINCT job_id) FROM silver", 0),
        ("silver required fields present",
         "SELECT count(*) FROM silver WHERE job_title IS NULL OR job_url IS NULL OR source IS NULL", 0),
        ("every duplicate has a primary",
         "SELECT count(*) FROM (SELECT dup_key FROM silver GROUP BY 1 HAVING sum(is_primary::INT) = 0)", 0),
        ("fact rows = silver rows", "SELECT (SELECT count(*) FROM fact_job_posting) - (SELECT count(*) FROM silver)", 0),
    ]
    for dim, k in (("dim_source", "source_key"), ("dim_company", "company_key"), ("dim_location", "location_key"),
                   ("dim_job", "job_key"), ("dim_skill", "skill_key"), ("dim_date", "date_key")):
        checks.append((f"{dim} key unique", f"SELECT count(*) - count(DISTINCT {k}) FROM {dim}", 0))
    for dim, k in (("dim_source", "source_key"), ("dim_company", "company_key"),
                   ("dim_location", "location_key"), ("dim_job", "job_key")):
        checks.append((f"fact -> {dim} keys valid",
                       f"SELECT count(*) FROM fact_job_posting f ANTI JOIN {dim} d USING ({k})", 0))
    checks += [
        ("fact -> dim_date keys valid",
         "SELECT count(*) FROM fact_job_posting f ANTI JOIN dim_date d ON f.posted_date_key = d.date_key", 0),
        ("bridge -> fact valid",
         "SELECT count(*) FROM bridge_job_posting_skill b ANTI JOIN fact_job_posting f USING (job_posting_key)", 0),
        ("bridge -> skill valid",
         "SELECT count(*) FROM bridge_job_posting_skill b ANTI JOIN dim_skill s USING (skill_key)", 0),
    ]

    results = []
    for name, sql, expected in checks:
        got = one(sql)
        results.append({"check": name, "value": got, "expected": expected, "passed": got == expected})
    primaries = one("SELECT count(*) FROM silver WHERE is_primary")
    results.append({"check": "jobs to export", "value": primaries, "expected": f">= {min_export}",
                    "passed": primaries >= min_export})
    return results
