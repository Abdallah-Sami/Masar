import hashlib
import json

from masar.pipeline import io, run as pipeline
from conftest import copy_fixture


def _all_fixtures(data_dir):
    for folder, name in (("sabbar", "sabbar_sample.json"), ("tanqeeb", "tanqeeb_sample.json"),
                         ("wuzzuf", "wuzzuf_sample.json"), ("wuzzuf", "wuzzuf_old_format.json")):
        copy_fixture(data_dir, folder, name)


def _silver(data_dir):
    return {r["job_url"]: r for r in io.read_rows(data_dir / "lake" / "silver")}


def _md5(p):
    return hashlib.md5(p.read_bytes()).hexdigest()


def test_end_to_end(data_dir, tmp_path):
    _all_fixtures(data_dir)
    assert pipeline.main() == 0

    # every landing file was loaded and archived
    assert not list((data_dir / "landing").rglob("*.json"))
    assert len(list((data_dir / "archive").rglob("*.json.gz"))) == 4

    silver = _silver(data_dir)
    assert len(silver) == 5      # 2 sabbar + 2 tanqeeb + 1 wuzzuf (old+new merged)
    rejected = io.read_rows(data_dir / "lake" / "rejected.parquet")
    assert sorted(r["reason"] for r in rejected) == ["missing_title", "not_saudi"]

    analyst_sabbar = silver["https://sabbar.com/en/jobs/c-riyadh-r-data-analyst/id-a1"]
    assert analyst_sabbar["job_title"] == "Data Analyst"                 # ALL CAPS fixed
    assert analyst_sabbar["work_mode"] == "Hybrid"
    assert analyst_sabbar["salary_min"] == 8000 and analyst_sabbar["salary_max"] == 12000
    assert analyst_sabbar["apply_url"] == "https://careers.acme.example/jobs/1"
    assert analyst_sabbar["is_primary"] is True

    analyst_tanqeeb = silver["https://saudi.tanqeeb.com/jobs/t1.html"]
    assert analyst_tanqeeb["is_primary"] is False                       # same job, other source
    assert analyst_tanqeeb["skills"] == ["SQL", "Power BI", "Excel"]

    nurse = silver["https://sabbar.com/en/jobs/c-jeddah-r-nurse/id-a2"]
    assert nurse["work_mode"] == "Unknown" and nurse["salary_min"] is None
    assert nurse["posted_date_estimated"] is True and nurse["description"] is None

    # old + new Wuzzuf format of the same job merge into one row
    py = silver["https://wuzzuf.net/jobs/p/abc-python-developer-riyadh"]
    assert py["times_seen"] == 2 and py["work_mode"] == "Remote"
    assert py["experience_needed"] == "3+" and py["skills"] == ["Python", "SQL", "Docker"]

    jobs = json.loads((tmp_path / "site" / "jobs.json").read_text(encoding="utf-8"))
    assert len(jobs) == sum(r["is_primary"] for r in silver.values())
    first = jobs[0]
    for field in ("job_url", "job_id", "job_title", "company_name", "city", "source_name", "posted_date_key"):
        assert field in first
    stats = json.loads((tmp_path / "site" / "stats.json").read_text(encoding="utf-8"))
    assert stats["total_jobs"] == len(jobs)

    report = json.loads(next((data_dir / "reports").glob("*.json")).read_text())
    assert all(c["passed"] for c in report["checks"])


def test_rerun_is_idempotent(data_dir, tmp_path):
    _all_fixtures(data_dir)
    assert pipeline.main() == 0
    lake = data_dir / "lake"
    before = {str(p.relative_to(lake)): _md5(p) for p in sorted(lake.glob("silver/**/*.parquet")) + sorted((lake / "gold").glob("*.parquet"))}
    site_before = _md5(tmp_path / "site" / "jobs.json")

    assert pipeline.main() == 0          # nothing new
    after = {str(p.relative_to(lake)): _md5(p) for p in sorted(lake.glob("silver/**/*.parquet")) + sorted((lake / "gold").glob("*.parquet"))}
    assert before == after
    assert _md5(tmp_path / "site" / "jobs.json") == site_before


def test_same_file_twice_is_loaded_once(data_dir):
    copy_fixture(data_dir, "tanqeeb", "tanqeeb_sample.json")
    pipeline.main()
    copy_fixture(data_dir, "tanqeeb", "tanqeeb_sample.json")     # dropped in again by mistake
    pipeline.main()
    n = io.read_rows(data_dir / "lake" / "bronze", "SELECT count(*) AS n FROM {t}")[0]["n"]
    assert n == 2


def test_old_archives_are_pruned(data_dir, monkeypatch):
    from masar.pipeline import bronze
    arch = data_dir / "archive" / "sabbar"
    arch.mkdir(parents=True)
    (arch / "sabbar_20200101T000000Z.json.gz").write_bytes(b"x")
    (arch / "sabbar_29990101T000000Z.json.gz").write_bytes(b"x")
    (arch / "legacy_name.json.gz").write_bytes(b"x")          # no stamp: never deleted
    monkeypatch.setenv("ARCHIVE_KEEP_DAYS", "60")
    assert bronze.prune_archive() == 1
    assert sorted(p.name for p in arch.iterdir()) == ["legacy_name.json.gz", "sabbar_29990101T000000Z.json.gz"]
