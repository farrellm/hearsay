"""Server configuration, read from `hearsay.toml` (design.md §12)."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Repository checkout: <repo>/server/src/hearsay/config.py
_REPO = Path(__file__).resolve().parents[3]


class Section(BaseModel):
    """A TOML table: unknown keys are errors, so a typo isn't silently ignored."""

    model_config = ConfigDict(extra="forbid")


class ColabConfig(Section):
    binary: str = "colab"
    auth: Literal["oauth2", "adc"] | None = None  # None: the CLI's default
    session_name: str = "hearsay"
    ssh_key: Path | None = None  # default: <data_dir>/ssh/id_ed25519 (generated)
    worker_port: int = 8765


class SchedulerConfig(Section):
    """When GPU sessions start and stop (design.md §5.3)."""

    min_batch_minutes: float = Field(default=120, gt=0)
    max_wait_hours: float = Field(default=6, gt=0)
    idle_grace_minutes: float = Field(default=5, ge=0)
    max_session_hours: float = Field(default=10, gt=0)
    gpus: list[str] = ["T4"]  # tried in order when `colab new` finds no GPU
    keepalive_exec_minutes: float | None = None  # off: Phase 0 found it unnecessary


class BudgetConfig(Section):
    units_per_month: float | None = None  # None: no monthly cap
    min_balance: float = 0  # never start below this balance (units Degas may want)


class HuggingFaceConfig(Section):
    # Access token for the gated pyannote model; sent to the worker, never to the browser.
    token_file: Path = Path("~/.cache/huggingface/token")


class AnthropicConfig(Section):
    api_key_file: Path | None = None  # None: extraction uses the local provider only
    model: str = "claude-haiku-5-5"


class PushConfig(Section):
    # VAPID contact: push services (Apple's included) want a mailto: or https: URL.
    subject: str = "mailto:hearsay@localhost"
    key_file: Path | None = None  # default: <data_dir>/vapid_private.pem (generated)


class Config(Section):
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8452
    web_dist: Path | None = None  # default: <repo>/web/dist
    colab: ColabConfig = ColabConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    budget: BudgetConfig = BudgetConfig()
    huggingface: HuggingFaceConfig = HuggingFaceConfig()
    anthropic: AnthropicConfig = AnthropicConfig()
    push: PushConfig = PushConfig()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hearsay.db"

    @property
    def ssh_key(self) -> Path:
        return self.colab.ssh_key or self.data_dir / "ssh" / "id_ed25519"

    @property
    def vapid_key_file(self) -> Path:
        return self.push.key_file or self.data_dir / "vapid_private.pem"

    @property
    def web_dist_dir(self) -> Path:
        return self.web_dist or _REPO / "web" / "dist"


def load_config(path: Path | None = None) -> Config:
    """Load `path`, else `$HEARSAY_CONFIG`, else `./hearsay.toml`; defaults if none exists.

    Relative paths in the file are resolved against the file's directory.
    """
    if path is None:
        env = os.environ.get("HEARSAY_CONFIG")
        path = Path(env) if env else Path("hearsay.toml")
        if not env and not path.exists():
            return Config()
    with path.open("rb") as f:
        config = Config.model_validate(tomllib.load(f))
    base = path.resolve().parent

    def resolve(p: Path | None) -> Path | None:
        return None if p is None else base / p.expanduser()

    def resolve_set(p: Path) -> Path:
        return base / p.expanduser()

    return config.model_copy(
        update={
            "data_dir": resolve_set(config.data_dir),
            "web_dist": resolve(config.web_dist),
            "colab": config.colab.model_copy(update={"ssh_key": resolve(config.colab.ssh_key)}),
            "huggingface": config.huggingface.model_copy(
                update={"token_file": resolve_set(config.huggingface.token_file)}
            ),
            "anthropic": config.anthropic.model_copy(
                update={"api_key_file": resolve(config.anthropic.api_key_file)}
            ),
            "push": config.push.model_copy(update={"key_file": resolve(config.push.key_file)}),
        }
    )
