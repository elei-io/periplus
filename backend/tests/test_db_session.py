from __future__ import annotations

import unittest
from unittest.mock import patch

from atlas.platform.postgres import session as db_session


class DatabaseSessionTests(unittest.TestCase):
    def tearDown(self) -> None:
        db_session.get_engine.cache_clear()

    def test_engine_has_a_hard_connection_ceiling(self) -> None:
        db_session.get_engine.cache_clear()
        with (
            patch.object(
                db_session,
                "get_database_url",
                return_value="postgresql+psycopg://atlas",
            ),
            patch.object(db_session, "get_int", return_value=3),
            patch.object(db_session, "get_float", return_value=2.5),
            patch.object(db_session, "create_engine") as create_engine,
            patch(
                "atlas.platform.postgres.metrics.instrument_postgres_pool"
            ) as instrument,
        ):
            returned = db_session.get_engine()

        self.assertIs(returned, create_engine.return_value)
        create_engine.assert_called_once_with(
            "postgresql+psycopg://atlas",
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=0,
            pool_timeout=2.5,
        )
        instrument.assert_called_once_with(create_engine.return_value)


if __name__ == "__main__":
    unittest.main()
