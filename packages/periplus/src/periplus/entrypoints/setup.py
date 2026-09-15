"""Idempotent Periplus deployment setup."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config

from periplus.crawl.control.collections.frontier_controls import ensure_frontier_control
from periplus.crawl.control.content_policies.service import (
    ensure_default_content_policy,
)
from periplus.crawl.control.domain_policies.service import ensure_default_domain_policy
from periplus.platform.postgres.session import session_scope
from periplus.platform.clickhouse import connect_clickhouse
from periplus.platform.clickhouse.public import (
    grant_query_target,
    install_public_schema,
    install_query_user,
)
from periplus.materialization.storage import install_material_schema
from periplus.materialization.recipe import recipe_digest
from periplus.materialization.rebuilds.control import BOOTSTRAP_ID, BuildControl

_PERIPLUS_ROOT = Path(__file__).resolve().parents[1]


def migrate_control_database() -> None:
    config = Config()
    config.set_main_option(
        "script_location",
        str(_PERIPLUS_ROOT / "platform" / "postgres" / "alembic"),
    )
    command.upgrade(config, "head")


def bootstrap_catalogue(manifest_key: str | None = None) -> None:
    control = BuildControl()
    control.bootstrap(manifest_key)
    client = connect_clickhouse()
    try:
        bootstrap = control.get(BOOTSTRAP_ID)
        # Published targets belong to their worker recipe. Re-running setup must
        # neither rewrite their views nor recreate a reclaimed bootstrap target.
        if bootstrap.phase == "preparing" and bootstrap.recipe == recipe_digest():
            install_material_schema(client)
            install_public_schema(client)
        install_query_user(client)
        for build in control.builds():
            if build.protected:
                grant_query_target(client, build.query_database)
    finally:
        client.close()


def seed_system_control_plane() -> None:
    with session_scope() as session:
        ensure_frontier_control(session)
        ensure_default_content_policy(session)
        ensure_default_domain_policy(session)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Set up a Periplus deployment.")
    parser.add_argument(
        "--restore-manifest",
        help="Bootstrap empty control state from this raw recovery manifest",
    )
    arguments = parser.parse_args(argv)
    migrate_control_database()
    seed_system_control_plane()
    bootstrap_catalogue(arguments.restore_manifest)
    print("Periplus setup complete.")


if __name__ == "__main__":
    main()
