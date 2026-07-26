from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg

from repository.ingestion.worker import (
    _CatalogueHardHangError,
    _catalogue_call,
    _commit_batch_isolated,
    _dependency_probe,
    _ingestion_commit_operation_ids,
    _prepare_ingestion_job,
    _recover_uncommitted_deliveries,
    _retry_or_fail_with_heartbeat,
)
from repository.catalogue import (
    CatalogueService,
    DuckBasinUnavailableError,
    ExistingCatalogueIdentities,
)
from repository.ingestion.health import HealthMonitor


@asynccontextmanager
async def _admitted(*_args, **_kwargs):
    yield


class IngestionFenceFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_infrastructure_outage_does_not_consume_failure_budget(
        self,
    ) -> None:
        message = SimpleNamespace(nak=AsyncMock())
        with patch(
            "repository.ingestion.worker.record_ingestion_processing_failure",
            new=AsyncMock(),
        ) as record_failure:
            await _retry_or_fail_with_heartbeat(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                message,
                SimpleNamespace(request_id="request"),
                DuckBasinUnavailableError(
                    "provider unavailable",
                    retry_after_seconds=7,
                ),
                asyncio.Lock(),
            )

        record_failure.assert_not_awaited()
        message.nak.assert_awaited_once_with(delay=7)

    async def test_stuck_native_catalogue_call_requests_process_exit(self) -> None:
        with (
            patch(
                "repository.ingestion.worker._terminate_for_catalogue_hang"
            ) as terminate,
            self.assertRaises(_CatalogueHardHangError),
        ):
            await _catalogue_call(
                lambda: __import__("time").sleep(0.05),
                description="stuck test call",
                hard_timeout_seconds=0.001,
            )

        terminate.assert_called_once_with("stuck test call", 0.001)

    async def test_restart_naks_only_work_that_cannot_have_committed(self) -> None:
        fetched = SimpleNamespace(nak=AsyncMock())
        accepted = SimpleNamespace(nak=AsyncMock())
        ingestor = SimpleNamespace(discard_prepared=MagicMock())
        metrics = SimpleNamespace(recovery=MagicMock())
        prepared = [SimpleNamespace()]

        await _recover_uncommitted_deliveries(
            ingestor,
            prepared=prepared,
            fetched_messages=(fetched,),
            accepted_messages=(accepted,),
            uncertain_commit=False,
            lane_metrics=metrics,
        )

        ingestor.discard_prepared.assert_called_once_with(prepared)
        fetched.nak.assert_awaited_once_with()
        accepted.nak.assert_awaited_once_with()
        metrics.recovery.assert_called_once_with("shutdown_nak")

    async def test_restart_retains_uncertain_commit_until_ack_timeout(self) -> None:
        fetched = SimpleNamespace(nak=AsyncMock())
        accepted = SimpleNamespace(nak=AsyncMock())
        ingestor = SimpleNamespace(discard_prepared=MagicMock())
        metrics = SimpleNamespace(recovery=MagicMock())

        await _recover_uncommitted_deliveries(
            ingestor,
            prepared=[SimpleNamespace()],
            fetched_messages=(fetched,),
            accepted_messages=(accepted,),
            uncertain_commit=True,
            lane_metrics=metrics,
        )

        ingestor.discard_prepared.assert_not_called()
        fetched.nak.assert_awaited_once_with()
        accepted.nak.assert_not_awaited()
        self.assertEqual(
            metrics.recovery.call_args_list,
            [
                unittest.mock.call("uncertain_commit_ack_timeout"),
                unittest.mock.call("shutdown_nak"),
            ],
        )

    async def test_health_probe_serializes_catalogue_access(self) -> None:
        client = SimpleNamespace(is_connected=True)
        ingestor = SimpleNamespace(probe=MagicMock())
        monitor = HealthMonitor()

        async def stop_after_probe(_interval: float) -> None:
            raise asyncio.CancelledError

        with (
            patch(
                "repository.ingestion.worker.get_float",
                side_effect=lambda name: {
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": 1.0,
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": 1.0,
                }[name],
            ),
            patch(
                "repository.ingestion.worker.asyncio.sleep",
                new=stop_after_probe,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await _dependency_probe(
                    client,
                    ingestor,
                    asyncio.Lock(),
                    monitor,
                )

        ingestor.probe.assert_called_once_with()
        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_health_probe_timeout_excludes_connection_lock_wait(self) -> None:
        client = SimpleNamespace(is_connected=True)
        ingestor = SimpleNamespace(probe=MagicMock())
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        connection_lock = asyncio.Lock()
        await connection_lock.acquire()

        with patch(
            "repository.ingestion.worker.get_float",
            side_effect=lambda name: {
                "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": 60.0,
                "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": 0.01,
            }[name],
        ):
            probe = asyncio.create_task(
                _dependency_probe(
                    client,
                    ingestor,
                    connection_lock,
                    monitor,
                )
            )
            await asyncio.sleep(0.03)
            self.assertFalse(probe.done())
            ingestor.probe.assert_not_called()
            self.assertEqual(monitor.status(), (True, "ready"))

            connection_lock.release()
            for _attempt in range(20):
                if ingestor.probe.called:
                    break
                await asyncio.sleep(0.01)
            probe.cancel()
            await asyncio.gather(probe, return_exceptions=True)

        ingestor.probe.assert_called_once_with()
        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_slow_health_probe_does_not_open_process_circuit(self) -> None:
        client = SimpleNamespace(is_connected=True)
        ingestor = SimpleNamespace(
            probe=MagicMock(side_effect=lambda: __import__("time").sleep(0.03))
        )
        monitor = HealthMonitor()
        monitor.dependencies_ready()
        circuit = SimpleNamespace(
            is_closed=AsyncMock(return_value=True),
            unavailable=AsyncMock(),
        )

        with patch(
            "repository.ingestion.worker.get_float",
            side_effect=lambda name: {
                "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": 60.0,
                "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": 0.01,
            }[name],
        ):
            probe = asyncio.create_task(
                _dependency_probe(
                    client,
                    ingestor,
                    asyncio.Lock(),
                    monitor,
                    circuit=circuit,
                )
            )
            for _attempt in range(20):
                if ingestor.probe.called and monitor.status() == (True, "ready"):
                    break
                await asyncio.sleep(0.01)
            probe.cancel()
            await asyncio.gather(probe, return_exceptions=True)

        ingestor.probe.assert_called_once_with()
        circuit.unavailable.assert_not_awaited()
        self.assertEqual(monitor.status(), (True, "ready"))

    async def test_health_probe_reports_a_disconnected_nats_client(self) -> None:
        client = SimpleNamespace(is_connected=False)
        ingestor = SimpleNamespace(probe=MagicMock())
        monitor = HealthMonitor()

        async def stop_after_probe(_interval: float) -> None:
            raise asyncio.CancelledError

        with (
            patch(
                "repository.ingestion.worker.get_float",
                side_effect=lambda name: {
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_INTERVAL_SECONDS": 1.0,
                    "ATLAS_INGESTION_WORKER_HEALTH_PROBE_TIMEOUT_SECONDS": 1.0,
                }[name],
            ),
            patch(
                "repository.ingestion.worker.asyncio.sleep",
                new=stop_after_probe,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await _dependency_probe(
                    client,
                    ingestor,
                    asyncio.Lock(),
                    monitor,
                )

        ingestor.probe.assert_not_called()
        self.assertEqual(monitor.status(), (False, "NATS is not connected"))

    async def test_preparation_reads_raw_data_without_global_admission(self) -> None:
        message = SimpleNamespace(in_progress=AsyncMock())
        crawl = SimpleNamespace()
        job = SimpleNamespace(
            request_id="request",
            crawl=crawl,
            crawl_attempts=(),
            crawl_steps=(),
        )
        ingestor = SimpleNamespace(prepare_from_raw=MagicMock(return_value="prepared"))
        self.assertEqual(
            await _prepare_ingestion_job(
                ingestor,
                message,
                job,
                {},
                asyncio.Lock(),
            ),
            "prepared",
        )

        ingestor.prepare_from_raw.assert_called_once_with(
            crawl=crawl,
            crawl_attempts=(),
            crawl_steps=(),
            known_documents={},
        )

    async def test_fence_unavailability_retries_the_batch_without_terminal_failures(
        self,
    ) -> None:
        ingestor = MagicMock()
        ingestor.preflight_existing_identities.return_value = (
            ExistingCatalogueIdentities()
        )
        ingestor.commit_prepared_batch.side_effect = psycopg.OperationalError(
            "catalogue unavailable"
        )
        jobs = [
            SimpleNamespace(request_id="first", enqueued_at=datetime.now(UTC)),
            SimpleNamespace(request_id="second", enqueued_at=datetime.now(UTC)),
        ]
        messages = [
            SimpleNamespace(
                metadata=SimpleNamespace(num_delivered=3),
                nak=AsyncMock(),
            )
            for _value in jobs
        ]
        prepared = [
            SimpleNamespace(
                element_count=10,
                staged_bytes=100,
                document=None,
                artifact=None,
            ),
            SimpleNamespace(
                element_count=20,
                staged_bytes=200,
                document=None,
                artifact=None,
            ),
        ]

        with (
            patch(
                "repository.ingestion.worker.operation_leases",
                _admitted,
            ),
        ):
            await _commit_batch_isolated(
                MagicMock(),
                MagicMock(),
                ingestor,
                jobs,
                messages,
                prepared,
                asyncio.Lock(),
                MagicMock(),
            )

        ingestor.discard_prepared.assert_called_once_with(prepared)
        for message in messages:
            message.nak.assert_awaited_once_with(delay=4)

    async def test_success_is_recorded_after_commit_and_before_ack(self) -> None:
        events: list[str] = []
        ingestor = MagicMock()
        ingestor.preflight_existing_identities.return_value = (
            ExistingCatalogueIdentities()
        )

        def commit(_prepared, **_kwargs):
            events.append("ducklake_commit")
            return [SimpleNamespace()]

        ingestor.commit_prepared_batch.side_effect = commit
        job = SimpleNamespace(
            request_id="request",
            enqueued_at=datetime.now(UTC),
        )
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=1),
            ack=AsyncMock(side_effect=lambda: events.append("ack")),
        )
        prepared = [
            SimpleNamespace(
                element_count=10,
                staged_bytes=100,
                document=None,
                artifact=None,
            )
        ]

        async def store_result(*_args, **_kwargs):
            events.append("terminal_state")

        with (
            patch(
                "repository.ingestion.worker.operation_leases",
                _admitted,
            ),
            patch(
                "repository.ingestion.worker.store_ingestion_response",
                new=store_result,
            ),
        ):
            await _commit_batch_isolated(
                MagicMock(),
                MagicMock(),
                ingestor,
                [job],
                [message],
                prepared,
                asyncio.Lock(),
                MagicMock(),
            )

        self.assertEqual(events, ["ducklake_commit", "terminal_state", "ack"])

    async def test_preflight_reduces_commit_lease_set(self) -> None:
        events: list[str] = []
        leased: list[str] = []
        ingestor = MagicMock()
        ingestor.preflight_existing_identities.side_effect = lambda _prepared: (
            events.append("preflight")
            or ExistingCatalogueIdentities(
                documents=frozenset({"sha256:existing-document"}),
            )
        )
        ingestor.commit_prepared_batch.side_effect = lambda *_args, **_kwargs: (
            events.append("ducklake_commit") or [SimpleNamespace()]
        )
        job = SimpleNamespace(
            request_id="request",
            enqueued_at=datetime.now(UTC),
        )
        message = SimpleNamespace(
            metadata=SimpleNamespace(num_delivered=1),
            ack=AsyncMock(),
        )
        prepared = [
            SimpleNamespace(
                element_count=10,
                staged_bytes=100,
                document=SimpleNamespace(
                    document_id="sha256:existing-document"
                ),
                artifact=None,
                replace_projection=False,
            )
        ]

        @asynccontextmanager
        async def capture_leases(_store, operation_ids, *, phase):
            self.assertEqual(phase, "ingestion-commit")
            events.append("leases")
            leased.extend(operation_ids)
            yield

        with (
            patch(
                "repository.ingestion.worker.operation_leases",
                capture_leases,
            ),
            patch(
                "repository.ingestion.worker.store_ingestion_response",
                new=AsyncMock(),
            ),
        ):
            await _commit_batch_isolated(
                MagicMock(),
                MagicMock(),
                ingestor,
                [job],
                [message],
                prepared,
                asyncio.Lock(),
                MagicMock(),
            )

        self.assertEqual(events, ["preflight", "leases", "ducklake_commit"])
        self.assertEqual(leased, ["request:request"])
        message.ack.assert_awaited_once_with()

    def test_commit_fence_covers_every_canonical_identity(self) -> None:
        jobs = [
            SimpleNamespace(request_id="crawl-one"),
            SimpleNamespace(request_id="crawl-two"),
        ]
        prepared = [
            SimpleNamespace(
                document=SimpleNamespace(document_id="sha256:document"),
                artifact=None,
                replace_projection=False,
            ),
            SimpleNamespace(
                document=None,
                artifact=SimpleNamespace(artifact_id="sha256:artifact"),
                replace_projection=False,
            ),
        ]

        self.assertEqual(
            _ingestion_commit_operation_ids(jobs, prepared),
            (
                "artifact:sha256:artifact",
                "document:sha256:document",
                "request:crawl-one",
                "request:crawl-two",
            ),
        )

    def test_commit_fence_skips_existing_immutable_identities(self) -> None:
        jobs = [SimpleNamespace(request_id="crawl-one")]
        prepared = [
            SimpleNamespace(
                document=SimpleNamespace(document_id="sha256:existing-document"),
                artifact=SimpleNamespace(artifact_id="sha256:existing-artifact"),
                replace_projection=False,
            ),
            SimpleNamespace(
                document=SimpleNamespace(document_id="sha256:replacement"),
                artifact=None,
                replace_projection=True,
            ),
        ]

        self.assertEqual(
            _ingestion_commit_operation_ids(
                jobs,
                prepared,
                existing_identities=ExistingCatalogueIdentities(
                    documents=frozenset(
                        {
                            "sha256:existing-document",
                            "sha256:replacement",
                        }
                    ),
                    artifacts=frozenset({"sha256:existing-artifact"}),
                ),
            ),
            (
                "document:sha256:replacement",
                "request:crawl-one",
            ),
        )

    def test_catalogue_preflight_uses_one_bounded_identity_query(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas_test"
        catalogue.config.schema = "main"
        catalogue.trusted_remote_rows.return_value = [
            ("document", "sha256:document"),
            ("artifact", "sha256:artifact"),
        ]
        service = CatalogueService(catalogue)

        self.assertEqual(
            service.preflight_existing_identities(
                document_ids=["sha256:document"],
                artifact_ids=["sha256:artifact"],
            ),
            ExistingCatalogueIdentities(
                documents=frozenset({"sha256:document"}),
                artifacts=frozenset({"sha256:artifact"}),
            ),
        )
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertEqual(sql.count(" UNION ALL "), 1)
        self.assertIn("'sha256:document'", sql)
        self.assertIn("'sha256:artifact'", sql)
        catalogue.trusted_connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
