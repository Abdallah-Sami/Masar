import json
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("MASAR_DATA_DIR", str(d))
    monkeypatch.setenv("MASAR_SITE_DIR", str(tmp_path / "site"))
    return d


def put_landing(data_dir: Path, folder: str, name: str, rows) -> Path:
    p = data_dir / "landing" / folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return p


def copy_fixture(data_dir: Path, folder: str, name: str) -> Path:
    dest = data_dir / "landing" / folder / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, dest)
    return dest
