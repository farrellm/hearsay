from pathlib import Path

import pytest
from pydantic import ValidationError

from hearsay.config import Config, load_config

EXAMPLE = Path(__file__).resolve().parents[2] / "hearsay.toml.example"


def test_defaults_without_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HEARSAY_CONFIG", raising=False)
    assert load_config() == Config()


def test_relative_paths_resolve_against_the_file(tmp_path: Path) -> None:
    (tmp_path / "conf").mkdir()
    path = tmp_path / "conf" / "hearsay.toml"
    path.write_text('data_dir = "d"\nport = 9000\n[colab]\nssh_key = "k/id"\n')
    config = load_config(path)
    assert config.port == 9000
    assert config.data_dir == tmp_path / "conf" / "d"
    assert config.ssh_key == tmp_path / "conf" / "k" / "id"
    assert config.db_path == tmp_path / "conf" / "d" / "hearsay.db"


def test_env_var_names_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "other.toml"
    path.write_text("port = 9001\n")
    monkeypatch.setenv("HEARSAY_CONFIG", str(path))
    assert load_config().port == 9001


def test_typos_are_errors(tmp_path: Path) -> None:
    path = tmp_path / "hearsay.toml"
    path.write_text("[scheduler]\nmin_batch_minuets = 60\n")
    with pytest.raises(ValidationError):
        load_config(path)


def test_example_file_loads() -> None:
    config = load_config(EXAMPLE)
    assert config.port == 8452
    assert config.colab.session_name == "hearsay"
