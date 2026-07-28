"""Idempotent Atlas deployment setup."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from atlas.crawl.control.crawl_policies.service import ensure_default_crawl_policy
from atlas.crawl.control.domain_policies.service import ensure_default_domain_policy
from atlas.platform.postgres.session import session_scope
from atlas.platform.catalogue import catalogue_from_env

_ATLAS_ROOT = Path(__file__).resolve().parents[1]


def migrate_control_database() -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(_ATLAS_ROOT / "platform" / "postgres" / "alembic"),
    )
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
