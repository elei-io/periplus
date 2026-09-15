"""Operational retirement intent; the immutable archive owns final membership."""

from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import select, func, text
from periplus.crawl.control.collections.models import (
    CollectionRecord,
    CollectionResultRecord,
)
from periplus.crawl.runtime.frontier_models import (
    FrontierControlRecord,
    AcquisitionRecord,
)
from periplus.ingestion.archive import Archive
from periplus.retention.identities import write_claims
from periplus.retention.models import CaptureRetirementRecord
from periplus.retention.policy import expires_at
from periplus.platform.postgres.session import SessionLocal


def request_retirement(identity: UUID, archive: Archive) -> None:
    archive.read(identity)
    with SessionLocal.begin() as session:
        # Match frontier admission's lock, so no new reuse can cross the decision.
        session.get(FrontierControlRecord, 1, with_for_update=True)
        if session.get(CaptureRetirementRecord, identity):
            return
        now = datetime.now(UTC)
        protected = session.scalar(
            select(func.count())
            .select_from(CollectionResultRecord)
            .join(
                CollectionRecord,
                CollectionRecord.id == CollectionResultRecord.collection_id,
            )
            .where(
                CollectionResultRecord.capture_id == identity,
                # No expiry, unfinished collections and future expiries all protect.
                (
                    CollectionRecord.completed_at.is_(None)
                    | CollectionRecord.spec["retention_seconds"].as_integer().is_(None)
                    | (
                        CollectionRecord.completed_at
                        + CollectionRecord.spec["retention_seconds"].as_integer()
                        * text("INTERVAL '1 second'")
                        > now
                    )
                ),
            )
        )
        if protected:
            raise ValueError("Capture remains protected by a collection")
        acquisition = session.get(AcquisitionRecord, identity)
        if acquisition and acquisition.status not in (
            "succeeded",
            "failed",
            "cancelled",
        ):
            raise ValueError("Capture is still being acquired")
        session.add(CaptureRetirementRecord(capture_id=identity, requested_at=now))


def apply_retirements(archive: Archive) -> None:
    with SessionLocal() as session:
        identities = list(
            session.scalars(
                select(CaptureRetirementRecord.capture_id)
                .where(CaptureRetirementRecord.completed_at.is_(None))
                .order_by(CaptureRetirementRecord.requested_at)
                .limit(16)
            )
        )
    for identity in identities:
        error = None
        try:
            capture = archive.read(identity)
            with write_claims(
                {
                    "capture": [str(identity)],
                    "content": [capture.payload.content_id] if capture.payload else [],
                }
            ):
                archive.retire(identity)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:1000]
        with SessionLocal.begin() as session:
            row = session.get(CaptureRetirementRecord, identity, with_for_update=True)
            row.error = error
            if error is None:
                row.completed_at = datetime.now(UTC)
