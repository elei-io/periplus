"""Idempotent Periplus deployment setup."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from periplus.crawl.control.collections.frontier_controls import ensure_frontier_control
from periplus.crawl.control.content_policies.service import ensure_default_content_policy
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.platform.postgres.session import session_scope
from periplus.platform.catalogue import catalogue_from_env
from periplus.materialization.live import bootstrap_live_cdc

_PERIPLUS_ROOT = Path(__file__).resolve().parents[1]


def migrate_control_database() -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(_PERIPLUS_ROOT / "platform" / "postgres" / "alembic"),
    )
    command.upgrade(config, "head")


def bootstrap_catalogue() -> None:
    with catalogue_from_env() as catalogue:
        catalogue.bootstrap()


def seed_system_control_plane() -> None:
    with session_scope() as session:
        ensure_frontier_control(session)
        ensure_default_content_policy(session)
        ensure_default_domain_policy(session)


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(description="Set up a Periplus deployment.").parse_args(argv)
    migrate_control_database()
    seed_system_control_plane()
    bootstrap_catalogue()
    bootstrap_live_cdc()
    print("Periplus setup complete.")


if __name__ == "__main__":
    main()
