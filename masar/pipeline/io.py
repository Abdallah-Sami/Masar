"""Small helpers to move Python rows in and out of parquet through DuckDB."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import duckdb


def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def _sql_str(p: Path | str) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def write_rows(rows: list[dict], columns: dict[str, str], target: Path, con=None,
               partition_by: str | None = None) -> int:
    """Write rows to parquet with an explicit schema (column -> DuckDB type).

    Rows go through a temp NDJSON file, which DuckDB reads fast and strictly.
    The parquet is written to a temp name and renamed, so readers never see a
    half-written file. With `partition_by`, `target` is a folder holding one
    sub-folder per value (keeps every file small enough for GitHub).
    """
    con = con or connect()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_json = tempfile.mkstemp(suffix=".ndjson", dir=target.parent)
    tmp_parquet = target.with_name(target.name + ".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({c: r.get(c) for c in columns}, ensure_ascii=False, default=str))
                fh.write("\n")
        cols = "{" + ", ".join(f"'{c}': '{t}'" for c, t in columns.items()) + "}"
        select = ", ".join(f'"{c}"' for c in columns)
        if rows:
            src = f"read_json({_sql_str(tmp_json)}, format='newline_delimited', columns={cols})"
        else:  # empty table that still has the right schema
            src = "(SELECT " + ", ".join(f'NULL::{t} AS "{c}"' for c, t in columns.items()) + " LIMIT 0)"
        if partition_by:
            if tmp_parquet.exists():
                shutil.rmtree(tmp_parquet)
            con.execute(f"COPY (SELECT {select} FROM {src}) TO {_sql_str(tmp_parquet)} "
                        f"(FORMAT parquet, COMPRESSION zstd, PARTITION_BY ({partition_by}))")
            if not rows:
                tmp_parquet.mkdir(parents=True, exist_ok=True)
            old = target.with_name(target.name + ".old")
            if target.exists():
                target.replace(old)
            tmp_parquet.replace(target)
            if old.exists():
                shutil.rmtree(old)
        else:
            con.execute(f"COPY (SELECT {select} FROM {src}) TO {_sql_str(tmp_parquet)} (FORMAT parquet, COMPRESSION zstd)")
            os.replace(tmp_parquet, target)
    finally:
        os.remove(tmp_json)
        if tmp_parquet.is_dir():
            shutil.rmtree(tmp_parquet)
        elif tmp_parquet.exists():
            tmp_parquet.unlink()
    return len(rows)


def exists(source: Path) -> bool:
    """A parquet file, or a folder that holds at least one parquet file."""
    if source.is_dir():
        return any(source.rglob("*.parquet"))
    return source.exists()


def read_rows(source: Path, query: str | None = None, con=None) -> list[dict]:
    """Read a parquet file/folder (or run `query` with {t} = it) into a list of dicts."""
    if not exists(source):
        return []
    con = con or connect()
    sql = (query or "SELECT * FROM {t}").format(t=parquet(source))
    cur = con.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def parquet(p: Path) -> str:
    """SQL expression for a parquet file, or every parquet file under a folder."""
    if p.is_dir():
        return f"read_parquet({_sql_str(p / '**' / '*.parquet')}, union_by_name=true, hive_partitioning=true)"
    return f"read_parquet({_sql_str(p)})"
