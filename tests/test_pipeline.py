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
    assert analyst_tanqeeb["skills"][:3] == ["SQL", "Power BI", "Excel"]
    assert analyst_sabbar["job_field"] == "data_ai"

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
    fields = json.loads((tmp_path / "site" / "fields.json").read_text(encoding="utf-8"))
    data_field = next(f for f in fields["fields"] if f["id"] == "data_ai")
    assert data_field["jobs"] == 1                                       # one primary "Data Analyst"
    assert {"SQL", "Power BI"} <= {s["name"] for s in data_field["top_skills"]}
    assert py["job_field"] == "software_it" and "Python" in py["skills"]
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


def test_ai_skills_replace_keyword_skills(data_dir):
    from masar.pipeline import ai_skills
    copy_fixture(data_dir, "tanqeeb", "tanqeeb_sample.json")
    pipeline.main()
    job = next(iter(_silver(data_dir).values()))
    cache = data_dir / "ai" / "skills.jsonl"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"job_id": job["job_id"], "skills": ["MS Excel", "Financial Modeling"],
                                 "soft_skills": ["Communication Skills"], "certifications": ["SOCPA"],
                                 "languages": ["English"]}) + "\n", encoding="utf-8")
    pipeline.main()
    skills = _silver(data_dir)[job["job_url"]]["skills"]
    assert skills == ["Excel", "Financial Analysis", "SOCPA", "Communication", "English"]


def test_ai_providers_parse_answers(monkeypatch):
    from masar.pipeline import ai_skills

    class Resp:
        status_code = 200
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    answer = '{"jobs": [{"job_id": "j1", "skills": ["SQL"]}]}'
    replies = {
        "gemini": {"choices": [{"message": {"content": answer}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        "anthropic": {"content": [{"type": "text", "text": answer}], "usage": {"input_tokens": 10, "output_tokens": 5}},
    }
    for provider, data in replies.items():
        monkeypatch.setattr(ai_skills.requests, "post", lambda *a, **k: Resp(data))
        text, usage = ai_skills.call_model("x", provider, "key", "model")
        assert ai_skills.parse_answer(text)[0]["skills"] == ["SQL"]
        assert usage["input_tokens"] == 10

    for env in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert ai_skills.pick_provider() == ("groq", "g")


def test_flag_active_uses_full_list_then_age(tmp_path, monkeypatch):
    from datetime import datetime
    from masar import storage
    from masar.pipeline import silver
    monkeypatch.setenv("MASAR_DATA_DIR", str(tmp_path))
    storage.write_json_atomic(storage.path("active", "sabbar.json"),
                              {"run": "x", "listed_at": "2026-10-10T00:00:00Z",
                               "urls": ["https://sabbar.com/ar/jobs/a"]})
    now = datetime(2026, 10, 11)
    jobs = [
        {"source": "Sabbar", "job_url": "https://sabbar.com/en/jobs/a", "first_seen_at": datetime(2026, 9, 1), "posted_at": datetime(2026, 9, 1)},
        {"source": "Sabbar", "job_url": "https://sabbar.com/en/jobs/b", "first_seen_at": datetime(2026, 9, 1), "posted_at": datetime(2026, 10, 9)},
        {"source": "Sabbar", "job_url": "https://sabbar.com/en/jobs/c", "first_seen_at": datetime(2026, 10, 10, 5), "posted_at": datetime(2026, 10, 10)},
        {"source": "Tanqeeb", "job_url": "https://tanqeeb.com/x", "first_seen_at": datetime(2026, 10, 1), "posted_at": datetime(2026, 10, 1)},
        {"source": "Tanqeeb", "job_url": "https://tanqeeb.com/y", "first_seen_at": datetime(2026, 7, 1), "posted_at": datetime(2026, 7, 1)},
    ]
    silver.flag_active(jobs, now=now)
    assert [j["is_active"] for j in jobs] == [True, False, True, True, False]


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
    def json(self):
        return self._d
    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []
    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.pages.pop(0)


def test_workable_pages_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("MASAR_DATA_DIR", str(tmp_path))
    from masar.sources import workable
    from masar.sources.base import SourceRun
    job = lambda i, country="Saudi Arabia": {
        "id": str(i), "title": f"Job {i}", "url": f"https://jobs.workable.com/view/{i}",
        "location": {"city": "Riyadh", "countryName": country}, "workplace": "hybrid",
        "company": {"title": "Foodics"}, "description": "<p>Excel</p>", "created": "2026-10-03T13:32:00Z"}
    s = _Session([_Resp({"totalSize": 3, "jobs": [job(1), job(2, "UAE")], "nextPageToken": "t1"}),
                  _Resp({"totalSize": 3, "jobs": [job(3)]})])
    monkeypatch.setattr(workable, "make_session", lambda: s)
    monkeypatch.setattr(workable, "PAGE_DELAY", 0)
    jobs, complete = workable.fetch_all(SourceRun("workable"))
    assert complete and [j["id"] for j in jobs] == ["1", "3"]
    assert s.calls[1]["pageToken"] == "t1"
    rec = workable.to_record(jobs[0])
    assert rec["company"] == "Foodics" and rec["city"] == "Riyadh" and rec["work_mode"] == "hybrid"


def test_company_boards_keep_saudi_only():
    from masar.sources import companies
    gh = _Session([_Resp({"jobs": [
        {"id": 1, "title": "AML Officer", "absolute_url": "https://x/1", "location": {"name": "Riyadh, Saudi Arabia"},
         "content": "&lt;p&gt;Hi&lt;/p&gt;", "first_published": "2026-10-01T00:00:00Z"},
        {"id": 2, "title": "Dubai role", "absolute_url": "https://x/2", "location": {"name": "Dubai, UAE"}}]})])
    out = companies.from_greenhouse("tamara", "Tamara", gh)
    assert [j["title"] for j in out] == ["AML Officer"] and out[0]["city"] == "Riyadh"
    lv = _Session([_Resp([
        {"id": "a", "text": "Intern", "hostedUrl": "https://l/a", "country": "SA",
         "categories": {"location": "Riyadh", "commitment": "Internship"}, "createdAt": 1760000000000},
        {"id": "b", "text": "Istanbul", "hostedUrl": "https://l/b", "country": "TR", "categories": {"location": "Istanbul"}}])])
    out = companies.from_lever("trendyol", "Trendyol", lv)
    assert [j["title"] for j in out] == ["Intern"] and out[0]["employment_type"] == "Internship"


def test_ai_picks_first_available_model(monkeypatch):
    from masar.pipeline import ai_skills
    tried = []
    def fake(prompt, provider, key, model):
        tried.append(model)
        if model != "gemini-3.1-flash-lite":
            raise ai_skills.ModelNotFound(model)
        return '{"jobs": []}', {}
    monkeypatch.setattr(ai_skills, "call_model", fake)
    assert ai_skills.pick_model("gemini", "k", None, log=lambda *a: None) == "gemini-3.1-flash-lite"
    assert tried[:2] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
