"""
File-based storage for the pipeline.

Everything lives under one data directory (default ./data, override with
MASAR_DATA_DIR). In GitHub Actions that directory is the checked-out `data`
branch, so the whole lake is versioned and costs nothing.

Layout
------
data/
  landing/<source>/<source>_<run_ts>.json     raw scraper output, not yet ingested
  archive/<source>/<source>_<run_ts>.json.gz  raw files already loaded into bronze
  checkpoints/<source>.json                   every URL a scraper has fully handled
  failed/<source>/<source>_<run_ts>.json      URLs that failed in a run (retried next run)
  lake/bronze/bronze_<run_ts>.parquet         one append-only file per pipeline run
  lake/silver/posted_month=YYYY-MM/*.parquet  clean postings, rebuilt every run
  lake/rejected.parquet                       rows that failed validation, with reason
  lake/gold/<table>.parquet                   star schema
  reports/<run_ts>.json                       per-run quality report
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("MASAR_DATA_DIR", "data")).resolve()


def data_dir() -> Path:
    # Re-read the env var so tests can point the pipeline at a temp folder.
    return Path(os.environ.get("MASAR_DATA_DIR", str(DATA_DIR))).resolve()


def path(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_stamp(dt: datetime | None = None) -> str:
    """Filesystem-safe UTC timestamp, unique per second: 20261006T141500Z."""
    return (dt or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def iso(dt: datetime | None = None) -> str:
    """ISO-8601 UTC timestamp used inside records: 2026-10-06T14:15:00Z."""
    return (dt or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json_atomic(target: Path, obj) -> None:
    """Write JSON via a temp file + rename so a crash never leaves half a file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(target: Path, default=None):
    if not target.exists():
        return default
    with open(target, encoding="utf-8") as fh:
        return json.load(fh)


def gzip_move(src: Path, dest_dir: Path) -> Path:
    """Compress a landing file into the archive and delete the original."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (src.name + ".gz")
    with open(src, "rb") as fin, gzip.open(dest, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    src.unlink()
    return dest
