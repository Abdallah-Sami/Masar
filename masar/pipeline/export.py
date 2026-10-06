"""
Export the website files from gold:

  <site>/jobs.json   same fields the current website reads (plus skills/salary)
  <site>/stats.json  market numbers for a future stats section on the site

<site> = MASAR_SITE_DIR (default ./site). Only primary postings are exported,
so a job listed on two sources shows once.
EXPORT_MAX_AGE_DAYS (default 0 = everything) limits jobs.json to recent posts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from masar import storage
from masar.pipeline import io


def site_dir() -> Path:
    p = Path(os.environ.get("MASAR_SITE_DIR", "site")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _views(con) -> None:
    gold = storage.data_dir() / "lake" / "gold"
    for t in ("fact_job_posting", "dim_job", "dim_company", "dim_location", "dim_source",
              "dim_skill", "bridge_job_posting_skill"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {io.parquet(gold / (t + '.parquet'))}")
    con.execute("""
        CREATE VIEW jobs AS
        SELECT f.*, j.job_title, j.work_mode, j.employment_type, j.qualification_required,
               j.experience_needed, c.company_name, l.city, s.source_name
        FROM fact_job_posting f
        JOIN dim_job j USING (job_key) JOIN dim_company c USING (company_key)
        JOIN dim_location l USING (location_key) JOIN dim_source s USING (source_key)
        WHERE f.is_primary""")


JOBS_SQL = """
    SELECT j.job_url, j.job_id, j.job_title, j.work_mode, j.employment_type, j.experience_needed,
           j.qualification_required, j.company_name, j.city, j.source_name, j.posted_date_key,
           j.salary_text AS salary, j.salary_min, j.salary_max, j.salary_currency,
           list(k.skill_name ORDER BY k.skill_name) FILTER (WHERE k.skill_name IS NOT NULL) AS skills
    FROM jobs j
    LEFT JOIN bridge_job_posting_skill b USING (job_posting_key)
    LEFT JOIN dim_skill k USING (skill_key)
    WHERE {age}
    GROUP BY ALL
    ORDER BY j.posted_date_key DESC, j.job_id"""


def _top(con, sql: str, n: int = 15) -> list[dict]:
    cur = con.execute(sql + f" LIMIT {n}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def build_stats(con) -> dict:
    total = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    known = "NOT IN ('Unknown')"
    return {
        "updated_at": storage.iso(),
        "total_jobs": total,
        "by_source": _top(con, "SELECT source_name AS name, count(*) AS jobs FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "top_cities": _top(con, f"SELECT city AS name, count(*) AS jobs FROM jobs WHERE city {known} GROUP BY 1 ORDER BY 2 DESC"),
        "top_titles": _top(con, "SELECT job_title AS name, count(*) AS jobs FROM jobs GROUP BY 1 ORDER BY 2 DESC, 1", 20),
        "top_companies": _top(con, f"SELECT company_name AS name, count(*) AS jobs FROM jobs WHERE company_name {known} GROUP BY 1 ORDER BY 2 DESC, 1", 20),
        "top_skills": _top(con, """SELECT k.skill_name AS name, count(*) AS jobs FROM jobs j
                                   JOIN bridge_job_posting_skill b USING (job_posting_key)
                                   JOIN dim_skill k USING (skill_key) GROUP BY 1 ORDER BY 2 DESC, 1""", 30),
        "work_mode": _top(con, "SELECT work_mode AS name, count(*) AS jobs FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "employment_type": _top(con, "SELECT employment_type AS name, count(*) AS jobs FROM jobs GROUP BY 1 ORDER BY 2 DESC"),
        "monthly": _top(con, """SELECT substr(CAST(posted_date_key AS VARCHAR), 1, 6) AS month, count(*) AS jobs
                                FROM jobs GROUP BY 1 ORDER BY 1""", 120),
        "salary_by_title": _top(con, """SELECT job_title AS name, count(*) AS jobs,
                                        round(median(salary_min)) AS median_salary_min
                                        FROM jobs WHERE salary_min IS NOT NULL AND salary_currency = 'SAR'
                                        AND coalesce(salary_period, 'month') LIKE 'month%'
                                        GROUP BY 1 HAVING count(*) >= 3 ORDER BY 2 DESC""", 20),
    }


def write_compact(target: Path, obj) -> None:
    tmp = target.with_name("." + target.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)


def run(log=print) -> dict:
    con = io.connect()
    _views(con)
    max_age = int(os.environ.get("EXPORT_MAX_AGE_DAYS", "0") or 0)
    age = (f"strptime(CAST(j.posted_date_key AS VARCHAR), '%Y%m%d') >= current_date - INTERVAL {max_age} DAY"
           if max_age else "TRUE")
    out = site_dir()
    cur = con.execute(JOBS_SQL.format(age=age))
    names = [d[0] for d in cur.description]
    # Null fields are dropped to keep the file small; the site treats missing as empty.
    jobs = [{k: v for k, v in zip(names, row) if v is not None} for row in cur.fetchall()]
    write_compact(out / "jobs.json", jobs)
    exported = len(jobs)
    write_compact(out / "stats.json", build_stats(con))
    result = {"exported_jobs": exported, "site_dir": str(out)}
    log(f"[export] {result}")
    return result
