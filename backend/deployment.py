"""Idempotent Atlas deployment setup."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from control.crawl_policies.service import ensure_default_crawl_policy
from control.domain_policies.service import ensure_default_domain_policy
from db.session import session_scope
from repository.catalogue import catalogue_from_env

_BACKEND_ROOT = Path(__file__).resolve().parent


def migrate_control_database() -> None:
    config = Config()
    config.set_main_option("script_location", str(_BACKEND_ROOT / "db" / "alembic"))
    command.upgrade(config, "head")


def bootstrap_catalogue() -> None:
    with catalogue_from_env() as catalogue:
        catalogue.bootstrap()


def seed_system_control_plane() -> None:
    with session_scope() as session:
        ensure_default_crawl_policy(session)
        ensure_default_domain_policy(session)


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(description="Set up an Atlas deployment.").parse_args(argv)
    migrate_control_database()
    seed_system_control_plane()
    bootstrap_catalogue()
    print("Atlas setup complete.")


if __name__ == "__main__":
    main()
