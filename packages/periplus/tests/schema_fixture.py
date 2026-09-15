"""SQLite unit tests flatten namespaces; PostgreSQL integration tests retain them."""
from sqlalchemy import create_engine as sqlalchemy_create_engine


def create_engine(url, **kwargs):
    engine = sqlalchemy_create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        engine = engine.execution_options(
            schema_translate_map={"control": None, "state": None}
        )
    return engine
