"""
Export the website files from gold:

  <site>/jobs.json   same fields the current website reads (plus skills/salary/field);
                     also feeds the dashboard page, which filters it in the browser
  <site>/stats.json  market numbers for a future stats section on the site
  <site>/details/<xx>.json  full job (with description) for the job page, by job_id prefix
<site>/fields.json per field and per popular title: titles, skills, companies,
                     cities, work mode, experience, salary, monthly trend

<site> = MASAR_SITE_DIR (default ./site). Only primary postings are exported,
so a job listed on two sources shows once.
jobs.json holds open jobs only (is_active, see silver.flag_active).
EXPORT_MAX_AGE_DAYS (default 0 = no extra limit) can cut it further by posting age.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from masar import storage
from masar.pipeline import io, social, taxonomy


def site_dir() -> Path:
    p = Path(os.environ.get("MASAR_SITE_DIR", "site")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _views(con) -> None:
    gold = storage.data_dir() / "lake" / "gold"
    for t in ("fact_job_posting", "dim_job", "dim_company", "dim_location", "dim_source",
              "dim_skill", "bridge_job_posting_skill", "dim_field"):
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {io.parquet(gold / (t + '.parquet'))}")
    con.execute("""
        CREATE VIEW jobs AS
        SELECT f.*, j.job_title, j.work_mode, j.employment_type, j.qualification_required,
               j.experience_needed, j.experience_band, c.company_name, l.city, s.source_name
        FROM fact_job_posting f
        JOIN dim_job j USING (job_key) JOIN dim_company c USING (company_key)
        JOIN dim_location l USING (location_key) JOIN dim_source s USING (source_key)
        WHERE f.is_primary""")


JOBS_SQL = """
    SELECT j.job_url, j.job_id, j.job_title, j.work_mode, j.employment_type, j.experience_needed,
           j.qualification_required, j.company_name, j.city, j.source_name, j.posted_date_key,
           j.job_field, j.experience_band,
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


# ---------------------------------------------------------------------------
# fields.json: market analysis per field (job family) and per popular title
# ---------------------------------------------------------------------------
MIN_TITLE_JOBS = 15


