from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest

from nats.js.errors import (
    KeyDeletedError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)

from runtime.operation_leases import (
    OperationLease,
    OperationLeaseUnavailable,
    operation_lease_key,
    operation_leases,
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


class OperationLeaseTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
