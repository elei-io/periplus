from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from nats.js.errors import NotFoundError

from api.routers.data_status import _queue_status, data_status
from runtime.catalogue_workers import (
    CatalogueLaneState,
    CatalogueWorkerState,
)


def _consumer(*, pending: int = 0, ack_pending: int = 0, redelivered: int = 0):
    return SimpleNamespace(
        num_pending=pending,
        num_ack_pending=ack_pending,
        num_redelivered=redelivered,
    )


def _worker(
    capability: str,
    *,
    lanes: int,
    active: int = 0,
) -> CatalogueWorkerState:
    now = datetime.now(UTC)
    return CatalogueWorkerState(
        worker_id=f"{capability}-worker",
        capability=capability,
        started_at=now,
        last_seen_at=now,
        lanes=tuple(
            CatalogueLaneState(
                lane_index=index,
                status="active" if index < active else "available",
                active=index < active,
            )
            for index in range(lanes)
        ),
        process_ready=True,
    )


class DataStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_distinguishes_ingestion_jobs_from_materialization_ticks(
        self,
    ) -> None:
        async def consumer_info(stream: str, durable: str):
            counts = {
                "atlas-ingestion": _consumer(),
                "atlas-material-documents-v1": _consumer(
                    pending=7,
                    ack_pending=2,
                ),
                "atlas-material-visits-v1": _consumer(),
            }
            return counts[durable]

        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(side_effect=consumer_info),
            stream_info=AsyncMock(
                return_value=SimpleNamespace(
                    state=SimpleNamespace(messages=0)
                )
            ),
        )
        runtime = SimpleNamespace(
            jetstream=jetstream,
            catalogue_workers=object(),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    materialization_runs=SimpleNamespace(
                        list=AsyncMock(return_value=[])
                    )
                )
            )
        )
        workers = [
            _worker("ingestion", lanes=4),
            _worker("materialization", lanes=8, active=1),
        ]

        with patch(
            "api.routers.data_status.list_catalogue_worker_states",
            AsyncMock(return_value=workers),
        ):
            status = await data_status(request, runtime)

        self.assertEqual(status.status, "processing")
        self.assertEqual(status.ingestion.status, "current")
        self.assertEqual(status.ingestion.queue.unit, "ingestion_jobs")
        self.assertEqual(status.ingestion.queue.total, 0)
        self.assertEqual(status.materialization.status, "processing")
        self.assertEqual(
            status.materialization.workloads[0].queue.unit,
            "cdc_messages",
        )
        self.assertEqual(
            status.materialization.workloads[0].queue.total,
            9,
        )
        self.assertIsNone(
            status.materialization.workloads[
                0
            ].queue.waiting_for_redelivery
        )
        self.assertFalse(status.source_record_lag.available)
        self.assertIsNone(status.source_record_lag.value)
        self.assertIn(
            "distinct source records",
            status.source_record_lag.reason,
        )

    async def test_missing_consumer_is_unavailable_not_zero_lag(self) -> None:
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(side_effect=NotFoundError())
        )

        queue = await _queue_status(
            jetstream,
            stream="stream",
            durable="durable",
            unit="cdc_messages",
        )

        self.assertFalse(queue.available)
        self.assertIsNone(queue.pending)
        self.assertIsNone(queue.ack_pending)
        self.assertIsNone(queue.total)

    async def test_maintenance_is_visible_but_does_not_change_live_status(
        self,
    ) -> None:
        jetstream = SimpleNamespace(
            consumer_info=AsyncMock(return_value=_consumer()),
            stream_info=AsyncMock(
                return_value=SimpleNamespace(
                    state=SimpleNamespace(messages=3)
                )
            ),
        )
        now = datetime.now(UTC)
        failed_run = SimpleNamespace(
            id=uuid4(),
            mode="rebuild",
            status="failed",
            stages=("html_elements",),
            active_stage=None,
            active_catchup_stage=None,
            source_items=10,
            source_bytes=100,
            output_rows=20,
            created_at=now,
            started_at=now,
            completed_at=now,
            error="write failed",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    materialization_runs=SimpleNamespace(
                        list=AsyncMock(return_value=[failed_run])
                    )
                )
            )
        )
        workers = [
            _worker("ingestion", lanes=4),
            _worker("materialization", lanes=8),
        ]

        with patch(
            "api.routers.data_status.list_catalogue_worker_states",
            AsyncMock(return_value=workers),
        ):
            status = await data_status(
                request,
                SimpleNamespace(
                    jetstream=jetstream,
                    catalogue_workers=object(),
                ),
            )

        self.assertEqual(status.status, "attention")
        self.assertEqual(status.ingestion.status, "attention")
        self.assertEqual(status.ingestion.dead_letters, 3)
        self.assertEqual(status.materialization.status, "current")
        self.assertEqual(status.maintenance_runs[0].error, "write failed")
        jetstream.stream_info.assert_awaited_once_with(
            "ATLAS_DEAD_LETTER",
            subjects_filter="atlas.dead_letter.ingest",
        )


if __name__ == "__main__":
    unittest.main()
