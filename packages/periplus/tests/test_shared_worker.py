"""Shared capacity, idle import admission and the durable completion boundary."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from periplus.materialization.rebuilds import runtime


class SharedWorkerTests(IsolatedAsyncioTestCase):
    async def exercise(self, *, failure=False, stop_on_timeout=False):
        stop = asyncio.Event()
        events = []
        identity = uuid4()
        message = SimpleNamespace(data=str(identity).encode(), ack_sync=AsyncMock())
        subscription = SimpleNamespace(fetch=AsyncMock(), unsubscribe=AsyncMock())
        jetstream = SimpleNamespace(pull_subscribe_bind=AsyncMock(return_value=subscription))
        batch = SimpleNamespace(id=identity, attempts=1)
        build = SimpleNamespace(recipe='recipe', material_database='material')
        control = Mock()
        control.claim.return_value = (batch, build)

        async def fetch(**kwargs):
            self.assertEqual(kwargs['batch'], 1)
            if not events:
                events.append('fetched')
                return [message]
            events.append('idle')
            if stop_on_timeout:
                stop.set()
            raise TimeoutError

        async def apply(*args):
            events.append('material')
            if failure:
                raise ValueError('bad capture')
            return 3

        def finish(*args):
            events.append('checkpoint')

        async def ack():
            events.append('ack')

        async def imported():
            events.append('import')
            stop.set()

        subscription.fetch.side_effect = fetch
        message.ack_sync.side_effect = ack
        control.finish.side_effect = finish
        control.fail.side_effect = lambda *args: events.append('failed')
        idle = AsyncMock(side_effect=imported)
        client = Mock()
        with (
            patch.object(runtime, 'BuildControl', return_value=control),
            patch.object(runtime, 'Archive'),
            patch.object(runtime, 'object_store_from_env'),
            patch.object(runtime, 'ClickHouseConfig'),
            patch.object(runtime, 'ClickHouseClient', return_value=client),
            patch.object(runtime, 'recipe_digest', return_value='recipe'),
            patch.object(runtime, 'bounded_call', side_effect=apply),
        ):
            await runtime.consume(jetstream, stop, 0, idle=idle)
        subscription.unsubscribe.assert_awaited_once()
        client.close.assert_called_once()
        return events, idle

    async def test_import_waits_for_material_and_checkpoint_before_ack(self):
        events, _ = await self.exercise()
        self.assertEqual(events, ['fetched', 'material', 'checkpoint', 'ack', 'idle', 'import'])

    async def test_failure_is_retained_before_ack_and_other_work_can_continue(self):
        events, _ = await self.exercise(failure=True)
        self.assertEqual(events, ['fetched', 'material', 'failed', 'ack', 'idle', 'import'])

    async def test_shutdown_does_not_admit_an_import(self):
        _, idle = await self.exercise(stop_on_timeout=True)
        idle.assert_not_awaited()


class ImportLaneTests(IsolatedAsyncioTestCase):
    async def test_one_import_step_releases_provider_before_returning(self):
        from contextlib import asynccontextmanager
        from periplus.ingestion.imports import worker
        from periplus.platform.health import HealthMonitor
        lane = worker.ImportLane.__new__(worker.ImportLane)
        lane.leases, lane.monitor = object(), HealthMonitor()
        lane.control = Mock()
        lane.control.list.return_value = ['first', 'second']
        events = []

        async def imported(job):
            events.append(job)

        lane.worker = SimpleNamespace(step=AsyncMock(side_effect=imported))

        @asynccontextmanager
        async def lease(*args, **kwargs):
            self.assertEqual(args[1], ['common-crawl'])
            self.assertEqual(kwargs['acquire_timeout'], 0)
            events.append('lease')
            try:
                yield SimpleNamespace(lost=False)
            finally:
                events.append('released')

        with patch.object(worker, 'operation_leases', side_effect=lease):
            await lane.step()
        self.assertEqual(events, ['lease', 'first', 'released'])
        lane.worker.step.assert_awaited_once_with('first')

    async def test_lost_provider_lease_does_not_start_import(self):
        from contextlib import asynccontextmanager
        from periplus.ingestion.imports import worker
        from periplus.platform.health import HealthMonitor
        lane = worker.ImportLane.__new__(worker.ImportLane)
        lane.leases, lane.monitor = object(), HealthMonitor()
        lane.control = Mock()
        lane.control.list.return_value = ['first']
        lane.worker = SimpleNamespace(step=AsyncMock())

        @asynccontextmanager
        async def lease(*args, **kwargs):
            yield SimpleNamespace(lost=True)

        with patch.object(worker, 'operation_leases', side_effect=lease):
            await lane.step()
        lane.worker.step.assert_not_awaited()
