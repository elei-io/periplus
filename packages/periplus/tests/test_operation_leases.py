from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from nats.js.errors import (
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)

from periplus.platform.messaging.leases import (
    OperationLease,
    OperationLeaseUnavailable,
    OperationLeaseBackendUnavailable,
    _try_acquire,
    operation_lease_key,
    operation_leases,
)


class FakeBucket:
    def __init__(self) -> None:
        self.values: dict[str, tuple[int, bytes]] = {}
        self.revision = 0
        self.calls: list[str] = []

    async def get(self, key: str):
        self.calls.append("get")
        try:
            revision, value = self.values[key]
        except KeyError:
            raise KeyNotFoundError from None
        return SimpleNamespace(revision=revision, value=value)

    async def create(self, key: str, value: bytes) -> int:
        self.calls.append("create")
        if key in self.values:
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def update(self, key: str, value: bytes, *, last: int) -> int:
        self.calls.append("update")
        if key not in self.values or self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        self.revision += 1
        self.values[key] = (self.revision, value)
        return self.revision

    async def delete(self, key: str, *, last: int) -> None:
        self.calls.append("delete")
        if key not in self.values:
            raise KeyDeletedError
        if self.values[key][0] != last:
            raise KeyWrongLastSequenceError
        del self.values[key]


class SlowLeaseSetBucket(FakeBucket):
    def __init__(self) -> None:
        super().__init__()
        self.first_renewed = asyncio.Event()

    async def create(self, key: str, value: bytes) -> int:
        lease = OperationLease.model_validate_json(value)
        if lease.operation_id == "second":
            await asyncio.wait_for(self.first_renewed.wait(), timeout=0.1)
        return await super().create(key, value)

    async def update(self, key: str, value: bytes, *, last: int) -> int:
        lease = OperationLease.model_validate_json(value)
        revision = await super().update(key, value, last=last)
        if lease.operation_id == "first":
            self.first_renewed.set()
        return revision


class OperationLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_error_releases_acquired_prefix_and_preserves_failure(self):
        class FailingBucket(FakeBucket):
            async def create(self, key, value):
                if OperationLease.model_validate_json(value).operation_id == 'second':
                    raise PermissionError('denied')
                return await super().create(key, value)
        bucket = FailingBucket()
        with self.assertRaises(OperationLeaseBackendUnavailable) as raised:
            async with operation_leases(bucket, ['first', 'second'], phase='ingestion', acquire_timeout=0):
                self.fail('unowned work started')
        self.assertIsInstance(raised.exception.__cause__, PermissionError)
        self.assertFalse(bucket.values)

    async def test_new_operation_is_created_without_a_missing_read(self) -> None:
        bucket = FakeBucket()

        granted = await _try_acquire(
            bucket,
            phase="ingestion-commit",
            operation_id="crawl-1",
            owner="worker-1",
        )

        self.assertTrue(granted)
        self.assertEqual(bucket.calls, ["create"])

    async def test_known_operation_is_renewed_without_a_create_collision(self) -> None:
        bucket = FakeBucket()
        await _try_acquire(
            bucket,
            phase="ingestion-commit",
            operation_id="crawl-1",
            owner="worker-1",
        )
        bucket.calls.clear()

        granted = await _try_acquire(
            bucket,
            phase="ingestion-commit",
            operation_id="crawl-1",
            owner="worker-1",
            create_first=False,
        )

        self.assertTrue(granted)
        self.assertEqual(bucket.calls, ["get", "update"])

    async def test_active_operation_cannot_be_claimed_by_a_second_worker(self) -> None:
        bucket = FakeBucket()
        async with operation_leases(
            bucket, ("crawl-1",), phase="ingestion-commit"
        ):
            with self.assertRaises(OperationLeaseUnavailable):
                async with operation_leases(
                    bucket,
                    ("crawl-1",),
                    phase="ingestion-commit",
                    acquire_timeout=0,
                ):
                    pass

        async with operation_leases(
            bucket, ("crawl-1",), phase="ingestion-commit"
        ):
            self.assertTrue(bucket.values)
        self.assertFalse(bucket.values)

    async def test_expired_operation_can_be_recovered(self) -> None:
        bucket = FakeBucket()
        key = operation_lease_key("materialization-commit", "scope-1")
        expired = OperationLease(
            owner="dead-worker",
            phase="materialization-commit",
            operation_id="scope-1",
            acquired_at=datetime.now(UTC) - timedelta(minutes=2),
            heartbeat_at=datetime.now(UTC) - timedelta(minutes=2),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        await bucket.create(key, expired.model_dump_json().encode())

        async with operation_leases(
            bucket, ("scope-1",), phase="materialization-commit"
        ):
            current = OperationLease.model_validate_json(
                (await bucket.get(key)).value
            )
            self.assertNotEqual(current.owner, "dead-worker")

        self.assertFalse(bucket.values)

    async def test_acquired_prefix_is_renewed_during_large_lease_acquisition(
        self,
    ) -> None:
        bucket = SlowLeaseSetBucket()

        with patch(
            "periplus.platform.messaging.leases.CATALOGUE_OPERATION_HEARTBEAT_SECONDS",
            0.001,
        ):
            async with operation_leases(
                bucket,
                ("first", "second"),
                phase="ingestion-commit",
            ):
                self.assertTrue(bucket.first_renewed.is_set())

        self.assertFalse(bucket.values)


if __name__ == "__main__":
    unittest.main()
