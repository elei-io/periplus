from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nats.js.errors import KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError

from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    RESOURCE_STATE_KEY,
    ResourceCapacityUnavailable,
    ResourceGrant,
    ResourceLimits,
    ResourceNeed,
    ResourceRequest,
    ResourceState,
    catalogue_request,
    remote_request,
    resource_usage,
    resource_permits,
)


async def _wait_for_waiter(bucket: FakeBucket, service_class: str) -> None:
    for _ in range(100):
        state = ResourceState.model_validate_json(
            (await bucket.get(RESOURCE_STATE_KEY)).value
        )
        if any(waiter.service_class == service_class for waiter in state.waiters):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{service_class} waiter was not registered")


class FakeBucket:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, bytes]] = {}
        self.revision = 0
        self.fail_next_updates = 0

    async def get(self, key: str):
        try:
            revision, value = self.values[key]
        except KeyError:
            raise KeyNotFoundError from None
        return SimpleNamespace(revision=revision, value=value)

    async def create(self, key: str, value: bytes) -> int:
        if key in self.values:
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def update(self, key: str, value: bytes, *, last: int) -> int:
        if key not in self.values or self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        if self.fail_next_updates:
            self.fail_next_updates -= 1
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def delete(self, key: str, *, last: int) -> None:
        if key not in self.values:
            raise KeyDeletedError
        if self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        del self.values[key]


class ResourceGovernorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.limits = ResourceLimits(
            catalogue=3,
            object_io=2,
        )

    async def test_bundle_is_atomic_when_one_resource_is_full(self) -> None:
        bucket = FakeBucket()
        first = ResourceRequest(
            operation_id="writer-1",
            service_class="live",
            resources=(
                ResourceNeed(name="catalogue:hot", units=1),
                ResourceNeed(name="object:write", units=2),
            ),
        )
        second = catalogue_request(
            "writer-2",
            service_class="live",
            object_write_units=1,
            limits=self.limits,
        )
        async with resource_permits(bucket, first, limits=self.limits):
            revision_before_probe = bucket.revision
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket, second, limits=self.limits, acquire_timeout=0
                ):
                    pass
            self.assertEqual(bucket.revision, revision_before_probe)
            state = ResourceState.model_validate_json(
                (await bucket.get(RESOURCE_STATE_KEY)).value
            )
            self.assertEqual(len(state.grants), 1)
            self.assertEqual(state.waiters, ())

    async def test_heartbeat_retries_transient_cas_contention(self) -> None:
        bucket = FakeBucket()
        request = catalogue_request(
            "renewed",
            service_class="live",
            limits=self.limits,
        )
        with (
            patch("runtime.resource_governor.RESOURCE_LEASE_SECONDS", 0.2),
            patch("runtime.resource_governor.RESOURCE_HEARTBEAT_SECONDS", 0.01),
            patch(
                "runtime.resource_governor.RESOURCE_RENEW_RETRY_INITIAL_SECONDS",
                0.001,
            ),
            patch(
                "runtime.resource_governor.RESOURCE_RENEW_RETRY_MAX_SECONDS",
                0.002,
            ),
        ):
            async with resource_permits(
                bucket,
                request,
                limits=self.limits,
            ) as guard:
                revision_before_renewal = bucket.revision
                bucket.fail_next_updates = 1
                for _ in range(100):
                    if bucket.revision > revision_before_renewal:
                        break
                    await asyncio.sleep(0.002)

                self.assertGreater(bucket.revision, revision_before_renewal)
                self.assertFalse(guard.lost)

    async def test_live_work_borrows_idle_critical_reserve(self) -> None:
        bucket = FakeBucket()
        live_one = catalogue_request(
            "live-1", service_class="live", limits=self.limits
        )
        live_two = catalogue_request(
            "live-2", service_class="live", limits=self.limits
        )
        live_three = catalogue_request(
            "live-3", service_class="live", limits=self.limits
        )
        critical = catalogue_request(
            "ingest", service_class="critical", limits=self.limits
        )
        async with resource_permits(bucket, live_one, limits=self.limits):
            async with resource_permits(bucket, live_two, limits=self.limits):
                async with resource_permits(bucket, live_three, limits=self.limits):
                    state = ResourceState.model_validate_json(
                        (await bucket.get(RESOURCE_STATE_KEY)).value
                    )
                    self.assertEqual(len(state.grants), 3)
        async with resource_permits(bucket, critical, limits=self.limits):
            pass

    async def test_backfill_has_an_independent_ceiling(self) -> None:
        bucket = FakeBucket()
        first = catalogue_request(
            "backfill-1", service_class="backfill", limits=self.limits
        )
        second = catalogue_request(
            "backfill-2", service_class="backfill", limits=self.limits
        )
        async with resource_permits(bucket, first, limits=self.limits):
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket, second, limits=self.limits, acquire_timeout=0
                ):
                    pass

    async def test_critical_work_borrows_idle_noncritical_reserve(self) -> None:
        bucket = FakeBucket()
        critical_one = catalogue_request(
            "critical-1", service_class="critical", limits=self.limits
        )
        critical_two = catalogue_request(
            "critical-2", service_class="critical", limits=self.limits
        )
        critical_three = catalogue_request(
            "critical-3", service_class="critical", limits=self.limits
        )
        live = catalogue_request("live", service_class="live", limits=self.limits)
        async with resource_permits(bucket, critical_one, limits=self.limits):
            async with resource_permits(bucket, critical_two, limits=self.limits):
                async with resource_permits(
                    bucket,
                    critical_three,
                    limits=self.limits,
                ):
                    state = ResourceState.model_validate_json(
                        (await bucket.get(RESOURCE_STATE_KEY)).value
                    )
                    self.assertEqual(len(state.grants), 3)
        async with resource_permits(bucket, live, limits=self.limits):
            pass

    async def test_waiting_live_work_reclaims_its_reserved_share(self) -> None:
        bucket = FakeBucket()
        contexts = [
            resource_permits(
                bucket,
                catalogue_request(
                    f"critical-{index}", service_class="critical", limits=self.limits
                ),
                limits=self.limits,
            )
            for index in range(3)
        ]
        for context in contexts:
            await context.__aenter__()
        live_entered = asyncio.Event()
        release_live = asyncio.Event()

        async def wait_for_live() -> None:
            async with resource_permits(
                bucket,
                catalogue_request("live", service_class="live", limits=self.limits),
                limits=self.limits,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                live_entered.set()
                await release_live.wait()

        task = asyncio.create_task(wait_for_live())
        try:
            await _wait_for_waiter(bucket, "live")
            usage = {
                item.name: item
                for item in await resource_usage(bucket, limits=self.limits)
            }
            self.assertEqual(usage["catalogue:hot"].waiting, 1)
            self.assertEqual(usage["catalogue:hot"].live_waiting, 1)
            self.assertGreaterEqual(
                usage["catalogue:hot"].oldest_wait_seconds, 0
            )
            await contexts.pop().__aexit__(None, None, None)
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket,
                    catalogue_request(
                        "late-critical",
                        service_class="critical",
                        limits=self.limits,
                    ),
                    limits=self.limits,
                    acquire_timeout=0,
                ):
                    pass
            await asyncio.wait_for(live_entered.wait(), timeout=1)
        finally:
            release_live.set()
            await asyncio.gather(task, return_exceptions=True)
            for context in reversed(contexts):
                await context.__aexit__(None, None, None)

    async def test_waiting_critical_work_reclaims_its_reserved_share(self) -> None:
        bucket = FakeBucket()
        contexts = [
            resource_permits(
                bucket,
                catalogue_request(
                    f"live-{index}", service_class="live", limits=self.limits
                ),
                limits=self.limits,
            )
            for index in range(3)
        ]
        for context in contexts:
            await context.__aenter__()
        critical_entered = asyncio.Event()
        release_critical = asyncio.Event()

        async def wait_for_critical() -> None:
            async with resource_permits(
                bucket,
                catalogue_request(
                    "critical", service_class="critical", limits=self.limits
                ),
                limits=self.limits,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                critical_entered.set()
                await release_critical.wait()

        task = asyncio.create_task(wait_for_critical())
        try:
            await _wait_for_waiter(bucket, "critical")
            await contexts.pop().__aexit__(None, None, None)
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket,
                    catalogue_request(
                        "late-live", service_class="live", limits=self.limits
                    ),
                    limits=self.limits,
                    acquire_timeout=0,
                ):
                    pass
            await asyncio.wait_for(critical_entered.wait(), timeout=1)
        finally:
            release_critical.set()
            await asyncio.gather(task, return_exceptions=True)
            for context in reversed(contexts):
                await context.__aexit__(None, None, None)

    async def test_bounded_maintenance_runs_beside_hot_work(self) -> None:
        bucket = FakeBucket()
        critical = catalogue_request(
            "ingest",
            service_class="critical",
            object_write_units=1,
            limits=self.limits,
        )
        maintenance = catalogue_request(
            "compact",
            service_class="maintenance",
            object_read_units=1,
            limits=self.limits,
        )
        async with resource_permits(bucket, critical, limits=self.limits):
            async with resource_permits(bucket, maintenance, limits=self.limits):
                state = ResourceState.model_validate_json(
                    (await bucket.get(RESOURCE_STATE_KEY)).value
                )
                self.assertEqual(
                    [grant.service_class for grant in state.grants],
                    ["critical", "maintenance"],
                )

    async def test_waiting_maintenance_does_not_stop_new_hot_admission(self) -> None:
        bucket = FakeBucket()
        object_context = resource_permits(
            bucket,
            ResourceRequest(
                operation_id="object-reader",
                service_class="live",
                resources=(ResourceNeed(name="object:read", units=2),),
            ),
            limits=self.limits,
        )
        await object_context.__aenter__()

        async def wait_for_maintenance() -> None:
            async with resource_permits(
                bucket,
                catalogue_request(
                    "maintenance",
                    service_class="maintenance",
                    object_read_units=self.limits.object_read,
                    limits=self.limits,
                ),
                limits=self.limits,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                pass

        task = asyncio.create_task(wait_for_maintenance())
        try:
            await _wait_for_waiter(bucket, "maintenance")
            async with resource_permits(
                bucket,
                catalogue_request(
                    "late-critical",
                    service_class="critical",
                    limits=self.limits,
                ),
                limits=self.limits,
                acquire_timeout=0,
            ):
                pass
        finally:
            await object_context.__aexit__(None, None, None)
            await asyncio.gather(task, return_exceptions=True)

    async def test_waiting_critical_object_work_reclaims_one_unit(self) -> None:
        bucket = FakeBucket()
        live_context = resource_permits(
            bucket,
            ResourceRequest(
                operation_id="live-reader",
                service_class="live",
                resources=(ResourceNeed(name="object:read", units=2),),
            ),
            limits=self.limits,
        )
        await live_context.__aenter__()
        critical_entered = asyncio.Event()

        async def wait_for_critical() -> None:
            async with resource_permits(
                bucket,
                ResourceRequest(
                    operation_id="critical-reader",
                    service_class="critical",
                    resources=(ResourceNeed(name="object:read", units=1),),
                ),
                limits=self.limits,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                critical_entered.set()

        task = asyncio.create_task(wait_for_critical())
        try:
            await _wait_for_waiter(bucket, "critical")
            await live_context.__aexit__(None, None, None)
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket,
                    ResourceRequest(
                        operation_id="late-live-reader",
                        service_class="live",
                        resources=(ResourceNeed(name="object:read", units=2),),
                    ),
                    limits=self.limits,
                    acquire_timeout=0,
                ):
                    pass
            await asyncio.wait_for(critical_entered.wait(), timeout=1)
        finally:
            await asyncio.gather(task, return_exceptions=True)

    async def test_old_same_class_waiter_cannot_be_bypassed(self) -> None:
        bucket = FakeBucket()
        blocker = resource_permits(
            bucket,
            ResourceRequest(
                operation_id="blocker",
                service_class="critical",
                resources=(ResourceNeed(name="object:read", units=2),),
            ),
            limits=self.limits,
        )
        await blocker.__aenter__()
        old_entered = asyncio.Event()

        async def old_live_request() -> None:
            async with resource_permits(
                bucket,
                ResourceRequest(
                    operation_id="old-live",
                    service_class="live",
                    resources=(ResourceNeed(name="object:read", units=2),),
                ),
                limits=self.limits,
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                old_entered.set()

        task = asyncio.create_task(old_live_request())
        try:
            await _wait_for_waiter(bucket, "live")
            await blocker.__aexit__(None, None, None)
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket,
                    ResourceRequest(
                        operation_id="late-live",
                        service_class="live",
                        resources=(ResourceNeed(name="object:read", units=1),),
                    ),
                    limits=self.limits,
                    acquire_timeout=0,
                ):
                    pass
            await asyncio.wait_for(old_entered.wait(), timeout=1)
        finally:
            await asyncio.gather(task, return_exceptions=True)

    async def test_expired_grant_releases_the_whole_bundle(self) -> None:
        bucket = FakeBucket()
        now = datetime.now(UTC)
        expired = ResourceGrant(
            token="dead",
            operation_id="old",
            service_class="live",
            resources=(
                ResourceNeed(name="catalogue:hot", units=2),
                ResourceNeed(name="object:write", units=2),
            ),
            acquired_at=now - timedelta(minutes=2),
            heartbeat_at=now - timedelta(minutes=2),
            expires_at=now - timedelta(minutes=1),
        )
        await bucket.create(
            RESOURCE_STATE_KEY,
            ResourceState(grants=(expired,), updated_at=now).model_dump_json().encode(),
        )
        request = catalogue_request(
            "new",
            service_class="critical",
            object_write_units=2,
            limits=self.limits,
        )
        async with resource_permits(bucket, request, limits=self.limits):
            state = ResourceState.model_validate_json(
                (await bucket.get(RESOURCE_STATE_KEY)).value
            )
            self.assertEqual([grant.operation_id for grant in state.grants], ["new"])

    async def test_remote_limit_change_does_not_create_a_second_pool(self) -> None:
        bucket = FakeBucket()
        old = remote_request(
            "old", remote_domain="example", concurrency=2
        )
        new = remote_request(
            "new", remote_domain="example", concurrency=4
        )
        async with resource_permits(bucket, old, limits=self.limits):
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket, new, limits=self.limits, acquire_timeout=0
                ):
                    pass
        async with resource_permits(bucket, new, limits=self.limits):
            state = ResourceState.model_validate_json(
                (await bucket.get(RESOURCE_STATE_KEY)).value
            )
            self.assertEqual(state.grants[0].resources[0].name, "remote:example")

    async def test_usage_projection_excludes_expired_grants(self) -> None:
        bucket = FakeBucket()
        request = catalogue_request(
            "live", service_class="live", object_read_units=2, limits=self.limits
        )
        async with resource_permits(bucket, request, limits=self.limits):
            usage = {item.name: item for item in await resource_usage(
                bucket, limits=self.limits
            )}
            self.assertEqual(usage["catalogue:hot"].live, 1)
            self.assertEqual(usage["object:read"].used, 2)


if __name__ == "__main__":
    unittest.main()
