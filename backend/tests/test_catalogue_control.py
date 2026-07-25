from __future__ import annotations

import asyncio
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from api.catalogue_control import CatalogueControl


class _FakeCatalogue:
    def __init__(self) -> None:
        self.closed = False
        self.validated = False
        self.validation_error: RuntimeError | None = None
        self.lake_slug = "atlas_test"
        self.config = SimpleNamespace(schema="main")

    def validate_schema(self) -> None:
        self.validated = True
        if self.validation_error is not None:
            raise self.validation_error

    def close(self) -> None:
        self.closed = True

    def latest_snapshot(self) -> int:
        return 42


class CatalogueControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_mismatch_fails_startup_and_closes_catalogue(self) -> None:
        catalogue = _FakeCatalogue()
        catalogue.validation_error = RuntimeError("schema mismatch")
        control = CatalogueControl(factory=lambda: catalogue)

        with self.assertRaisesRegex(RuntimeError, "schema mismatch"):
            await control.start()

        self.assertTrue(catalogue.closed)

    async def test_one_connection_and_thread_serialize_all_operations(self) -> None:
        catalogue = _FakeCatalogue()
        factory_calls = 0
        active = 0
        maximum_active = 0
        thread_ids: set[int] = set()

        def factory() -> _FakeCatalogue:
            nonlocal factory_calls
            factory_calls += 1
            return catalogue

        @contextmanager
        def fake_session_scope():
            yield SimpleNamespace()

        def operation(_session, received_catalogue):
            nonlocal active, maximum_active
            self.assertIs(received_catalogue, catalogue)
            thread_ids.add(threading.get_ident())
            active += 1
            maximum_active = max(maximum_active, active)
            time.sleep(0.01)
            active -= 1

        control = CatalogueControl(factory=factory)
        with patch("api.catalogue_control.session_scope", fake_session_scope):
            await control.start()
            await asyncio.gather(*(control.run(operation) for _ in range(8)))
            snapshot = await control.latest_snapshot()
            await control.close()

        self.assertEqual(factory_calls, 1)
        self.assertEqual(maximum_active, 1)
        self.assertEqual(len(thread_ids), 1)
        self.assertEqual(snapshot, 42)
        self.assertTrue(catalogue.closed)
        self.assertTrue(catalogue.validated)


if __name__ == "__main__":
    unittest.main()
