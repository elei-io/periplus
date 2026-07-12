from __future__ import annotations

import unittest
from types import SimpleNamespace

from repository.ingestion.worker import _materialization_batch_due


class CatalogWorkerSchedulingTests(unittest.TestCase):
    def test_partial_materialization_batch_flushes_at_deadline(self) -> None:
        entries = [(object(), SimpleNamespace(file_bytes=10), object())]
        self.assertFalse(
            _materialization_batch_due(
                entries,
                started_at=10.0,
                now=14.9,
                max_items=100,
                max_bytes=1000,
                max_wait=5.0,
            )
        )
        self.assertTrue(
            _materialization_batch_due(
                entries,
                started_at=10.0,
                now=15.0,
                max_items=100,
                max_bytes=1000,
                max_wait=5.0,
            )
        )
