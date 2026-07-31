from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb

from atlas.ingestion.consumer import _commit_prepared_batch
from atlas.ingestion.service import PreparedIngestion
from atlas.ingestion.worker import _ingestion_concurrency
from atlas.platform.config.environment import ConfigurationError


class IngestionConcurrencyTests(unittest.TestCase):
    def test_local_concurrency_is_configurable_within_process_bound(self) -> None:
        with patch.dict(
            os.environ,
            {"ATLAS_INGESTION_CONCURRENCY": "1"},
        ):
            self.assertEqual(_ingestion_concurrency(), 1)

    def test_scaling_beyond_process_bound_uses_replicas(self) -> None:
        with patch.dict(
            os.environ,
            {"ATLAS_INGESTION_CONCURRENCY": "5"},
        ):
            with self.assertRaisesRegex(
                ConfigurationError,
                "add replicas to scale further",
            ):
                _ingestion_concurrency()


class IngestionCommitRetryTests(unittest.TestCase):
    @patch("atlas.platform.catalogue.operations.time.sleep")
    def test_transaction_conflict_retries_the_same_immutable_batch(
        self,
        _sleep,
    ) -> None:
        prepared = [MagicMock(spec=PreparedIngestion)]
        expected = [MagicMock()]
        ingestor = MagicMock()
        ingestor.commit_prepared_batch.side_effect = [
            duckdb.TransactionException("transaction conflict"),
            expected,
        ]
        metrics = MagicMock()

        result = _commit_prepared_batch(ingestor, prepared, metrics)

        self.assertIs(result, expected)
        self.assertEqual(ingestor.commit_prepared_batch.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in ingestor.commit_prepared_batch.call_args_list],
            [prepared, prepared],
        )
        metrics.recovery.assert_called_once_with("commit_conflict")


class IngestionProcessOwnershipTests(unittest.IsolatedAsyncioTestCase):
    @patch("atlas.ingestion.worker.run_catalogue_process_presence")
    @patch("atlas.ingestion.worker.monitor_catalogue_lanes")
    @patch("atlas.ingestion.worker.run_ingestion")
    @patch("atlas.ingestion.worker.run_worker_process")
    @patch("atlas.ingestion.worker.ensure_repository_consumer")
    @patch("atlas.ingestion.worker.ensure_operation_lease_storage")
    @patch("atlas.ingestion.worker.ensure_ingestion_results")
    @patch("atlas.ingestion.worker.ensure_dead_letter_stream")
    @patch("atlas.ingestion.worker.ensure_repository_stream")
    @patch("atlas.ingestion.worker.connect_nats")
    @patch("atlas.ingestion.worker._ingestion_concurrency", return_value=3)
    async def test_one_nats_session_and_one_subscription_per_lane(
        self,
        _concurrency,
        connect_nats,
        ensure_stream,
        ensure_dead_letter,
        ensure_results,
        ensure_leases,
        ensure_consumer,
        run_worker_process,
        run_ingestion,
        monitor_lanes,
        process_presence,
    ) -> None:
        from atlas.ingestion.worker import run

        client = MagicMock()
        client.drain = AsyncMock()
        jetstream = MagicMock()
        jetstream.pull_subscribe = AsyncMock(
            side_effect=[MagicMock(), MagicMock(), MagicMock()]
        )
        client.jetstream.return_value = jetstream
        connect_nats.return_value = client
        ensure_results.return_value = MagicMock()
        ensure_leases.return_value = MagicMock()
        run_ingestion.side_effect = lambda **_kwargs: _empty_coroutine()
        monitor_lanes.side_effect = lambda **_kwargs: _empty_coroutine()
        process_presence.side_effect = lambda **_kwargs: _empty_coroutine()

        async def supervise_once(*, tasks, **_kwargs) -> None:
            self.assertEqual(
                sorted(
                    name
                    for name in tasks
                    if name.startswith("ingestion-writer-")
                ),
                [
                    "ingestion-writer-0",
                    "ingestion-writer-1",
                    "ingestion-writer-2",
                ],
            )
            for operation in tasks.values():
                operation.close()

        run_worker_process.side_effect = supervise_once

        await run()

        connect_nats.assert_awaited_once_with()
        self.assertEqual(jetstream.pull_subscribe.await_count, 3)
        ensure_stream.assert_awaited_once_with(jetstream)
        ensure_dead_letter.assert_awaited_once_with(jetstream)
        ensure_results.assert_awaited_once_with(jetstream)
        ensure_leases.assert_awaited_once_with(jetstream)
        ensure_consumer.assert_awaited_once_with(jetstream)
        self.assertEqual(run_ingestion.call_count, 3)
        for call in run_ingestion.call_args_list:
            self.assertIs(call.kwargs["jetstream"], jetstream)
            self.assertIs(
                call.kwargs["results_store"],
                ensure_results.return_value,
            )
            self.assertIs(call.kwargs["leases"], ensure_leases.return_value)
        client.drain.assert_awaited_once_with()


async def _empty_coroutine() -> None:
    return None


if __name__ == "__main__":
    unittest.main()
