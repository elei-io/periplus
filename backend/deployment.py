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
from repository.catalogue import Catalogue, catalogue_config_from_env

_BACKEND_ROOT = Path(__file__).resolve().parent


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


def main(argv: Sequence[str] | None = None) -> None:
    argparse.ArgumentParser(description="Set up an Atlas deployment.").parse_args(argv)
    ensure_catalogue_database()
    migrate_control_database()
    bootstrap_catalogue()
    print("Atlas setup complete.")


if __name__ == "__main__":
    main()
