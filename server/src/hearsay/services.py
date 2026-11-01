"""Wiring of the server's long-lived components."""

from dataclasses import dataclass

from hearsay.config import Config
from hearsay.db import Database


@dataclass
class Services:
    config: Config
    db: Database

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.db.close()


def build_services(config: Config) -> Services:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return Services(config=config, db=Database(config.db_path))
