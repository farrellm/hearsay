from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hearsay.app import create_app
from hearsay.config import Config


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Hearsay</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log('hi')")
    (dist / "sw.js").write_text("// sw")
    return dist


@pytest.fixture
def config(tmp_path: Path, web_dist: Path) -> Config:
    return Config(data_dir=tmp_path / "data", web_dist=web_dist)


@pytest.fixture
def client(config: Config) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as c:
        yield c
