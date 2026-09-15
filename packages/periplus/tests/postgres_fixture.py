"""Each opt-in PostgreSQL test owns a database, including its fixed schemas."""
from contextlib import contextmanager
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


@contextmanager
def isolated_database(url):
    url = make_url(url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "periplus_test_" + uuid4().hex
    engine = None
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        engine = create_engine(url.set(database=name))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
