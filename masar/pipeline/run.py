"""
Run the whole pipeline:  python -m masar.pipeline.run

bronze -> silver -> gold -> checks -> export, then a report in
data/reports/<run>.json with row counts, rejects, check results and the
health of the latest scraper runs.

Exit code 1 if a check fails (the website files are NOT updated in that case).
Scraper health is recorded in the report; the workflow alerts on it separately.
"""
from __future__ import annotations

import os
import sys

from masar import storage
from masar.pipeline import bronze, checks, export, gold, silver
from masar.sources import SOURCES


def latest_scraper_runs() -> list[dict]:
    runs_dir = storage.data_dir() / "runs"
    out = []
    for src in SOURCES:
        files = sorted(runs_dir.glob(f"{src}_*.json"))
        out.append(storage.read_json(files[-1]) if files else {"source": src, "status": "never_ran"})
    return out


def main() -> int:
    stamp = storage.run_stamp()
    report = {"run": stamp, "started_at": storage.iso()}
    report["bronze"] = bronze.run()
    report["silver"] = silver.run()
    report["gold"] = gold.run()
    report["checks"] = checks.run_checks()
    failed = [c for c in report["checks"] if not c["passed"]]
    if failed:
        print("[checks] FAILED:", *[f"  {c['check']}: got {c['value']}, expected {c['expected']}" for c in failed],
              sep="\n")
    else:
        print(f"[checks] all {len(report['checks'])} passed")
        report["export"] = export.run()
    report["scrapers"] = latest_scraper_runs()
    unhealthy = [r["source"] for r in report["scrapers"] if r.get("status") not in ("ok", "never_ran")]
    report["unhealthy_sources"] = unhealthy
    report["finished_at"] = storage.iso()
    storage.write_json_atomic(storage.path("reports", f"{stamp}.json"), report)

    if unhealthy:
        print(f"[health] unhealthy sources: {', '.join(unhealthy)}")
    write_github_summary(report)
    return 1 if failed else 0


def write_github_summary(report: dict) -> None:
    """Show a short table on the GitHub Actions run page."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = ["## Masar daily run", "", "| Source | Status | Seen | New | Scraped | Failed |", "|---|---|---|---|---|---|"]
    for r in report["scrapers"]:
        lines.append(f"| {r.get('source')} | {r.get('status')} | {r.get('listings_seen', '')} | "
                     f"{r.get('discovered_new', '')} | {r.get('scraped', '')} | {r.get('failed', '')} |")
    s, b = report["silver"], report["bronze"]
    lines += ["", f"- Bronze: +{b['rows_loaded']} rows (total {b['bronze_rows']})",
              f"- Silver: {s['silver_rows']} jobs, {s['primary_rows']} after cross-source dedup, "
              f"{s['rejected_rows']} rejected {s['rejected_by_reason'] or ''}",
              f"- Checks: {sum(c['passed'] for c in report['checks'])}/{len(report['checks'])} passed",
              f"- Exported to website: {report.get('export', {}).get('exported_jobs', 'NOT UPDATED')}"]
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
