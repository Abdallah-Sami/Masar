import json

from masar.sources import base, tanqeeb, wuzzuf, sabbar
from masar.sources.base import SourceRun


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_landing_written_before_checkpoint(data_dir):
    run = SourceRun("tanqeeb", flush_every=2)
    run.listings_seen = 3
    run.success("u1", {"title": "A"})
    run.success("u2", {"title": "B"})          # triggers a flush
    landing = _read(run.landing_file)
    checkpoint = set(_read(run.checkpoint_file))
    assert {r["url"] for r in landing} >= checkpoint == {"u1", "u2"}
    run.failure("u3", "http_500")
    assert run.finish() == base.OK
    assert "u3" not in _read(run.checkpoint_file)   # failures are retried next run
    assert list((data_dir / "failed" / "tanqeeb").glob("*.json"))


def test_invalid_record_is_a_failure(data_dir):
    run = SourceRun("sabbar")
    assert run.success("u1", {"title": "  "}) is False
    assert run.failed and not run.records


def test_no_listings_is_unhealthy(data_dir):
    run = SourceRun("wuzzuf")
    assert run.finish() == base.NO_LISTINGS
    summary = _read(next((data_dir / "runs").glob("wuzzuf_*.json")))
    assert summary["status"] == "no_listings"


def test_layout_change_is_not_checkpointed(data_dir):
    run = SourceRun("sabbar")
    run.listings_seen = 30
    for i in range(25):
        run.soft_gone(f"u{i}", "no_details_in_page")
    assert run.finish() == base.PARSE_FAILURE
    assert _read(run.checkpoint_file) == []


def test_rare_empty_pages_count_as_gone(data_dir):
    run = SourceRun("sabbar")
    run.listings_seen = 30
    for i in range(25):
        run.success(f"ok{i}", {"title": "T"})
    run.soft_gone("gone1", "no_details_in_page")
    assert run.finish() == base.OK
    assert "gone1" in _read(run.checkpoint_file)


TANQEEB_LISTING = """
<h2 class="search-job-title"><a class="search-job-title-link" href="/jobs/1.html">A</a></h2>
<h2 class="search-job-title"><a class="search-job-title-link" href="https://evil.example/jobs/2">B</a></h2>
<footer><a href="/jobs/about">about</a></footer>"""

TANQEEB_JOB = """
<h3 class="job-title-text">Data Analyst</h3><a class="job-meta-company">Acme</a>
<div class="job-meta-item"><span>Riyadh, Saudi Arabia</span></div>
<div class="job-tags"><span class="job-tag">Remote</span><span class="job-tag">Full Time</span></div>
<span class="job-date" data-datetime="2026-10-01 10:00:00"></span>
<div class="meta-data"><div class="meta"><span>Experience</span><span>2 - 4 Years</span></div></div>
<div class="job-skills"><span class="skill">SQL</span><span class="skill">Excel</span></div>
<div id="jobDescriptionBody">Do analysis</div>
<a class="apply-btn" data-job-id="99" href="https://acme.example/apply">Apply</a>"""


def test_tanqeeb_parsers():
    assert tanqeeb.job_links_from_html(TANQEEB_LISTING) == ["https://saudi.tanqeeb.com/jobs/1.html"]
    assert tanqeeb.job_links_from_html("<footer><a href='/jobs/x'>x</a></footer>") == []
    r = tanqeeb.parse_job_page(TANQEEB_JOB, "https://saudi.tanqeeb.com/jobs/1.html")
    assert r["title"] == "Data Analyst" and r["company"] == "Acme" and r["id"] == "99"
    assert r["work_mode"] == "Remote" and r["job_type"] == "Full Time"
    assert r["skills"] == "SQL; Excel" and r["experience"] == "2 - 4 Years"


def _wuzzuf_page(slug):
    store = {"entities": {
        "job": {"collection": {
            "1": {"attributes": {"slug": "other-job", "title": "Wrong Job"}},
            "2": {"attributes": {"slug": slug, "title": "Right Job"},
                  "relationships": {"company": {"data": {"id": "c1"}}}}}},
        "company": {"collection": {"c1": {"attributes": {"name": "Co"}}}}}}
    return f"<script>Wuzzuf.initialStoreState = {json.dumps(store)};\n</script>"


def test_wuzzuf_picks_the_matching_job_only():
    url = "https://wuzzuf.net/jobs/p/right-slug"
    r = wuzzuf.parse_job_page(_wuzzuf_page("right-slug"), url)
    assert r["job_attributes"]["title"] == "Right Job" and r["company_attributes"]["name"] == "Co"
    assert wuzzuf.parse_job_page(_wuzzuf_page("not-this"), url) is None   # no silent fallback
    assert wuzzuf.canonical_url("/ar/jobs/p/x?a=1#b") == "https://wuzzuf.net/jobs/p/x"


def test_sabbar_extract_details():
    obj = {"id": "s1", "jobPositionValue": "Engineer", "partnerName": "Co", "cityValue": "Riyadh"}
    escaped = json.dumps(obj).replace('"', '\\"')
    html = f'<script>self.__next_f.push([1,"{escaped}"])</script>'
    details = sabbar.extract_job_details(html)
    assert details["jobPositionValue"] == "Engineer"
    rec = sabbar.to_record(details, "https://sabbar.com/en/jobs/x", "desc")
    assert rec["title"] == "Engineer" and rec["company"] == "Co" and rec["raw"] == obj
