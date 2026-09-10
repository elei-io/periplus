"""Janitor-owned bounded retention sweeps; no control transaction spans lake I/O."""
from collections import Counter
from contextlib import AsyncExitStack
from datetime import UTC, datetime
import logging
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.runtime.frontier_models import AcquisitionRecord
from periplus.platform.catalogue import catalogue_from_env
from periplus.platform.config import get_int, get_str
from periplus.platform.telemetry import event
from periplus.retention.catalogue import RetentionCatalogue


class RetentionSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["disabled", "dry_run", "purge"] = "disabled"
    batch_size: int = Field(default=25, ge=1, le=100)
    minimum_age_seconds: int = Field(default=86400, ge=3600)
    object_grace_seconds: int = Field(default=86400, ge=3600)

    @classmethod
    def from_env(cls):
        return cls(mode=get_str("PERIPLUS_RETENTION_MODE"),
                   batch_size=get_int("PERIPLUS_RETENTION_BATCH_SIZE"),
                   minimum_age_seconds=get_int("PERIPLUS_RETENTION_MINIMUM_AGE_SECONDS"),
                   object_grace_seconds=get_int("PERIPLUS_RETENTION_OBJECT_GRACE_SECONDS"))


def current_roots(sessions, observation_ids: list[UUID], request_ids: list[UUID]):
    if len(observation_ids) > 100 or len(request_ids) > 100:
        raise ValueError("retention roots exceed batch bound")
    # Operational cleanup removes these rows only after pending outbox, selection,
    # interests and evidence receipts settle. Reuse requires an existing row, so
    # absence cannot turn into a newly admitted reference to that acquisition.
    with sessions() as session:
        observations = set(session.scalars(select(AcquisitionRecord.id).where(AcquisitionRecord.id.in_(observation_ids)))) if observation_ids else set()
        requests = set(session.scalars(select(CollectionRecord.id).where(CollectionRecord.id.in_(request_ids)))) if request_ids else set()
    return observations, requests


class RetentionSweep:
    def __init__(self, settings: RetentionSettings, sessions):
        self.settings = settings
        self.sessions = sessions
        self.after = None
        self.after_requests = None

    def run(self, *, now: datetime | None = None) -> dict:
        if self.settings.mode == "disabled":
            return {"mode": "disabled"}
        now = now or datetime.now(UTC)
        with catalogue_from_env(threads=1, memory_limit="512MB") as catalogue:
            catalogue.validate_schema()
            retention = RetentionCatalogue(catalogue)
            plan = retention.plan(now=now, limit=self.settings.batch_size,
                minimum_age_seconds=self.settings.minimum_age_seconds, after=self.after)
            requests = retention.expired_requests(now=now, limit=self.settings.batch_size, after=self.after_requests)
            blocked_observations, blocked_requests = current_roots(self.sessions,
                [item.observation_id for item in plan.candidates], requests)
            eligible = [item for item in plan.candidates if item.observation_id not in blocked_observations]
            report = {"mode": self.settings.mode, "as_of": now.isoformat(),
                      "candidates": [str(item.observation_id) for item in eligible],
                      "expired_requests": [str(item) for item in requests if item not in blocked_requests],
                      "blocked_current_observations": len(blocked_observations),
                      "blocked_current_requests": len(blocked_requests),
                      "referenced_object_bytes": sum(item.stored_bytes for item in eligible),
                      "bytes_semantics": plan.bytes_semantics, "observations_retired": 0, "requests_retired": 0}
            if self.settings.mode == "purge":
                for candidate in eligible:
                    report["observations_retired"] += retention.purge_observation(candidate, now=now)
                for identity in requests:
                    if identity not in blocked_requests:
                        report["requests_retired"] += retention.purge_request(identity, now=now)
            self.after_requests = requests[-1] if len(requests) == self.settings.batch_size else None
            self.after = ((plan.candidates[-1].finished_at, plan.candidates[-1].observation_id)
                          if len(plan.candidates) == self.settings.batch_size else None)
            event("retention_sweep", mode=self.settings.mode, candidates=len(eligible),
                  observations_retired=report["observations_retired"], requests_retired=report["requests_retired"],
                  blocked_observations=len(blocked_observations), blocked_requests=len(blocked_requests))
            return report


async def reclaim_pass(settings, objects, leases, after=None):
    """Existing NATS operation leases suppress duplicate exact-content reclamation."""
    from periplus.platform.messaging.leases import operation_leases, OperationLeaseLost, OperationLeaseUnavailable
    if settings.mode != "purge":
        return 0, after

    def candidates():
        from periplus.retention.store import candidates as pending_objects
        return pending_objects(settings.batch_size, after)
    rows = await bounded_call(candidates)
    counts = Counter(row[0] for row in rows)
    cursor = (rows[-1][1], rows[-1][2]) if len(rows) == settings.batch_size else None
    removed = deferred = 0
    async with AsyncExitStack() as held:
        hashes = []
        guards = []
        for content_hash, limit in counts.items():
            try:
                guard = await held.enter_async_context(operation_leases(
                    leases, [f"content:{content_hash}"], phase="ingestion", acquire_timeout=0))
            except OperationLeaseUnavailable:
                # The keyset wraps later; a publisher must not block other content.
                deferred += limit
                continue
            hashes.append(content_hash)
            guards.append(guard)
        if hashes:
            def check():
                if any(guard.lost for guard in guards):
                    raise OperationLeaseLost("raw object reclamation lost ownership")

            def reclaim():
                with catalogue_from_env(threads=1, memory_limit="512MB") as catalogue:
                    return RetentionCatalogue(catalogue).reclaim_objects(objects, now=datetime.now(UTC),
                        grace_seconds=settings.object_grace_seconds, limit=sum(counts[key] for key in hashes),
                        content_hashes=tuple(hashes), check_ownership=check)
            # Keep one attachment and the existing 300-second bound for the whole batch.
            removed = await bounded_call(reclaim)
    if rows:
        event("retention_reclamation", candidates=len(rows), deferred=deferred, removed=removed)
    return removed, cursor


async def bounded_call(operation):
    """Fail-stop a hung retention writer before stale marker takeover is allowed."""
    import asyncio
    import os
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=300)
    except TimeoutError:
        logging.critical("Retention operation exceeded 300 seconds; terminating janitor")
        os._exit(70)
    except asyncio.CancelledError:
        # Keep the caller's operation lease until the object operation completes.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=300)
        except TimeoutError:
            os._exit(70)
        raise
