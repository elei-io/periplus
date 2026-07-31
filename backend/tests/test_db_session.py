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
            patch.object(db_session, "get_int", return_value=3) as get_int,
            patch.object(db_session, "get_float", return_value=2.5) as get_float,
            patch.object(db_session, "create_engine") as create_engine,
            patch(
                "atlas.platform.postgres.metrics.instrument_postgres_pool"
            ) as instrument,
        ):
            returned = db_session.get_engine()

        self.assertIs(returned, create_engine.return_value)
        get_int.assert_called_once_with("ATLAS_CONTROL_POSTGRES_POOL_SIZE")
        get_float.assert_called_once_with(
            "ATLAS_CONTROL_POSTGRES_POOL_TIMEOUT_SECONDS"
        )
        create_engine.assert_called_once_with(
            "postgresql+psycopg://atlas",
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=0,
            pool_timeout=2.5,
        )
        instrument.assert_called_once_with(create_engine.return_value)

    def test_control_database_uses_the_explicit_atlas_contract(self) -> None:
        with patch.object(
            db_session,
            "get_str",
            return_value="postgresql://atlas@atlas-postgres/atlas",
        ) as get_str:
            value = db_session.get_database_url()

        get_str.assert_called_once_with("ATLAS_CONTROL_DATABASE_URL")
        self.assertEqual(
            value,
            "postgresql+psycopg://atlas@atlas-postgres/atlas",
        )


if __name__ == "__main__":
    unittest.main()
