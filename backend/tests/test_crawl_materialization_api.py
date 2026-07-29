from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

from atlas.crawl.api.materialization import (
    get_materialization_barrier,
    resolve_materialization_barrier,
)
from atlas.materialization.cdc.events import DMLTick


def _run(*, status: str = "completed", request_count: int = 1):
    return SimpleNamespace(
        id=uuid4(),
        status=status,
        request_count=request_count,
    )


def _state(
    snapshot: int,
    *,
    kind: str,
    document: bool = False,
    status: str = "succeeded",
):
    visit = (
        SimpleNamespace(
            document=object() if document else None,
        )
        if kind == "visit"
        else None
    )
    return SimpleNamespace(
        status=status,
        result=(
            SimpleNamespace(repository_snapshot=snapshot)
            if status == "succeeded"
            else None
        ),
        job=SimpleNamespace(visit=visit),
    )


def _tick(table: str, end_snapshot: int) -> bytes:
    return DMLTick(
        start_snapshot=end_snapshot - 1,
        end_snapshot=end_snapshot,
        table_id=1,
        table_uuid=uuid4(),
        schema_name="ingest",
        table_name=table,
        snapshot_id=end_snapshot,
        snapshot_time=datetime.now(UTC),
        schema_version=1,
    ).model_dump_json().encode()


def _runtime(
    run,
    *,
    document_snapshot: int,
    visit_snapshot: int,
):
    request = SimpleNamespace(id=uuid4())

    async def consumer_info(_stream: str, durable: str):
        sequence = 10 if "documents" in durable else 11
        return SimpleNamespace(
            ack_floor=SimpleNamespace(stream_seq=sequence)
        )

    async def get_msg(_stream: str, *, seq: int):
        if seq == 10:
            return SimpleNamespace(
                data=_tick("documents", document_snapshot)
            )
        return SimpleNamespace(data=_tick("visits", visit_snapshot))

    return (
        SimpleNamespace(
            requests=SimpleNamespace(
                list_requests=AsyncMock(return_value=[request])
            ),
            runs=object(),
            jetstream=SimpleNamespace(
                consumer_info=AsyncMock(side_effect=consumer_info),
                get_msg=AsyncMock(side_effect=get_msg),
            ),
        ),
        request,
    )


class CrawlMaterializationBarrierTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_materialized_uses_exact_crawl_snapshots(self) -> None:
        run = _run()
        runtime, _request = _runtime(
            run,
            document_snapshot=42,
            visit_snapshot=43,
        )
        states = [
            _state(40, kind="crawl"),
            _state(42, kind="visit", document=True),
        ]
        with patch(
            "atlas.crawl.api.materialization.get_ingestion_state",
            AsyncMock(side_effect=states),
        ):
            barrier = await resolve_materialization_barrier(
                run,
                runtime=runtime,
                results=object(),
            )

        self.assertEqual(barrier.status, "materialized")
        self.assertEqual(barrier.required_snapshots.documents, 42)
        self.assertEqual(barrier.required_snapshots.visits, 42)
        self.assertEqual(barrier.committed_snapshots.documents, 42)
        self.assertEqual(barrier.committed_snapshots.visits, 43)

    async def test_acknowledged_snapshot_must_reach_crawl_write(self) -> None:
        run = _run()
        runtime, _request = _runtime(
            run,
            document_snapshot=50,
            visit_snapshot=49,
        )
        states = [
            _state(48, kind="crawl"),
            _state(50, kind="visit", document=True),
        ]
        with patch(
            "atlas.crawl.api.materialization.get_ingestion_state",
            AsyncMock(side_effect=states),
        ):
            barrier = await resolve_materialization_barrier(
                run,
                runtime=runtime,
                results=object(),
            )

        self.assertEqual(barrier.status, "waiting_materialization")

    async def test_missing_ingestion_result_waits_without_reading_queues(
        self,
    ) -> None:
        run = _run()
        runtime, _request = _runtime(
            run,
            document_snapshot=100,
            visit_snapshot=100,
        )
        with patch(
            "atlas.crawl.api.materialization.get_ingestion_state",
            AsyncMock(side_effect=[_state(40, kind="crawl"), None]),
        ):
            barrier = await resolve_materialization_barrier(
                run,
                runtime=runtime,
                results=object(),
            )

        self.assertEqual(barrier.status, "waiting_ingestion")
        runtime.jetstream.consumer_info.assert_not_awaited()

    async def test_failed_ingestion_is_terminal_and_redacted(self) -> None:
        run = _run()
        runtime, _request = _runtime(
            run,
            document_snapshot=100,
            visit_snapshot=100,
        )
        with patch(
            "atlas.crawl.api.materialization.get_ingestion_state",
            AsyncMock(
                side_effect=[
                    _state(40, kind="crawl"),
                    _state(0, kind="visit", status="failed"),
                ]
            ),
        ):
            barrier = await resolve_materialization_barrier(
                run,
                runtime=runtime,
                results=object(),
            )

        self.assertEqual(barrier.status, "failed")
        self.assertEqual(
            barrier.error,
            "One or more crawl ingestion jobs failed",
        )

    async def test_active_run_waits_for_acquisition(self) -> None:
        run = _run(status="running")
        runtime, _request = _runtime(
            run,
            document_snapshot=100,
            visit_snapshot=100,
        )

        barrier = await resolve_materialization_barrier(
            run,
            runtime=runtime,
            results=object(),
        )

        self.assertEqual(barrier.status, "waiting_acquisition")
        runtime.requests.list_requests.assert_not_awaited()

    async def test_endpoint_returns_not_found(self) -> None:
        runtime = SimpleNamespace(runs=object())
        with patch(
            "atlas.crawl.api.materialization.get_graph_run",
            AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as raised:
                await get_materialization_barrier(
                    uuid4(),
                    runtime=runtime,
                    results=object(),
                )

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
