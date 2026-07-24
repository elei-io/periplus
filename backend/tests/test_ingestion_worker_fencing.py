from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg

from repository.ingestion.worker import (
    _commit_batch_isolated,
    _dependency_probe,
    _ingestion_commit_operation_ids,
    _prepare_ingestion_job,
)
from repository.catalogue import CatalogueService, ExistingCatalogueIdentities
from repository.ingestion.health import HealthMonitor


@asynccontextmanager
async def _admitted(*_args, **_kwargs):
    yield


class IngestionFenceFailureTests(unittest.IsolatedAsyncioTestCase):
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
            urls=(),
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
            urls=(),
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
                urls=(),
                document=None,
                artifact=None,
            ),
            SimpleNamespace(
                element_count=20,
                staged_bytes=200,
                urls=(),
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
                urls=(),
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
                urls=frozenset({"existing-url"}),
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
                urls=(
                    SimpleNamespace(url_id="existing-url"),
                    SimpleNamespace(url_id="new-url"),
                ),
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
        self.assertEqual(leased, ["request:request", "url:new-url"])
        message.ack.assert_awaited_once_with()

    def test_commit_fence_covers_every_canonical_identity(self) -> None:
        jobs = [
            SimpleNamespace(request_id="crawl-one"),
            SimpleNamespace(request_id="crawl-two"),
        ]
        prepared = [
            SimpleNamespace(
                urls=(
                    SimpleNamespace(url_id="shared-url"),
                    SimpleNamespace(url_id="first-url"),
                ),
                document=SimpleNamespace(document_id="sha256:document"),
                artifact=None,
                replace_projection=False,
            ),
            SimpleNamespace(
                urls=(SimpleNamespace(url_id="shared-url"),),
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
                "url:first-url",
                "url:shared-url",
            ),
        )

    def test_commit_fence_skips_existing_immutable_identities(self) -> None:
        jobs = [SimpleNamespace(request_id="crawl-one")]
        prepared = [
            SimpleNamespace(
                urls=(
                    SimpleNamespace(url_id="existing-url"),
                    SimpleNamespace(url_id="new-url"),
                ),
                document=SimpleNamespace(document_id="sha256:existing-document"),
                artifact=SimpleNamespace(artifact_id="sha256:existing-artifact"),
                replace_projection=False,
            ),
            SimpleNamespace(
                urls=(),
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
                    urls=frozenset({"existing-url"}),
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
                "url:new-url",
            ),
        )

    def test_catalogue_preflight_uses_one_bounded_identity_query(self) -> None:
        catalogue = MagicMock()
        catalogue.config.alias = "atlas_test"
        catalogue.config.schema = "main"
        catalogue.trusted_remote_rows.return_value = [
            ("url", "existing-url"),
            ("document", "sha256:document"),
            ("artifact", "sha256:artifact"),
        ]
        service = CatalogueService(catalogue)

        self.assertEqual(
            service.preflight_existing_identities(
                url_ids=["existing-url", "new-url", "existing-url"],
                document_ids=["sha256:document"],
                artifact_ids=["sha256:artifact"],
            ),
            ExistingCatalogueIdentities(
                urls=frozenset({"existing-url"}),
                documents=frozenset({"sha256:document"}),
                artifacts=frozenset({"sha256:artifact"}),
            ),
        )
        sql = catalogue.trusted_remote_rows.call_args.args[0]
        self.assertEqual(sql.count(" UNION ALL "), 2)
        self.assertEqual(sql.count("'existing-url'"), 1)
        self.assertIn("'new-url'", sql)
        self.assertIn("'sha256:document'", sql)
        self.assertIn("'sha256:artifact'", sql)
        catalogue.trusted_connection.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
