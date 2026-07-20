#!/usr/bin/env python3
"""Exercise governor capacity policy against real JetStream KV storage."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from uuid import uuid4

from nats.js.api import KeyValueConfig, StorageType

from runtime.nats_client import connect_nats
from runtime.resource_governor import (
    ResourceCapacityUnavailable,
    ResourceLimits,
    catalogue_request,
    resource_permits,
    resource_usage,
)
from reliability_support import capture_diagnostics


LIMITS = ResourceLimits(catalogue=4, object_io=4)


async def wait_for_usage(bucket, predicate, *, timeout: float = 3):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        usage = {
            item.name: item
            for item in await resource_usage(bucket, limits=LIMITS)
        }["catalogue:hot"]
        if predicate(usage):
            return usage
        await asyncio.sleep(0.05)
    raise RuntimeError("governor state did not reach the expected condition")


async def prove_capacity_is_replica_invariant(bucket) -> int:
    active = 0
    maximum_active = 0
    lock = asyncio.Lock()
    capacity_reached = asyncio.Event()
    release = asyncio.Event()

    async def caller(index: int) -> None:
        nonlocal active, maximum_active
        async with resource_permits(
            bucket,
            catalogue_request(
                f"replica-{index}",
                service_class="live",
                limits=LIMITS,
            ),
            limits=LIMITS,
            acquire_timeout=5,
        ):
            async with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == LIMITS.catalogue:
                    capacity_reached.set()
            await release.wait()
            async with lock:
                active -= 1

    tasks = [asyncio.create_task(caller(index)) for index in range(12)]
    try:
        await asyncio.wait_for(capacity_reached.wait(), timeout=3)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    if maximum_active != LIMITS.catalogue:
        raise RuntimeError(
            f"expected {LIMITS.catalogue} concurrent grants, observed {maximum_active}"
        )
    return maximum_active


async def prove_backfill_ceiling(bucket) -> int:
    request = catalogue_request(
        "backfill-one",
        service_class="backfill",
        limits=LIMITS,
    )
    async with resource_permits(bucket, request, limits=LIMITS):
        try:
            async with resource_permits(
                bucket,
                catalogue_request(
                    "backfill-two",
                    service_class="backfill",
                    limits=LIMITS,
                ),
                limits=LIMITS,
                acquire_timeout=0.2,
            ):
                raise RuntimeError("backfill exceeded its configured ceiling")
        except ResourceCapacityUnavailable:
            pass
        usage = await wait_for_usage(bucket, lambda value: value.backfill == 1)
        return usage.backfill


async def prove_reserve_reclamation(bucket) -> dict[str, int]:
    stack = AsyncExitStack()
    for index in range(4):
        await stack.enter_async_context(
            resource_permits(
                bucket,
                catalogue_request(
                    f"borrowed-critical-{index}",
                    service_class="critical",
                    limits=LIMITS,
                ),
                limits=LIMITS,
            )
        )
    live_entered = asyncio.Event()
    release_live = asyncio.Event()

    async def live_waiter() -> None:
        async with resource_permits(
            bucket,
            catalogue_request(
                "waiting-live",
                service_class="live",
                limits=LIMITS,
            ),
            limits=LIMITS,
            acquire_timeout=5,
        ):
            live_entered.set()
            await release_live.wait()

    task = asyncio.create_task(live_waiter())
    try:
        waiting = await wait_for_usage(bucket, lambda value: value.live_waiting == 1)
        await stack.aclose()
        await asyncio.wait_for(live_entered.wait(), timeout=2)
        granted = await wait_for_usage(bucket, lambda value: value.live == 1)
        return {
            "borrowed_by_critical": LIMITS.catalogue,
            "live_waiting": waiting.live_waiting,
            "reclaimed_by_live": granted.live,
        }
    finally:
        release_live.set()
        await asyncio.gather(task, return_exceptions=True)
        await stack.aclose()


async def prove_maintenance_exclusion(bucket) -> dict[str, int]:
    hot = resource_permits(
        bucket,
        catalogue_request("hot", service_class="critical", limits=LIMITS),
        limits=LIMITS,
    )
    await hot.__aenter__()
    hot_released = False
    maintenance_entered = asyncio.Event()
    release_maintenance = asyncio.Event()

    async def maintenance() -> None:
        async with resource_permits(
            bucket,
            catalogue_request(
                "maintenance",
                service_class="maintenance",
                limits=LIMITS,
                exclusive=True,
            ),
            limits=LIMITS,
            acquire_timeout=5,
        ):
            maintenance_entered.set()
            await release_maintenance.wait()

    task = asyncio.create_task(maintenance())
    try:
        waiting = await wait_for_usage(
            bucket, lambda value: value.maintenance_waiting == 1
        )
        try:
            async with resource_permits(
                bucket,
                catalogue_request(
                    "late-hot",
                    service_class="critical",
                    limits=LIMITS,
                ),
                limits=LIMITS,
                acquire_timeout=0.2,
            ):
                raise RuntimeError(
                    "hot catalogue work entered while maintenance was waiting"
                )
        except ResourceCapacityUnavailable:
            pass
        await hot.__aexit__(None, None, None)
        hot_released = True
        await asyncio.wait_for(maintenance_entered.wait(), timeout=2)
        active = await wait_for_usage(
            bucket, lambda value: value.maintenance == LIMITS.catalogue
        )
        return {
            "maintenance_waiting": waiting.maintenance_waiting,
            "maintenance_units": active.maintenance,
            "hot_units_during_maintenance": active.critical + active.live + active.backfill,
        }
    finally:
        release_maintenance.set()
        await asyncio.gather(task, return_exceptions=True)
        if not hot_released:
            await hot.__aexit__(None, None, None)


async def run() -> dict[str, object]:
    client = await connect_nats()
    bucket_name = f"ATLAS_RELIABILITY_{uuid4().hex.upper()}"
    jetstream = client.jetstream()
    bucket = await jetstream.create_key_value(
        config=KeyValueConfig(
            bucket=bucket_name,
            description="Disposable Atlas reliability-test governor state",
            history=1,
            storage=StorageType.FILE,
        )
    )
    try:
        return {
            "catalogue_capacity": LIMITS.catalogue,
            "maximum_concurrent_grants": await prove_capacity_is_replica_invariant(
                bucket
            ),
            "backfill_grants": await prove_backfill_ceiling(bucket),
            "reserve_reclamation": await prove_reserve_reclamation(bucket),
            "maintenance_exclusion": await prove_maintenance_exclusion(bucket),
        }
    finally:
        await jetstream.delete_key_value(bucket_name)
        await client.drain()


def main() -> None:
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        destination = capture_diagnostics("resource-governor")
        print(f"reliability diagnostics: {destination}")
        raise