def _group_profile(con, where: str, params: list, top: int = 12) -> dict:
    """Titles, skills, companies, cities, salary... for one slice of jobs."""
    def q(sql, n=top):
        cur = con.execute(sql + f" LIMIT {n}", params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    base = f"FROM jobs WHERE {where}"
    total, known = con.execute(f"SELECT count(*), count(*) FILTER (WHERE skills_known) {base}", params).fetchone()
    salary = con.execute(f"""SELECT count(*), round(median(salary_min)), round(quantile_cont(salary_min, 0.25)),
                                    round(quantile_cont(salary_min, 0.75))
                             {base} AND salary_min IS NOT NULL AND salary_currency = 'SAR'
                               AND coalesce(salary_period, 'month') ILIKE 'month%'""", params).fetchone()
    prof = {
        "jobs": total,
        "skills_basis": known,   # postings whose description/skills we could read
        "top_titles": q(f"SELECT job_title AS name, count(*) AS jobs {base} GROUP BY 1 ORDER BY 2 DESC, 1"),
        "top_skills": q(f"""SELECT k.skill_name AS name, count(*) AS jobs,
                                   round(100.0 * count(*) / greatest({known}, 1), 1) AS pct
                            FROM jobs j JOIN bridge_job_posting_skill b USING (job_posting_key)
                            JOIN dim_skill k USING (skill_key)
                            WHERE {where} AND j.skills_known
                            GROUP BY 1 ORDER BY 2 DESC, 1""", 25),
        "top_companies": q(f"SELECT company_name AS name, count(*) AS jobs {base} AND company_name <> 'Unknown' "
                           "GROUP BY 1 ORDER BY 2 DESC, 1"),
        "top_cities": q(f"SELECT city AS name, count(*) AS jobs {base} AND city <> 'Unknown' GROUP BY 1 ORDER BY 2 DESC, 1", 8),
        "work_mode": q(f"SELECT work_mode AS name, count(*) AS jobs {base} AND work_mode <> 'Unknown' GROUP BY 1 ORDER BY 2 DESC", 5),
        "experience": q(f"SELECT experience_band AS name, count(*) AS jobs {base} AND experience_band <> 'Unknown' "
                        "GROUP BY 1 ORDER BY 1", 5),
        "salary": {"sample": salary[0], "median": salary[1], "p25": salary[2], "p75": salary[3]} if salary[0] >= 5 else None,
        "monthly": q(f"""SELECT substr(CAST(posted_date_key AS VARCHAR), 1, 6) AS month, count(*) AS jobs
                         {base} GROUP BY 1 ORDER BY 1 DESC""", 12)[::-1],
    }
    for sk in prof["top_skills"]:
        sk["soft"] = sk["name"] in taxonomy.SOFT_SKILLS
    return prof


def build_fields(con) -> dict:
    total = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    fields = []
    for fid, en, ar in con.execute("SELECT job_field, field_name_en, field_name_ar FROM dim_field").fetchall():
        prof = _group_profile(con, "job_field = ?", [fid])
        if prof["jobs"]:
            fields.append({"id": fid, "name_en": en, "name_ar": ar,
                           "share": round(100.0 * prof["jobs"] / max(total, 1), 1), **prof})
    fields.sort(key=lambda f: (f["id"] == "other", -f["jobs"]))

    titles = []
    for title, fid, n in con.execute(f"""SELECT job_title, mode(job_field), count(*) FROM jobs GROUP BY 1
                                        HAVING count(*) >= {MIN_TITLE_JOBS} ORDER BY 3 DESC""").fetchall():
        prof = _group_profile(con, "job_title = ?", [title], top=8)
        prof.pop("top_titles")
        titles.append({"title": title, "field": fid, **prof})
    # The whole market in one profile (shown first on the insights page).
    market = _group_profile(con, "TRUE", [])
    market["companies"] = con.execute("SELECT count(DISTINCT company_name) FROM jobs "
                                      "WHERE company_name <> 'Unknown'").fetchone()[0]
    return {"updated_at": storage.iso(), "total_jobs": total, "market": market,
            "fields": fields, "titles": titles}


DETAIL_SHARDS = 2          # first N hex characters of job_id -> 256 files
MAX_DESC_CHARS = 8000


def write_details(con, out: Path, jobs: list[dict]) -> int:
    """details/<xx>.json: everything the job page needs, for the jobs whose id
    starts with <xx>. The job page loads one small file instead of all jobs."""
    silver = storage.data_dir() / "lake" / "silver"
    extra = {}
    if io.exists(silver):
        for job_id, desc, apply_url, industry in con.execute(
                f"SELECT job_id, description, apply_url, industry FROM {io.parquet(silver)} WHERE is_primary").fetchall():
            extra[job_id] = {"description": (desc or "")[:MAX_DESC_CHARS] or None,
                             "apply_url": apply_url, "industry": industry}
    shards: dict[str, dict] = {}
    for j in jobs:
        rec = {**j, **{k: v for k, v in extra.get(j["job_id"], {}).items() if v}}
        shards.setdefault(j["job_id"][:DETAIL_SHARDS], {})[j["job_id"]] = rec
    folder = out / "details"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for prefix, recs in shards.items():
        write_compact(folder / f"{prefix}.json", recs)
    return len(shards)


def run(log=print) -> dict:
    con = io.connect()
    _views(con)
    max_age = int(os.environ.get("EXPORT_MAX_AGE_DAYS", "0") or 0)
    age = (f"strptime(CAST(j.posted_date_key AS VARCHAR), '%Y%m%d') >= current_date - INTERVAL {max_age} DAY"
           if max_age else "TRUE")
    # The jobs list (and the dashboard built on it) shows open jobs only;
    # stats.json and fields.json keep the full history for the analysis.
    age += " AND j.is_active"
    out = site_dir()
    cur = con.execute(JOBS_SQL.format(age=age))
    names = [d[0] for d in cur.description]
    # Null fields are dropped to keep the file small; the site treats missing as empty.
    jobs = [{k: v for k, v in zip(names, row) if v is not None} for row in cur.fetchall()]
    write_compact(out / "jobs.json", jobs)
    exported = len(jobs)
    detail_files = write_details(con, out, jobs)
    write_compact(out / "stats.json", build_stats(con))
    fields = build_fields(con)
    write_compact(out / "fields.json", fields)
    write_compact(out / "social.json", social.build(jobs, fields))
    result = {"exported_jobs": exported, "detail_files": detail_files, "closed_hidden": con.execute(
                  "SELECT count(*) FROM jobs WHERE NOT is_active").fetchone()[0], "fields": len(fields["fields"]), "title_profiles": len(fields["titles"]),
              "site_dir": str(out)}
    log(f"[export] {result}")
    return result
