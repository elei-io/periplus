from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nats.js.errors import KeyDeletedError, KeyNotFoundError, KeyWrongLastSequenceError

from runtime.resource_governor import (
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


class FakeBucket:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, bytes]] = {}
        self.revision = 0

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
            catalogue_critical_reserve=1,
            catalogue_noncritical_reserve=1,
            catalogue_backfill_max=1,
            object_read=4,
            object_write=2,
        )
        self.config = patch(
            "runtime.resource_governor.get_float",
            side_effect=lambda name: {
                "ATLAS_RESOURCE_LEASE_SECONDS": 30.0,
                "ATLAS_RESOURCE_HEARTBEAT_SECONDS": 5.0,
                "ATLAS_RESOURCE_ACQUIRE_TIMEOUT_SECONDS": 0.0,
            }[name],
        )
        self.config.start()
        self.addCleanup(self.config.stop)

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
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket, second, limits=self.limits, acquire_timeout=0
                ):
                    pass
            state = ResourceState.model_validate_json(
                (await bucket.get(RESOURCE_STATE_KEY)).value
            )
            self.assertEqual(len(state.grants), 1)

    async def test_critical_reserve_cannot_be_consumed_by_live_work(self) -> None:
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
                with self.assertRaises(ResourceCapacityUnavailable):
                    async with resource_permits(
                        bucket, live_three, limits=self.limits, acquire_timeout=0
                    ):
                        pass
                async with resource_permits(bucket, critical, limits=self.limits):
                    state = ResourceState.model_validate_json(
                        (await bucket.get(RESOURCE_STATE_KEY)).value
                    )
                    self.assertEqual(len(state.grants), 3)

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

    async def test_critical_work_cannot_consume_noncritical_reserve(self) -> None:
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
                with self.assertRaises(ResourceCapacityUnavailable):
                    async with resource_permits(
                        bucket,
                        critical_three,
                        limits=self.limits,
                        acquire_timeout=0,
                    ):
                        pass
                async with resource_permits(bucket, live, limits=self.limits):
                    state = ResourceState.model_validate_json(
                        (await bucket.get(RESOURCE_STATE_KEY)).value
                    )
                    self.assertEqual(len(state.grants), 3)

    async def test_maintenance_exclusive_bundle_waits_for_hot_work(self) -> None:
        bucket = FakeBucket()
        critical = catalogue_request(
            "ingest", service_class="critical", limits=self.limits
        )
        maintenance = catalogue_request(
            "compact",
            service_class="maintenance",
            limits=self.limits,
            exclusive=True,
        )
        async with resource_permits(bucket, critical, limits=self.limits):
            with self.assertRaises(ResourceCapacityUnavailable):
                async with resource_permits(
                    bucket, maintenance, limits=self.limits, acquire_timeout=0
                ):
                    pass
        async with resource_permits(bucket, maintenance, limits=self.limits):
            state = ResourceState.model_validate_json(
                (await bucket.get(RESOURCE_STATE_KEY)).value
            )
            self.assertEqual(state.grants[0].resources[0].units, 3)

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
            "old", domain_group="example", concurrency=2
        )
        new = remote_request(
            "new", domain_group="example", concurrency=4
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
