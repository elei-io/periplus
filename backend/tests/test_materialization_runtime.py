import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from atlas.materialization.runtime import (
    MaintenanceWork,
    _discard_rebuild,
    _finalize_run,
    _process_one_batch,
    run_maintenance,
)


class MaterializationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_run_is_terminally_discarded(self) -> None:
        store = SimpleNamespace(start=AsyncMock(side_effect=KeyError("run")))
        lane_pool = SimpleNamespace(call=AsyncMock())

        with self.assertLogs(level="WARNING") as logs:
            done = await _process_one_batch(
                UUID("705ca93c-11fe-48d8-833e-c454ee726668"),
                store,
                MagicMock(),
                lane_pool,
                MagicMock(),
            )

        self.assertTrue(done)
        lane_pool.call.assert_not_awaited()
        self.assertIn("unknown run", "\n".join(logs.output))

    async def test_completed_rebuild_replay_finalizes_retired_tables(
        self,
    ) -> None:
        run = SimpleNamespace(
            status="completed",
            mode="rebuild",
        )
        store = SimpleNamespace(start=AsyncMock(return_value=run))
        lane_pool = SimpleNamespace(call=AsyncMock())

        done = await _process_one_batch(
            UUID("705ca93c-11fe-48d8-833e-c454ee726668"),
            store,
            MagicMock(),
            lane_pool,
            MagicMock(),
        )

        self.assertTrue(done)
        lane_pool.call.assert_awaited_once_with(_finalize_run, run)

    async def test_failed_rebuild_replay_retries_shadow_cleanup(
        self,
    ) -> None:
        destinations = {
            "html_elements": "_atlas_rebuild_html_elements_run"
        }
        run = SimpleNamespace(
            status="failed",
            mode="rebuild",
            destinations=destinations,
        )
        store = SimpleNamespace(start=AsyncMock(return_value=run))
        lane_pool = SimpleNamespace(call=AsyncMock())

        done = await _process_one_batch(
            UUID("705ca93c-11fe-48d8-833e-c454ee726668"),
            store,
            MagicMock(),
            lane_pool,
            MagicMock(),
        )

        self.assertTrue(done)
        lane_pool.call.assert_awaited_once_with(
            _discard_rebuild,
            destinations,
        )

    @patch("atlas.materialization.runtime.ensure_catalogue_work_stream")
    @patch("atlas.materialization.runtime._publish_queued_runs")
    @patch(
        "atlas.materialization.runtime._process_one_batch",
        side_effect=ValueError("deterministic failure"),
    )
    async def test_permanent_failure_is_recorded_cleaned_and_acked(
        self,
        _process,
        publish_queued,
        ensure_stream,
    ) -> None:
        stop = asyncio.Event()
        run_id = UUID("705ca93c-11fe-48d8-833e-c454ee726668")
        destinations = {
            "html_elements": "_atlas_rebuild_html_elements_run"
        }
        failed_run = SimpleNamespace(
            status="failed",
            mode="rebuild",
            destinations=destinations,
        )
        message = SimpleNamespace(
            data=MaintenanceWork(run_id=run_id).model_dump_json().encode(),
            in_progress=AsyncMock(),
            nak=AsyncMock(),
            term=AsyncMock(),
        )

        async def acknowledge() -> None:
            stop.set()

        message.ack = AsyncMock(side_effect=acknowledge)
        subscription = SimpleNamespace(
            fetch=AsyncMock(return_value=[message])
        )
        jetstream = SimpleNamespace(
            pull_subscribe=AsyncMock(return_value=subscription)
        )
        store = SimpleNamespace(
            fail=AsyncMock(return_value=failed_run),
        )
        lane_pool = SimpleNamespace(call=AsyncMock())

        async def wait_for_stop(*_args, **_kwargs) -> None:
            await stop.wait()

        publish_queued.side_effect = wait_for_stop

        with patch("atlas.materialization.runtime.logging.exception"):
            await run_maintenance(
                jetstream,
                MagicMock(),
                lane_pool,
                MagicMock(),
                stop=stop,
                store=store,
            )

        ensure_stream.assert_awaited_once_with(jetstream)
        store.fail.assert_awaited_once()
        lane_pool.call.assert_awaited_once_with(
            _discard_rebuild,
            destinations,
        )
        message.ack.assert_awaited_once()
        message.nak.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
