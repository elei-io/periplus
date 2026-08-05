from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from periplus.platform.config import get_float, get_int, get_str
import periplus.platform.postgres.models  # noqa: F401 - register every mapped table before ORM statements compile

def get_database_url() -> str:
    url = get_str("PERIPLUS_CONTROL_DATABASE_URL")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)

    return url


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(
        get_database_url(),
        pool_pre_ping=True,
        pool_size=get_int("PERIPLUS_CONTROL_POSTGRES_POOL_SIZE"),
        max_overflow=0,
        pool_timeout=get_float("PERIPLUS_CONTROL_POSTGRES_POOL_TIMEOUT_SECONDS"),
    )
    from periplus.platform.postgres.metrics import instrument_postgres_pool

    instrument_postgres_pool(engine)
    return engine


SessionLocal = sessionmaker(
    bind=get_engine(),
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
