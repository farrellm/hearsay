"""`hearsay` command-line entry point."""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from hearsay.app import create_app
from hearsay.config import Config, load_config


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args.run(config, args)


def build_parser() -> argparse.ArgumentParser:
    """Each subcommand sets `run`: the function that carries it out, given the config."""
    parser = argparse.ArgumentParser(prog="hearsay")
    parser.add_argument("--config", type=Path, help="path to hearsay.toml")
    parser.set_defaults(run=serve)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the server (default)").set_defaults(run=serve)
    return parser


def serve(config: Config, _args: argparse.Namespace) -> None:
    uvicorn.run(create_app(config), host=config.host, port=config.port)
