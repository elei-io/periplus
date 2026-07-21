"""Idempotent Atlas deployment setup."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import DuplicateDatabase

from config import get_str
from control.crawl_policies.service import ensure_default_crawl_policy
from control.crawl_graphs.service import ensure_seeded_crawl_graphs
from control.domain_policies.service import ensure_default_domain_policy
from control.catalogue_fixtures import seed_catalogue_fixtures
from db.session import session_scope
from repository.catalogue import Catalogue, catalogue_config_from_env

_BACKEND_ROOT = Path(__file__).resolve().parent
_FIXTURES_ROOT = _BACKEND_ROOT.parent / "fixtures"


def ensure_catalogue_database() -> None:
    if get_str("ATLAS_CATALOGUE_CATALOG").lower() != "postgres":
        return
    catalogue_database = conninfo_to_dict(
        get_str("ATLAS_CATALOGUE_CATALOG_DSN")
    ).get("dbname")
    if not catalogue_database:
        raise ValueError("ATLAS_CATALOGUE_CATALOG_DSN must include dbname")
    with psycopg.connect(get_str("DATABASE_URL"), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (catalogue_database,),
        ).fetchone()
        if exists is not None:
            return
        try:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(catalogue_database))
            )
        except DuplicateDatabase:
            pass


def migrate_control_database() -> None:
    config = Config()
    config.set_main_option("script_location", str(_BACKEND_ROOT / "db" / "alembic"))
    command.upgrade(config, "head")


def bootstrap_catalogue() -> None:
    with Catalogue(catalogue_config_from_env()) as catalogue:
        catalogue.bootstrap()


def seed_catalogue_fixture_definitions() -> None:
    with Catalogue(catalogue_config_from_env()) as catalogue, session_scope() as session:
        seed_catalogue_fixtures(session, catalogue, _FIXTURES_ROOT)


def seed_system_control_plane() -> None:
    with session_scope() as session:
        ensure_seeded_crawl_graphs(session, _FIXTURES_ROOT)
        ensure_default_crawl_policy(session)
        ensure_default_domain_policy(session)


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(description="Set up an Atlas deployment.").parse_args(argv)
    ensure_catalogue_database()
    migrate_control_database()
    seed_system_control_plane()
    bootstrap_catalogue()
    seed_catalogue_fixture_definitions()
    print("Atlas setup complete.")


if __name__ == "__main__":
    main()
