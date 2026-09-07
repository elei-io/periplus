"""Short transactional transitions for the continuous crawler.

The control row serializes changes to this crawler's exact admission/dispatch counters.
No method holds a transaction over browser, object store, NATS, or lake I/O.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.orm import Session

from periplus.crawl.acquisition.models import AcquisitionResult
from periplus.crawl.control.collections.exclusions import UrlExcluded, is_excluded
from periplus.crawl.control.collections.discovery import DiscoveryState
from periplus.crawl.control.collections.models import CollectionRecord
from periplus.crawl.control.collections.frontier_controls import (
    ControlVersionConflict, FrontierControlView, FrontierSettings, ReplaceFrontierSettings,
)
from periplus.crawl.control.collections.schemas import CollectionSpec, SelectionContext
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.domain_policies.models import DomainPolicy
from periplus.crawl.control.domain_policies.schemas import DomainPolicySnapshot
from periplus.crawl.control.domain_policies.service import domain_policy_snapshot, find_domain_policy_for_url
from periplus.crawl.runtime.frontier_models import (
    AcquisitionRecord, BackgroundCheckRecord, FrontierControlRecord, FrontierOutboxRecord, InterestRecord,
)
from periplus.crawl.runtime.background_policy import BACKGROUND_RULE, background_rejection
from periplus.crawl.runtime.background_seen import (
    BackgroundAdmission, BackgroundCheck, HistoricalSeenResult, SeenCandidates,
)
from periplus.crawl.runtime.navigation_contract import NavigationPackage
from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.crawl.runtime.frontier_evidence import acquisition_context, terminal_evidence
from periplus.crawl.acquisition.evidence import attempt_records
from periplus.platform.catalogue.records import AttemptUsage, VisitEvidence
from periplus.platform.catalogue.lineage import (
    CollectionDefinition, CollectionOutcome, FulfillmentRecord, AcquisitionReason, LineageEvidence,
)
from periplus.ingestion.queue import (
    IngestionJob, IngestionState, lineage_ingestion_job, visit_ingestion_job,
)
from periplus.urls import normalize_url


def request_url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class AdmissionDeferred(RuntimeError):
    """The caller must preserve its selection checkpoint and resume on capacity."""


class CollectionUnavailable(RuntimeError):
    pass


class StaleDispatch(RuntimeError):
    pass


@dataclass(frozen=True)
class Admission:
    interest_id: UUID
    acquisition_id: UUID
    created: bool
    mode: str


@dataclass(frozen=True)
class Dispatch:
    acquisition_id: UUID
    generation: int
    url: str
    requirements: EffectivePolicySnapshot


@dataclass(frozen=True)
class CollectionWork:
    collection_id: UUID
    token: UUID


@dataclass(frozen=True)
class BackgroundWork:
    acquisition_id: UUID
    token: UUID


@dataclass(frozen=True)
class FrontierDelivery:
    message_id: str
    acquisition_id: UUID | None
    kind: str
    payload: dict
    claim_token: UUID

    def ingestion_job(self) -> IngestionJob:
        if self.kind == "observation":
            return visit_ingestion_job(VisitEvidence.model_validate(self.payload))
        if self.kind == "lineage":
            from periplus.platform.catalogue.lineage import LINEAGE_ADAPTER
            return lineage_ingestion_job(LINEAGE_ADAPTER.validate_python(self.payload))
        raise ValueError("capture delivery has no ingestion receipt")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def capture_identity(url: str, policy: EffectivePolicySnapshot, *, visibility: str,
                     access_context: str, private_collection: UUID | None = None) -> str:
    if visibility not in {"public", "private"} or (visibility == "private" and private_collection is None):
        raise ValueError("capture identity requires explicit visibility and private isolation")
    # Website pacing is checked at execution; it does not alter captured content.
    payload = {
        "url": normalize_url(url), "content": policy.content.model_dump(mode="json"),
        "visibility": visibility, "access": access_context,
        # Private collections never share, even when a caller reuses a context label.
        "private_collection": str(private_collection) if visibility == "private" else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _current_domain_column(column):
    """Correlated current policy lookup filters ineligible domains before LIMIT."""
    pattern = DomainPolicy.host_match
    suffix = func.substr(pattern, 2)
    wildcard = func.substr(pattern, 1, 2) == "*."
    matches = or_(pattern == "*", pattern == AcquisitionRecord.domain,
        wildcard & (func.length(AcquisitionRecord.domain) > func.length(suffix)) &
        (func.substr(AcquisitionRecord.domain,
                     func.length(AcquisitionRecord.domain) - func.length(suffix) + 1) == suffix))
    specificity = case((pattern == "*", 0), (wildcard, 1), else_=2)
    return select(column).where(DomainPolicy.enabled.is_(True), matches).order_by(
        specificity.desc(), func.length(pattern).desc(), DomainPolicy.id,
    ).limit(1).correlate(AcquisitionRecord).scalar_subquery()


class FrontierStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _control(session: Session) -> FrontierControlRecord:
        control = session.scalar(select(FrontierControlRecord).where(
            FrontierControlRecord.id == 1).with_for_update())
        if control is None:
            raise RuntimeError("frontier control is not installed; run setup")
        return control

    @staticmethod
    def _retained_acquisitions(session: Session) -> int:
        return session.scalar(select(func.count()).select_from(AcquisitionRecord))

    @staticmethod
    def _require_acquisition_capacity(session: Session, control: FrontierControlRecord) -> None:
        if FrontierStore._retained_acquisitions(session) >= control.acquisition_limit:
            raise AdmissionDeferred("retained acquisition capacity")

    def validate_installed(self) -> None:
        """Startup checks the replacement schema; it never creates control state."""
        with self._sessions() as session:
            if session.get(FrontierControlRecord, 1) is None:
                raise RuntimeError("frontier control is not installed; run setup")
            for model in (CollectionRecord, AcquisitionRecord, InterestRecord, FrontierOutboxRecord, BackgroundCheckRecord):
                session.execute(select(model).limit(0))

    @staticmethod
    def _control_view(control: FrontierControlRecord, now: datetime, retained_acquisitions: int) -> FrontierControlView:
        values = {name: getattr(control, name) for name in FrontierSettings.model_fields}
        values["captures_per_minute"] = control.captures_per_minute or None
        return FrontierControlView(
            settings=FrontierSettings.model_validate(values), policy_version=control.policy_version,
            updated_at=control.updated_at, updated_by=control.updated_by,
            retained_acquisitions=retained_acquisitions,
            acquisition_admission_waiting_reason=("retained_acquisition_capacity" if retained_acquisitions >= control.acquisition_limit else None),
            pending_acquisitions=control.pending_count, dispatched_acquisitions=control.active_count,
            retained_interests=control.interest_count,
            reserved_attempts=control.reserved_attempts, started_attempts=control.started_attempts,
            reserved_capture_ms=control.reserved_capture_ms, charged_capture_ms=control.charged_capture_ms,
            background_reserved_attempts=control.background_reserved_attempts,
            background_started_attempts=control.background_started_attempts,
            background_reserved_capture_ms=control.background_reserved_capture_ms,
            background_charged_capture_ms=control.background_charged_capture_ms,
            background_waiting_reason=FrontierStore._background_waiting_reason(control),
            dispatch_waiting_reason=(
                FrontierStore._attempt_waiting_reason(control)
                or ("dispatch_capacity" if control.active_count >= control.dispatch_limit else None)
                or ("dispatch_rate" if control.next_dispatch_at and _aware(control.next_dispatch_at) > now else None)
            ),
            next_rate_eligibility_at=control.next_dispatch_at, as_of=now,
        )

    def control_view(self) -> FrontierControlView:
        with self._sessions() as session, session.begin():
            control = session.scalar(select(FrontierControlRecord).where(
                FrontierControlRecord.id == 1).with_for_update(read=True))
            if control is None:
                raise RuntimeError("frontier control is not initialized")
            return self._control_view(control, datetime.now(UTC), self._retained_acquisitions(session))

    def replace_controls(self, change: ReplaceFrontierSettings, *, actor: str,
                         now: datetime | None = None) -> FrontierControlView:
        if not actor or len(actor) > 200:
            raise ValueError("control actor must contain 1 to 200 characters")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            if change.expected_version != control.policy_version:
                raise ControlVersionConflict("Frontier settings changed; reload before updating.")
            current = self._control_view(control, now, self._retained_acquisitions(session))
            if current.settings == change.settings:
                return current
            if current.settings.exclusions != change.settings.exclusions:
                control.exclusion_cursor = None
            old_rate = control.captures_per_minute
            if control.background_share != change.settings.background_share:
                control.background_credit = 0
            for name, value in change.settings.model_dump(mode="json").items():
                setattr(control, name, value if value is not None else 0)
            if old_rate != control.captures_per_minute:
                control.next_dispatch_at = (
                    _aware(control.last_dispatch_at) + timedelta(seconds=60 / control.captures_per_minute)
                    if control.last_dispatch_at and control.captures_per_minute else None
                )
            control.policy_version += 1
            control.updated_at, control.updated_by = now, actor
            return self._control_view(control, now, self._retained_acquisitions(session))

    def set_collection_priority(self, collection_id: UUID, priority: int) -> None:
        if not -10 <= priority <= 10:
            raise ValueError("collection priority outside bounds")
        with self._sessions() as session, session.begin():
            self._control(session)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.status == "settled":
                raise CollectionUnavailable("collection is settled")
            if collection.priority != priority:
                collection.admission_timing = None
            collection.priority = priority

    def create_collection(self, identity: UUID, spec: CollectionSpec, *,
                          priority: int = 0) -> CollectionRecord:
        if not -10 <= priority <= 10:
            raise ValueError("priority must be between -10 and 10")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            existing = session.get(CollectionRecord, identity)
            if existing is not None:
                if existing.spec != spec.model_dump(mode="json"):
                    raise ValueError("collection identity reused with different intent")
                return existing
            retained_collections = session.scalar(select(func.count()).select_from(CollectionRecord))
            if retained_collections >= control.collection_limit:
                raise AdmissionDeferred("collection admission capacity")
            submitted_at = self._transaction_now(session, None)
            record = CollectionRecord(id=identity, spec=spec.model_dump(mode="json"),
                                      created_at=submitted_at, service_after=submitted_at, last_progress_at=submitted_at,
                                      page_limit=spec.page_limit, priority=priority,
                                      scheduling_turn=control.scheduling_turn,
                                      admission_timing={"policy_version": control.policy_version,
                                                        "priority": priority, "first_admitted_at": None})
            session.add(record)
            session.flush()
            self._lineage(session, CollectionDefinition(
                record_id=identity, collection_id=identity, visibility=spec.visibility,
                recorded_at=record.created_at, specification=spec.model_dump(mode="json"),
            ), collection_id=identity)
            return record

    def get_collection(self, identity: UUID) -> CollectionRecord | None:
        with self._sessions() as session:
            return session.get(CollectionRecord, identity)

    def get_acquisition(self, identity: UUID) -> AcquisitionRecord | None:
        with self._sessions() as session:
            return session.get(AcquisitionRecord, identity)

    def get_interest(self, identity: UUID) -> InterestRecord | None:
        with self._sessions() as session:
            return session.get(InterestRecord, identity)

    def claim_collection(self, *, now: datetime | None = None) -> CollectionWork | None:
        """A short claim suppresses duplicate selection; checkpoints remain authoritative."""
        with self._sessions() as session, session.begin():
            selected_at = self._transaction_now(session, now)
            collection = session.scalar(select(CollectionRecord).where(
                CollectionRecord.status.in_(("active", "paused")),
                CollectionRecord.service_after <= selected_at,
                or_(CollectionRecord.service_expires_at.is_(None),
                    CollectionRecord.service_expires_at <= selected_at),
            # Priority advances a due request by up to ten seconds; resuming
            # service refreshes its timestamp so older work eventually wins.
            # The due-time predicate above still enforces dependency backoff.
            ).order_by(func.extract("epoch", CollectionRecord.service_after) - CollectionRecord.priority,
                       CollectionRecord.created_at,
                       CollectionRecord.id).limit(1).with_for_update(skip_locked=True))
            if collection is None:
                return None
            granted_at = self._transaction_now(session, now)
            token = uuid4()
            collection.service_token = token
            collection.service_expires_at = granted_at + timedelta(seconds=120)
            return CollectionWork(collection.id, token)

    def checkpoint_discovery(self, work: CollectionWork, expected_revision: int,
                             state: DiscoveryState, *, now: datetime | None = None) -> bool:
        if state.revision != expected_revision + 1 or len(state.model_dump_json().encode()) > 256 * 1024:
            raise ValueError("invalid or oversized discovery checkpoint")
        with self._sessions() as session, session.begin():
            self._control(session)
            now = self._transaction_now(session, now)
            collection = session.get(CollectionRecord, work.collection_id)
            if (collection is None or collection.status != "active" or collection.service_token != work.token
                    or collection.service_expires_at is None or _aware(collection.service_expires_at) <= now):
                return False
            spec = CollectionSpec.model_validate(collection.spec)
            if spec.deadline_at is not None and spec.deadline_at <= now:
                return False
            current = DiscoveryState.model_validate(collection.discovery_state or {})
            if current.revision != expected_revision or current.complete:
                return False
            collection.discovery_state = state.model_dump(mode="json")
            collection.last_progress_at = now
            collection.waiting_reason = "resolving_sources" if not state.complete else None
            return True

    def release_collection(self, work: CollectionWork, *, delay_seconds: float = 0,
                           error: str | None = None, now: datetime | None = None) -> bool:
        if not 0 <= delay_seconds <= 60:
            raise ValueError("collection resumption delay outside bounds")
        with self._sessions() as session, session.begin():
            collection = session.scalar(select(CollectionRecord).where(
                CollectionRecord.id == work.collection_id).with_for_update())
            now = self._transaction_now(session, now)
            if (collection is None or collection.service_token != work.token
                    or collection.service_expires_at is None
                    or _aware(collection.service_expires_at) <= now):
                return False
            collection.service_token = None
            collection.service_expires_at = None
            collection.service_failures = min(10, collection.service_failures + 1) if error else 0
            if error:
                delay_seconds = max(delay_seconds, min(60, 2 ** collection.service_failures))
                collection.waiting_reason = error
            collection.service_after = now + timedelta(seconds=delay_seconds)
            return True

    def next_selection(self, collection_id: UUID) -> UUID | None:
        with self._sessions() as session:
            return session.scalar(select(InterestRecord.id).where(
                InterestRecord.collection_id == collection_id,
                InterestRecord.status == "selecting",
            ).order_by(InterestRecord.created_at, InterestRecord.id).limit(1))

    def expired_dispatches(self, *, now: datetime | None = None, limit: int = 64) -> tuple[UUID, ...]:
        if not 1 <= limit <= 64:
            raise ValueError("recovery batch outside bounds")
        with self._sessions() as session:
            now = self._transaction_now(session, now)
            return tuple(session.scalars(select(AcquisitionRecord.id).where(
                AcquisitionRecord.status == "dispatched",
                AcquisitionRecord.claim_expires_at <= now,
            ).order_by(AcquisitionRecord.claim_expires_at, AcquisitionRecord.id).limit(limit)))

    def claim_outbox(self, *, batch: int = 100, lease_seconds: int = 60,
                     now: datetime | None = None) -> list[FrontierDelivery]:
        if not 1 <= batch <= 1000 or not 1 <= lease_seconds <= 3600:
            raise ValueError("outbox batch or lease outside bounds")
        with self._sessions() as session, session.begin():
            selected_at = self._transaction_now(session, now)
            # Independent publishers claim different rows; no crawler capacity lock
            # participates in network delivery or blocks acquisition admission.
            rows = list(session.scalars(select(FrontierOutboxRecord).where(
                FrontierOutboxRecord.published_at.is_(None),
                or_(FrontierOutboxRecord.not_before.is_(None),
                    FrontierOutboxRecord.not_before <= selected_at),
                or_(FrontierOutboxRecord.claim_expires_at.is_(None),
                    FrontierOutboxRecord.claim_expires_at <= selected_at),
            ).order_by(FrontierOutboxRecord.created_at, FrontierOutboxRecord.message_id)
              .limit(batch).with_for_update(skip_locked=True)))
            granted_at = self._transaction_now(session, now)
            deliveries = []
            for row in rows:
                row.claim_token = uuid4()
                row.claim_expires_at = granted_at + timedelta(seconds=lease_seconds)
                row.publish_attempts += 1
                deliveries.append(FrontierDelivery(row.message_id, row.acquisition_id,
                                                   row.kind, row.payload, row.claim_token))
            return deliveries

    def mark_outbox_published(self, delivery: FrontierDelivery, *,
                              now: datetime | None = None) -> bool:
        with self._sessions() as session, session.begin():
            row = session.get(FrontierOutboxRecord, delivery.message_id, with_for_update=True)
            now = self._transaction_now(session, now)
            if (row is None or row.claim_token != delivery.claim_token
                    or row.claim_expires_at is None or _aware(row.claim_expires_at) <= now
                    or row.published_at is not None):
                return False
            row.published_at = row.next_receipt_at = now
            row.last_error = None
            row.claim_token = row.claim_expires_at = None
            return True

    def release_outbox(self, delivery: FrontierDelivery, error: str, *,
                       now: datetime | None = None, delay_seconds: int = 5) -> bool:
        if not 1 <= delay_seconds <= 3600:
            raise ValueError("outbox retry delay outside bounds")
        with self._sessions() as session, session.begin():
            row = session.get(FrontierOutboxRecord, delivery.message_id, with_for_update=True)
            now = self._transaction_now(session, now)
            if (row is None or row.claim_token != delivery.claim_token
                    or row.claim_expires_at is None or _aware(row.claim_expires_at) <= now
                    or row.published_at is not None):
                return False
            row.not_before = now + timedelta(seconds=delay_seconds)
            row.last_error = error[:1000]
            row.claim_token = row.claim_expires_at = None
            return True

    def claim_ingestion_receipts(self, *, batch: int = 8,
                                 now: datetime | None = None) -> list[FrontierDelivery]:
        if not 1 <= batch <= 8:
            raise ValueError("receipt batch must be between one and eight")
        with self._sessions() as session, session.begin():
            selected_at = self._transaction_now(session, now)
            rows = list(session.scalars(select(FrontierOutboxRecord).where(
                FrontierOutboxRecord.kind.in_(("observation", "lineage")),
                FrontierOutboxRecord.published_at.is_not(None),
                FrontierOutboxRecord.committed_snapshot.is_(None),
                or_(FrontierOutboxRecord.next_receipt_at.is_(None),
                    FrontierOutboxRecord.next_receipt_at <= selected_at),
                or_(FrontierOutboxRecord.claim_expires_at.is_(None),
                    FrontierOutboxRecord.claim_expires_at <= selected_at),
            ).order_by(FrontierOutboxRecord.next_receipt_at.asc().nullsfirst(),
                       FrontierOutboxRecord.created_at, FrontierOutboxRecord.message_id)
              .limit(batch).with_for_update(skip_locked=True)))
            granted_at = self._transaction_now(session, now)
            deliveries = []
            for row in rows:
                row.claim_token = uuid4()
                row.claim_expires_at = granted_at + timedelta(seconds=60)
                row.receipt_checks += 1
                deliveries.append(FrontierDelivery(row.message_id, row.acquisition_id,
                                                   row.kind, row.payload, row.claim_token))
            return deliveries

    def record_ingestion_receipt(self, delivery: FrontierDelivery, state: IngestionState, *,
                                 now: datetime | None = None) -> bool:
        if state.status != "succeeded" or state.result is None:
            raise ValueError("ingestion receipt requires a successful durable result")
        expected = delivery.ingestion_job()
        if state.job.model_copy(update={"enqueued_at": expected.enqueued_at}) != expected:
            raise ValueError("ingestion receipt has different immutable evidence")
        with self._sessions() as session, session.begin():
            self._control(session)
            row = session.get(FrontierOutboxRecord, delivery.message_id, with_for_update=True)
            now = self._transaction_now(session, now)
            if (row is None or row.claim_token != delivery.claim_token
                    or row.claim_expires_at is None or _aware(row.claim_expires_at) <= now
                    or row.published_at is None or row.committed_snapshot is not None):
                return False
            if (row.kind != delivery.kind or row.payload != delivery.payload
                    or row.acquisition_id != delivery.acquisition_id):
                raise ValueError("ingestion receipt does not match the claimed outbox record")
            row.committed_snapshot = state.result.repository_snapshot
            row.committed_at = now
            row.claim_token = row.claim_expires_at = None
            row.last_error = None
            if row.kind == "observation":
                acquisition = session.get(AcquisitionRecord, row.acquisition_id)
                if acquisition is None:
                    raise ValueError("observation receipt has no acquisition")
                acquisition.evidence_snapshot = state.result.repository_snapshot
            return True

    def defer_ingestion_receipt(self, delivery: FrontierDelivery, reason: str, *,
                                now: datetime | None = None) -> bool:
        with self._sessions() as session, session.begin():
            row = session.get(FrontierOutboxRecord, delivery.message_id, with_for_update=True)
            now = self._transaction_now(session, now)
            if (row is None or row.claim_token != delivery.claim_token
                    or row.claim_expires_at is None or _aware(row.claim_expires_at) <= now
                    or row.published_at is None or row.committed_snapshot is not None):
                return False
            row.next_receipt_at = now + timedelta(seconds=min(300, 5 * 2 ** min(row.receipt_checks, 6)))
            row.last_error = reason[:1000]
            row.claim_token = row.claim_expires_at = None
            return True

    @staticmethod
    def _transaction_now(session: Session, override: datetime | None = None) -> datetime:
        if override is not None:
            return override
        # Read after acquiring the control lock. Transaction-start time could
        # predate a lock wait and wrongly keep an expired checkpoint eligible.
        if session.get_bind().dialect.name == "postgresql":
            return _aware(session.scalar(select(func.clock_timestamp())))
        return datetime.now(UTC)

    def claim_background(self) -> BackgroundWork | None:
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session)
            if self._background_waiting_reason(control) or control.pending_count >= control.admission_limit:
                return None
            claimed = session.scalar(select(func.count()).select_from(AcquisitionRecord).where(
                AcquisitionRecord.background_expires_at > now,
            ))
            if claimed >= 128:
                return None
            parent = session.scalar(select(AcquisitionRecord).where(
                AcquisitionRecord.visibility == "public", AcquisitionRecord.status == "succeeded",
                AcquisitionRecord.navigation["object_name"].as_string().is_not(None), AcquisitionRecord.background_selected.is_(False),
                AcquisitionRecord.background_after <= now,
                or_(AcquisitionRecord.background_expires_at.is_(None), AcquisitionRecord.background_expires_at <= now),
            ).order_by(AcquisitionRecord.background_after, AcquisitionRecord.created_at, AcquisitionRecord.id).limit(1))
            if parent is None:
                return None
            parent.background_token = uuid4()
            parent.background_expires_at = now + timedelta(seconds=120)
            return BackgroundWork(parent.id, parent.background_token)

    def resume_background_check(self, work: BackgroundWork) -> BackgroundCheck | None:
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session)
            parent = session.get(AcquisitionRecord, work.acquisition_id)
            if (parent is None or parent.background_token != work.token
                    or parent.background_expires_at is None or _aware(parent.background_expires_at) <= now):
                raise StaleDispatch("background service claim expired")
            row = session.get(BackgroundCheckRecord, work.acquisition_id)
            if row is None:
                return None
            if _aware(row.expires_at) <= now or row.policy_version != control.policy_version:
                session.delete(row)
                return None
            return BackgroundCheck(parent_observation_id=parent.id, token=row.token,
                candidates=SeenCandidates(urls=tuple(row.candidates)), policy_version=row.policy_version,
                expires_at=_aware(row.expires_at),
                result=HistoricalSeenResult.model_validate(row.result) if row.result else None)

    def release_background(self, work: BackgroundWork, *, complete: bool = False,
                           error: str | None = None, delay_seconds: int = 15) -> bool:
        if not 0 <= delay_seconds <= 300:
            raise ValueError("background service delay outside bounds")
        with self._sessions() as session, session.begin():
            self._control(session)
            now = self._transaction_now(session)
            parent = session.get(AcquisitionRecord, work.acquisition_id)
            if (parent is None or parent.background_token != work.token
                    or parent.background_expires_at is None or _aware(parent.background_expires_at) <= now):
                return False
            if complete:
                parent.background_selected = True
            parent.background_failures = min(parent.background_failures + 1, 10) if error else 0
            delay = min(300, 15 * 2 ** parent.background_failures) if error else delay_seconds
            parent.background_after = now + timedelta(seconds=delay)
            parent.background_error = error[:1000] if error else None
            parent.background_token = parent.background_expires_at = None
            return True

    def start_background_check(self, parent_observation_id: UUID, candidates: SeenCandidates, *,
                               now: datetime | None = None) -> BackgroundCheck:
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            if control.background_share == 0 or control.paused:
                raise AdmissionDeferred("background exploration is paused")
            parent = session.get(AcquisitionRecord, parent_observation_id)
            if (parent is None or parent.visibility != "public" or parent.status != "succeeded"
                    or parent.navigation is None or parent.background_selected):
                raise ValueError("background selection requires retained public navigation")
            # The entire check table is capped at 128 batches; removing expired
            # checks cannot allow their fenced results to admit later.
            session.execute(delete(BackgroundCheckRecord).where(BackgroundCheckRecord.expires_at <= now))
            if session.get(BackgroundCheckRecord, parent_observation_id) is not None:
                raise AdmissionDeferred("background parent already has an active check")
            count = session.scalar(select(func.count()).select_from(BackgroundCheckRecord))
            if count >= 128:
                raise AdmissionDeferred("background historical checks are at capacity")
            check = BackgroundCheck(parent_observation_id=parent_observation_id, token=uuid4(),
                                    candidates=candidates, policy_version=control.policy_version,
                                    expires_at=now + timedelta(seconds=120))
            session.add(BackgroundCheckRecord(
                parent_observation_id=parent_observation_id, token=check.token,
                candidates=list(candidates.urls), policy_version=check.policy_version,
                expires_at=check.expires_at,
            ))
            return check

    def finish_background_check(self, check: BackgroundCheck, result: HistoricalSeenResult, *,
                                now: datetime | None = None) -> BackgroundCheck:
        if result.candidates != check.candidates:
            raise ValueError("historical check changed its frozen candidates")
        with self._sessions() as session, session.begin():
            self._control(session)
            now = self._transaction_now(session, now)
            row = session.get(BackgroundCheckRecord, check.parent_observation_id)
            if row is None or row.token != check.token or _aware(row.expires_at) <= now:
                raise StaleDispatch("historical check expired; repeat the lookup")
            if (row.candidates != list(check.candidates.urls) or row.policy_version != check.policy_version
                    or _aware(row.expires_at) != check.expires_at):
                raise ValueError("historical check identity changed")
            frozen = result.model_dump(mode="json")
            if row.result is not None and row.result != frozen:
                raise ValueError("historical check already has a different result")
            row.result, row.snapshot = frozen, result.snapshot
            return check.model_copy(update={"result": result})

    def admit_background(self, check: BackgroundCheck, url: str, policy: EffectivePolicySnapshot, *,
                         now: datetime | None = None) -> BackgroundAdmission:
        url = normalize_url(url)
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            if control.paused or control.background_share == 0:
                raise AdmissionDeferred("background exploration is paused")
            row = session.get(BackgroundCheckRecord, check.parent_observation_id)
            if (row is None or row.token != check.token or _aware(row.expires_at) <= now
                    or row.policy_version != control.policy_version):
                raise StaleDispatch("historical check expired or policy changed; repeat the lookup")
            if row.result is None or row.snapshot is None:
                raise AdmissionDeferred("historical lookup has not completed")
            if url not in row.candidates:
                raise ValueError("URL was not in the checked candidate batch")
            result = HistoricalSeenResult.model_validate(row.result)
            if url in row.decisions:
                return BackgroundAdmission.model_validate(row.decisions[url])
            rejection = "global_exclusion" if is_excluded(url, control.exclusions) else background_rejection(url)
            decision = None
            if rejection:
                decision = BackgroundAdmission(url=url, status="declined", reason=rejection)
            elif url in result.seen_urls:
                decision = BackgroundAdmission(url=url, status="seen", reason="lake_observation")
            else:
                existing = session.scalar(select(AcquisitionRecord).where(
                    AcquisitionRecord.visibility == "public",
                    or_(
                        (AcquisitionRecord.url == url) & or_(
                            AcquisitionRecord.status.in_(("queued", "retry", "dispatched")),
                            AcquisitionRecord.outcome.is_not(None),
                        ),
                        (AcquisitionRecord.status == "succeeded")
                        & (AcquisitionRecord.outcome["visit"]["effective_url"].as_string() == url),
                    ),
                ).limit(1))
                if existing is not None:
                    terminal = existing.outcome is not None and existing.status in ("succeeded", "failed", "cancelled")
                    decision = BackgroundAdmission(url=url, status="seen" if terminal else "already_pending",
                                                   reason="operational_observation" if terminal else "current_work")
            if decision is None:
                if control.pending_count >= control.admission_limit:
                    raise AdmissionDeferred("frontier admission capacity")
                domain = urlsplit(url).hostname or ""
                pending_background = session.scalar(select(func.count()).select_from(AcquisitionRecord).where(
                    AcquisitionRecord.background_reason["rule_id"].as_string().is_not(None),
                    AcquisitionRecord.status.in_(("queued", "retry", "dispatched")),
                ))
                domain_pending = session.scalar(select(func.count()).select_from(AcquisitionRecord).where(
                    AcquisitionRecord.visibility == "public", AcquisitionRecord.domain == domain,
                    AcquisitionRecord.status.in_(("queued", "retry", "dispatched")),
                ))
                if pending_background >= 1000 or domain_pending >= 16:
                    raise AdmissionDeferred("background admission or domain capacity")
                self._require_acquisition_capacity(session, control)
                key = capture_identity(url, policy, visibility="public", access_context="public")
                acquisition = AcquisitionRecord(
                    url=url, domain=domain, capture_key=key, pending_key=key, background_url_key=url,
                    requirements=policy.model_dump(mode="json"), visibility="public", access_context="public",
                    created_at=now, eligible_at=now,
                    background_reason={
                        "parent_observation_id": str(check.parent_observation_id), "rule_id": BACKGROUND_RULE,
                        "selection_provenance": {"policy_version": row.policy_version,
                            "source_snapshot": result.snapshot, "source_query_id": result.query_id},
                    },
                )
                session.add(acquisition)
                session.flush()
                control.pending_count += 1
                decision = BackgroundAdmission(url=url, status="admitted", acquisition_id=acquisition.id)
            row.decisions = row.decisions | {url: decision.model_dump(mode="json")}
            if len(row.decisions) == len(row.candidates):
                parent = session.get(AcquisitionRecord, row.parent_observation_id)
                parent.background_selected = True
            return decision

    def cleanup_collections(self, *, cutoff: datetime) -> int:
        """Hand settled requests to immutable history, then reclaim at most 512 dependent rows."""
        if cutoff.utcoffset() is None:
            raise ValueError("collection cleanup cutoff requires a timezone")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session)
            statement = select(CollectionRecord).where(
                CollectionRecord.status == "settled", CollectionRecord.completed_at <= cutoff,
            )
            if control.collection_retention_cursor is not None:
                statement = statement.where(CollectionRecord.id > control.collection_retention_cursor)
            records = list(session.scalars(statement.order_by(CollectionRecord.id).limit(64)))
            removed, remaining, last = 0, 512, None
            for collection in records:
                last = collection.id
                if not collection.retiring:
                    if collection.service_expires_at is not None and _aware(collection.service_expires_at) > now:
                        continue
                    required = (f"lineage:collection:{collection.id}", f"lineage:collection_outcome:{collection.id}")
                    committed = session.scalar(select(func.count()).select_from(FrontierOutboxRecord).where(
                        FrontierOutboxRecord.message_id.in_(required), FrontierOutboxRecord.committed_snapshot.is_not(None),
                    ))
                    if committed != 2:
                        continue
                    outstanding = session.scalar(select(FrontierOutboxRecord.message_id).where(
                        FrontierOutboxRecord.collection_id == collection.id,
                        FrontierOutboxRecord.kind.in_(("observation", "lineage")),
                        FrontierOutboxRecord.committed_snapshot.is_(None),
                    ).limit(1))
                    if outstanding is not None:
                        continue
                    blocked = session.scalar(select(InterestRecord.id).join(AcquisitionRecord,
                        AcquisitionRecord.id == InterestRecord.acquisition_id).where(
                        InterestRecord.collection_id == collection.id,
                        or_(InterestRecord.status.not_in(("settled", "cancelled")),
                            AcquisitionRecord.outcome["visit"]["visit_id"].as_string().is_not(None)
                            & AcquisitionRecord.evidence_snapshot.is_(None)),
                    ).limit(1))
                    if blocked is not None:
                        continue
                    # Detail reads now use immutable history, so partial pruning
                    # cannot make counts shrink. Identity/spec remain until finish.
                    collection.retiring = True
                identities = list(session.scalars(select(InterestRecord.id).where(
                    InterestRecord.collection_id == collection.id).order_by(InterestRecord.id).limit(remaining)))
                if identities:
                    session.execute(delete(InterestRecord).where(InterestRecord.id.in_(identities)))
                    control.interest_count -= len(identities)
                    remaining -= len(identities)
                if session.scalar(select(InterestRecord.id).where(InterestRecord.collection_id == collection.id).limit(1)) is None:
                    messages = list(session.scalars(select(FrontierOutboxRecord.message_id).where(
                        FrontierOutboxRecord.collection_id == collection.id).order_by(FrontierOutboxRecord.message_id).limit(remaining)))
                    if messages:
                        session.execute(delete(FrontierOutboxRecord).where(FrontierOutboxRecord.message_id.in_(messages)))
                        remaining -= len(messages)
                    if session.scalar(select(FrontierOutboxRecord.message_id).where(FrontierOutboxRecord.collection_id == collection.id).limit(1)) is None:
                        session.delete(collection)
                        removed += 1
                if remaining == 0:
                    break
            control.collection_retention_cursor = last if len(records) == 64 or remaining == 0 else None
            return removed

    def cleanup_acquisitions(self, *, cutoff: datetime) -> int:
        """Reclaim a bounded batch only after all current ownership and durable evidence gates."""
        if cutoff.utcoffset() is None:
            raise ValueError("acquisition cleanup cutoff requires a timezone")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session)
            referenced = select(InterestRecord.id).where(InterestRecord.acquisition_id == AcquisitionRecord.id).exists()
            statement = select(AcquisitionRecord).where(
                AcquisitionRecord.status.in_(("succeeded", "failed", "cancelled")),
                AcquisitionRecord.completed_at <= cutoff, ~referenced,
                AcquisitionRecord.navigation["object_name"].as_string().is_(None),
            )
            if control.retention_cursor is not None:
                statement = statement.where(AcquisitionRecord.id > control.retention_cursor)
            records = list(session.scalars(statement.order_by(AcquisitionRecord.id).limit(64)))
            removed = 0
            for acquisition in records:
                if (acquisition.background_expires_at is not None
                        and _aware(acquisition.background_expires_at) > now):
                    continue
                check = session.get(BackgroundCheckRecord, acquisition.id)
                if check is not None and _aware(check.expires_at) > now:
                    continue
                if acquisition.outcome is None:
                    # Only cancelled, never-started work has no terminal observation.
                    if acquisition.attempt_count or acquisition.status != "cancelled":
                        continue
                else:
                    if acquisition.evidence_snapshot is None:
                        continue
                    if acquisition.visibility == "public" and not self._historical_checks_allow_cleanup(
                            session, acquisition.evidence_snapshot, now):
                        continue
                uncommitted = session.scalar(select(FrontierOutboxRecord.message_id).where(
                    FrontierOutboxRecord.acquisition_id == acquisition.id,
                    FrontierOutboxRecord.kind.in_(("observation", "lineage")),
                    FrontierOutboxRecord.committed_snapshot.is_(None),
                ).limit(1))
                if uncommitted is not None:
                    continue
                session.execute(delete(FrontierOutboxRecord).where(FrontierOutboxRecord.acquisition_id == acquisition.id))
                if check is not None:
                    session.delete(check)
                    session.flush()
                session.delete(acquisition)
                removed += 1
            control.retention_cursor = records[-1].id if len(records) == 64 else None
            return removed

    def retire_navigation(self, acquisition_id: UUID, key: str, *, modified_at: datetime,
                          cutoff: datetime, orphan_cutoff: datetime) -> bool:
        """Revoke a reusable reference before remote deletion, without holding a remote lock."""
        prefix = f"runtime/navigation/{acquisition_id.hex}/"
        suffix = key.removeprefix(prefix)
        if (not key.startswith(prefix) or len(suffix) != 70 or not suffix.endswith(".arrow")
                or any(character not in "0123456789abcdef" for character in suffix[:-6])):
            return False
        if any(value.utcoffset() is None for value in (modified_at, cutoff, orphan_cutoff)):
            raise ValueError("navigation retention requires timezone-aware times")
        if modified_at > cutoff:
            return False
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if acquisition is None:
                return modified_at <= orphan_cutoff
            if (acquisition.status not in ("succeeded", "failed", "cancelled")
                    or acquisition.completed_at is None or _aware(acquisition.completed_at) > cutoff):
                return False
            if acquisition.outcome is not None and acquisition.evidence_snapshot is None:
                return False
            uncommitted = session.scalar(select(FrontierOutboxRecord.message_id).where(
                FrontierOutboxRecord.acquisition_id == acquisition.id,
                FrontierOutboxRecord.kind.in_(("observation", "lineage")),
                FrontierOutboxRecord.committed_snapshot.is_(None),
            ).limit(1))
            if uncommitted is not None:
                return False
            navigation = acquisition.navigation
            if navigation is None or navigation["object_name"] != key:
                # No accepted reference can ever attach an orphan to this terminal
                # acquisition. A failed delete is retried on the next object scan.
                return True
            pinned = session.scalar(select(InterestRecord.id).where(
                InterestRecord.acquisition_id == acquisition.id,
                InterestRecord.status.not_in(("settled", "cancelled")),
            ).limit(1))
            check = session.get(BackgroundCheckRecord, acquisition.id)
            if (pinned is not None
                    or (control.background_share > 0 and not acquisition.background_selected)
                    or (acquisition.background_expires_at is not None and _aware(acquisition.background_expires_at) > now)
                    or (check is not None and _aware(check.expires_at) > now)):
                return False
            acquisition.retired_navigation = navigation
            acquisition.navigation = None
            return True

    @staticmethod
    def _historical_checks_allow_cleanup(session: Session, evidence_snapshot: int,
                                        now: datetime | None = None) -> bool:
        """Use only inside the cleanup transaction holding the control row."""
        now = FrontierStore._transaction_now(session, now)
        blocking = session.scalar(select(BackgroundCheckRecord.parent_observation_id).where(
            BackgroundCheckRecord.expires_at > now,
            or_(BackgroundCheckRecord.snapshot.is_(None), BackgroundCheckRecord.snapshot < evidence_snapshot),
        ).limit(1))
        return blocking is None

    def admit(self, collection_id: UUID, url: str, context: SelectionContext,
              policy: EffectivePolicySnapshot, *, now: datetime | None = None) -> Admission:
        url = normalize_url(url)
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            existing = session.scalar(select(InterestRecord).where(
                InterestRecord.collection_id == collection_id, InterestRecord.url_key == request_url_key(url)))
            if existing is not None:
                if existing.url != url:
                    raise ValueError("request URL digest collision")
                return Admission(existing.id, existing.acquisition_id, False, existing.mode)
            spec = CollectionSpec.model_validate(collection.spec)
            if collection.status != "active" or (
                spec.deadline_at is not None and spec.deadline_at <= now
            ):
                raise CollectionUnavailable("collection paused, settled, or expired")
            if context.depth > spec.max_depth:
                raise ValueError("selection exceeds collection depth")
            if spec.allowed_sections:
                from periplus.crawl.control.collections.scopes import within_allowed_sections
                if not within_allowed_sections(url, list(spec.allowed_sections)):
                    raise ValueError("URL outside collection sections")
            if collection.reserved + collection.consumed >= collection.page_limit:
                raise CollectionUnavailable("page budget reached")
            if control.interest_count >= control.interest_limit:
                raise AdmissionDeferred("retained interest capacity")
            if is_excluded(url, control.exclusions):
                raise UrlExcluded("URL excluded by current crawler policy")
            key = capture_identity(url, policy, visibility=spec.visibility,
                                   access_context=spec.access_context, private_collection=collection_id)
            acquisition = None
            if spec.result_max_age_seconds > 0:
                statement = select(AcquisitionRecord).where(
                    AcquisitionRecord.capture_key == key,
                    AcquisitionRecord.status == "succeeded",
                    AcquisitionRecord.completed_at >= now - timedelta(seconds=spec.result_max_age_seconds),
                    AcquisitionRecord.completed_at <= now,
                    AcquisitionRecord.outcome.is_not(None),
                )
                if context.depth < spec.max_depth:
                    statement = statement.where(AcquisitionRecord.navigation["object_name"].as_string().is_not(None))
                acquisition = session.scalar(statement.order_by(
                    AcquisitionRecord.completed_at.desc()).limit(1))
            if acquisition is not None:
                effective_url = (acquisition.outcome or {}).get("visit", {}).get("effective_url")
                if effective_url and is_excluded(effective_url, control.exclusions):
                    # A retained redirect target cannot be reused. The requested
                    # URL itself is still eligible for a fresh, guarded capture.
                    acquisition = None
            reused = acquisition is not None
            if acquisition is None:
                acquisition = session.scalar(select(AcquisitionRecord).where(
                    AcquisitionRecord.pending_key == key))
            mode = "reused" if reused else "shared"
            if acquisition is None:
                if control.pending_count >= control.admission_limit:
                    raise AdmissionDeferred("frontier admission capacity")
                self._require_acquisition_capacity(session, control)
                acquisition = AcquisitionRecord(
                    url=url, domain=urlsplit(url).hostname or "", capture_key=key,
                    pending_key=key, requirements=policy.model_dump(mode="json"),
                    visibility=spec.visibility, access_context=spec.access_context,
                    eligible_at=now, created_at=now,
                )
                session.add(acquisition)
                session.flush()
                control.pending_count += 1
                mode = "acquired"
            interest = InterestRecord(
                collection_id=collection_id, acquisition_id=acquisition.id, url=url, url_key=request_url_key(url),
                context=context.model_dump(mode="json"), mode=mode,
                budget_state="consumed" if reused else "reserved",
                status="selecting" if reused else "queued", created_at=now,
            )
            session.add(interest)
            collection.last_progress_at = now
            timing = collection.admission_timing
            if timing is not None and timing["first_admitted_at"] is None:
                collection.admission_timing = (timing | {"first_admitted_at": now.isoformat()}
                    if timing["policy_version"] == control.policy_version else None)
            control.interest_count += 1
            session.flush()
            if reused:
                collection.consumed += 1
                self._fulfillment(session, interest, acquisition, now)
            else:
                collection.reserved += 1
            return Admission(interest.id, acquisition.id, True, mode)

    @staticmethod
    def _attempt_waiting_reason(control: FrontierControlRecord) -> str | None:
        if control.paused:
            return "crawler_paused"
        if control.reserved_attempts + control.started_attempts >= control.attempt_allowance:
            return "attempt_allowance_exhausted"
        if (control.reserved_capture_ms + control.charged_capture_ms
                + control.capture_timeout_ms + 5000 > control.capture_time_allowance_ms):
            return "capture_time_allowance_exhausted"
        return None

    @staticmethod
    def _background_waiting_reason(control: FrontierControlRecord, reserved_ms: int | None = None) -> str | None:
        if control.paused:
            return "crawler_paused"
        if control.background_share == 0:
            return "background_disabled"
        if control.background_reserved_attempts + control.background_started_attempts >= control.background_attempt_allowance:
            return "background_attempt_allowance_exhausted"
        required = reserved_ms if reserved_ms is not None else control.capture_timeout_ms + 5000
        if (control.background_reserved_capture_ms + control.background_charged_capture_ms
                + required > control.background_capture_time_allowance_ms):
            return "background_capture_time_allowance_exhausted"
        return None

    @staticmethod
    def _reserve_background(control: FrontierControlRecord, acquisition: AcquisitionRecord) -> None:
        acquisition.attempt_background = True
        control.background_reserved_attempts += 1
        control.background_reserved_capture_ms += acquisition.attempt_reserved_ms

    @staticmethod
    def _reserve_attempt(control: FrontierControlRecord, acquisition: AcquisitionRecord, *, background: bool) -> None:
        if acquisition.attempt_reserved_ms:
            raise ValueError("dispatch already has an attempt reservation")
        acquisition.attempt_background = False
        acquisition.attempt_reserved_ms = control.capture_timeout_ms + 5000
        control.reserved_attempts += 1
        control.reserved_capture_ms += acquisition.attempt_reserved_ms
        if background:
            FrontierStore._reserve_background(control, acquisition)

    @staticmethod
    def _settle_attempt(control: FrontierControlRecord, acquisition: AcquisitionRecord,
                        usage: AttemptUsage | None) -> None:
        reserved = acquisition.attempt_reserved_ms
        if reserved <= 0:
            raise ValueError("dispatch has no attempt reservation")
        if acquisition.attempt_started_at is None:
            if usage is not None:
                raise ValueError("unstarted dispatch cannot produce attempt usage")
            control.reserved_attempts -= 1
        else:
            if (usage is None or usage.reserved_ms != reserved
                    or usage.policy_version != acquisition.dispatch_policy_version
                    or usage.domain_policy != DomainPolicySnapshot.model_validate(acquisition.attempt_domain_policy)
                    or usage.exclusion_policy_version != acquisition.attempt_exclusion_version):
                raise ValueError("attempt usage differs from its frozen reservation")
            # Never clip observed overrun. It consumes remaining allowance and
            # becomes visible even when the remote provider exceeded its deadline.
            control.charged_capture_ms += usage.charged_ms
        if acquisition.attempt_background:
            control.background_reserved_capture_ms -= reserved
            if acquisition.attempt_started_at is None:
                control.background_reserved_attempts -= 1
            else:
                assert usage is not None
                control.background_charged_capture_ms += usage.charged_ms
        control.reserved_capture_ms -= reserved
        acquisition.attempt_reserved_ms = 0

    def dispatch(self, acquisition_id: UUID, *, now: datetime | None = None,
                 lease_seconds: int = 120) -> Dispatch | None:
        if lease_seconds < 1:
            raise ValueError("lease must be positive")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            return self._dispatch(session, control, acquisition_id, now, lease_seconds)

    def reconcile_exclusions(self) -> int:
        """Bounded wraparound scan also runs when dispatch is paused or out of allowance."""
        with self._sessions() as session, session.begin():
            control = self._control(session)
            if not control.exclusions:
                control.exclusion_cursor = None
                return 0
            now = self._transaction_now(session)
            statement = select(AcquisitionRecord).where(
                AcquisitionRecord.status.in_(("queued", "retry", "dispatched")),
                AcquisitionRecord.attempt_started_at.is_(None),
            )
            if control.exclusion_cursor is not None:
                statement = statement.where(AcquisitionRecord.id > control.exclusion_cursor)
            acquisitions = list(session.scalars(statement.order_by(AcquisitionRecord.id).limit(64)))
            excluded = 0
            for acquisition in acquisitions:
                if is_excluded(acquisition.url, control.exclusions):
                    self._exclude_acquisition(session, control, acquisition, now)
                    excluded += 1
            control.exclusion_cursor = acquisitions[-1].id if len(acquisitions) == 64 else None
            return excluded

    def reject_destination(self, acquisition_id: UUID, generation: int) -> bool:
        """Reject an unstarted dispatch whose destination failed public-address validation."""
        with self._sessions() as session, session.begin():
            control = self._control(session)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.generation != generation or acquisition.attempt_started_at is not None):
                return False
            self._exclude_acquisition(session, control, acquisition, self._transaction_now(session),
                                      reason="non_public_destination")
            return True

    def _exclude_acquisition(self, session: Session, control: FrontierControlRecord,
                             acquisition: AcquisitionRecord, now: datetime, *, reason: str = "global_exclusion") -> None:
        if acquisition.attempt_started_at is not None:
            raise ValueError("cannot exclude an already started attempt")
        if acquisition.status == "dispatched":
            self._settle_attempt(control, acquisition, None)
            control.active_count -= 1
        else:
            control.pending_count -= 1
        for interest in session.scalars(select(InterestRecord).where(
            InterestRecord.acquisition_id == acquisition.id,
            InterestRecord.status.in_(("queued", "awaiting_result")),
        )):
            collection = session.get(CollectionRecord, interest.collection_id)
            self._detach(collection, interest)
        if acquisition.attempt_count:
            evidence = terminal_evidence(acquisition, now, "cancelled")
            acquisition.outcome = evidence.model_dump(mode="json")
            self._outbox(session, acquisition, "observation", acquisition.outcome, f"observation:{acquisition.id}")
        acquisition.generation += 1
        acquisition.status = "cancelled"
        acquisition.terminal_reason = reason
        acquisition.pending_key = acquisition.background_url_key = None
        acquisition.claim_expires_at = None
        acquisition.completed_at = now

    def _dispatch(self, session: Session, control: FrontierControlRecord,
                  acquisition_id: UUID, now: datetime, lease_seconds: int) -> Dispatch | None:
        if self._attempt_waiting_reason(control) or control.active_count >= control.dispatch_limit:
            return None
        if control.next_dispatch_at and _aware(control.next_dispatch_at) > now:
            return None
        acquisition = session.get(AcquisitionRecord, acquisition_id)
        if acquisition is None or acquisition.status not in ("queued", "retry"):
            return None
        if is_excluded(acquisition.url, control.exclusions):
            self._exclude_acquisition(session, control, acquisition, now)
            return None
        if _aware(acquisition.eligible_at) > now:
            return None
        background_only = not self._has_live_interest(session, acquisition.id, now)
        if acquisition.background_reason is not None and background_only and self._background_waiting_reason(control):
            return None
        domain_active = session.scalar(select(func.count()).select_from(AcquisitionRecord).where(
            AcquisitionRecord.domain == acquisition.domain, AcquisitionRecord.status == "dispatched",
        ))
        domain_policy = domain_policy_snapshot(find_domain_policy_for_url(session, url=acquisition.url))
        if domain_policy.paused or domain_active >= domain_policy.maximum_concurrency:
            return None
        if (acquisition.domain_policy_id == domain_policy.id and acquisition.domain_policy_version == domain_policy.version
                and acquisition.domain_eligible_at and _aware(acquisition.domain_eligible_at) > now):
            return None
        if acquisition.status == "retry":
            if not self._has_live_interest(session, acquisition.id, now) and not (
                acquisition.background_reason is not None and control.background_share > 0
            ):
                return None
            if acquisition.attempt_count >= acquisition.attempt_limit:
                return None
            acquisition.status = "dispatched"
            acquisition.generation += 1
            acquisition.claim_expires_at = now + timedelta(seconds=lease_seconds)
            self._reserve_attempt(control, acquisition, background=background_only)
            acquisition.dispatch_policy_version = control.policy_version
            control.last_dispatch_at = now
            control.pending_count -= 1
            control.active_count += 1
            control.scheduling_turn += 1
            for collection in session.scalars(select(CollectionRecord).join(
                InterestRecord, InterestRecord.collection_id == CollectionRecord.id,
            ).where(InterestRecord.acquisition_id == acquisition.id,
                    InterestRecord.status == "awaiting_result")):
                collection.scheduling_turn = control.scheduling_turn
                collection.last_dispatch_at = now
            if control.captures_per_minute > 0:
                control.next_dispatch_at = now + timedelta(seconds=60 / control.captures_per_minute)
            self._outbox(session, acquisition, "capture", {
                "acquisition_id": str(acquisition.id), "generation": acquisition.generation,
            }, f"capture:{acquisition.id}:{acquisition.generation}")
            return Dispatch(acquisition.id, acquisition.generation, acquisition.url,
                            EffectivePolicySnapshot.model_validate(acquisition.requirements))
        interests = list(session.scalars(select(InterestRecord).where(
            InterestRecord.acquisition_id == acquisition_id, InterestRecord.status == "queued")))
        participants = []
        paused = []
        for interest in interests:
            collection = session.get(CollectionRecord, interest.collection_id)
            assert collection is not None
            spec = CollectionSpec.model_validate(collection.spec)
            if collection.status == "settled" or (spec.deadline_at and spec.deadline_at <= now):
                self._detach(collection, interest)
            elif collection.status == "paused":
                paused.append(interest)
            else:
                participants.append((interest, collection))
        background = acquisition.background_reason is not None and control.background_share > 0
        if paused and not participants and not background:
            return None
        if paused:
            if self._retained_acquisitions(session) >= control.acquisition_limit:
                return None
            # Moving the paused interests preserves their reservations without
            # holding up another caller. This replaces one pending slot while
            # the original leaves that pool, so it cannot exceed admission capacity.
            pending_key = acquisition.pending_key
            acquisition.pending_key = None
            session.flush()
            deferred = AcquisitionRecord(
                url=acquisition.url, domain=acquisition.domain,
                capture_key=acquisition.capture_key, pending_key=pending_key,
                requirements=acquisition.requirements, visibility=acquisition.visibility,
                access_context=acquisition.access_context, eligible_at=now, created_at=now,
            )
            session.add(deferred)
            session.flush()
            for interest in paused:
                interest.acquisition_id = deferred.id
            control.pending_count += 1
        if not participants and not background:
            acquisition.background_url_key = None
            acquisition.status = "cancelled"
            acquisition.pending_key = None
            acquisition.completed_at = now
            control.pending_count -= 1
            return None
        reasons = []
        control.scheduling_turn += 1
        for interest, collection in participants:
            collection.scheduling_turn = control.scheduling_turn
            collection.reserved -= 1
            collection.consumed += 1
            collection.last_dispatch_at = now
            collection.last_progress_at = now
            interest.budget_state = "consumed"
            interest.status = "awaiting_result"
            reasons.append({"collection_id": str(collection.id), "interest_id": str(interest.id),
                            "context": interest.context})
        if background:
            reasons.append({"background": acquisition.background_reason})
        for reason in reasons:
            collection_id = UUID(reason["collection_id"]) if "collection_id" in reason else None
            context = reason.get("context", reason.get("background", {}))
            self._lineage(session, AcquisitionReason(
                record_id=uuid5(acquisition.id, str(collection_id) if collection_id else "background"),
                observation_id=acquisition.id, collection_id=collection_id,
                visibility=acquisition.visibility, recorded_at=now,
                parent_observation_id=context.get("parent_observation_id"),
                reason="collection" if collection_id else "background",
                policy_version=str(control.policy_version), rule_id=context.get("rule_id", "background"),
                selection_provenance=context.get("selection_provenance"),
            ), acquisition_id=acquisition.id, collection_id=collection_id)
        acquisition.frozen_reasons = reasons
        acquisition.pending_key = None
        acquisition.status = "dispatched"
        acquisition.generation += 1
        acquisition.claim_expires_at = now + timedelta(seconds=lease_seconds)
        self._reserve_attempt(control, acquisition, background=background_only)
        acquisition.dispatch_policy_version = control.policy_version
        control.last_dispatch_at = now
        control.pending_count -= 1
        control.active_count += 1
        if control.captures_per_minute > 0:
            control.next_dispatch_at = now + timedelta(seconds=60 / control.captures_per_minute)
        self._outbox(session, acquisition, "capture", {
            "acquisition_id": str(acquisition.id), "generation": acquisition.generation,
        }, f"capture:{acquisition.id}:{acquisition.generation}")
        return Dispatch(acquisition.id, acquisition.generation, acquisition.url,
                        EffectivePolicySnapshot.model_validate(acquisition.requirements))

    def dispatch_next(self, *, now: datetime | None = None,
                      lease_seconds: int = 120) -> Dispatch | None:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("dispatch lease outside bounds")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            if self._attempt_waiting_reason(control) or control.active_count >= control.dispatch_limit:
                return None
            if control.next_dispatch_at and _aware(control.next_dispatch_at) > now:
                return None
            # The earliest participant owns scheduling weight. Adding interests to
            # shared work cannot multiply it. New collections enter at the current
            # turn, so a stream of new submissions cannot reset their waiting age.
            rank = func.min(CollectionRecord.scheduling_turn - CollectionRecord.priority)
            active_domains = select(
                AcquisitionRecord.domain.label("domain"),
                func.count().label("active"),
            ).where(AcquisitionRecord.status == "dispatched").group_by(AcquisitionRecord.domain).subquery()
            domain_limit = _current_domain_column(DomainPolicy.maximum_concurrency)
            domain_paused = _current_domain_column(DomainPolicy.paused)
            domain_ready = or_(AcquisitionRecord.domain_eligible_at.is_(None),
                               AcquisitionRecord.domain_eligible_at <= now,
                               AcquisitionRecord.domain_policy_id != _current_domain_column(DomainPolicy.id),
                               AcquisitionRecord.domain_policy_version != _current_domain_column(DomainPolicy.version))
            statement = select(AcquisitionRecord.id).join(
                InterestRecord, InterestRecord.acquisition_id == AcquisitionRecord.id
            ).join(CollectionRecord, CollectionRecord.id == InterestRecord.collection_id).outerjoin(
                active_domains, active_domains.c.domain == AcquisitionRecord.domain
            ).where(
                or_(
                    (AcquisitionRecord.status == "queued") & (InterestRecord.status == "queued"),
                    (AcquisitionRecord.status == "retry") & (InterestRecord.status == "awaiting_result"),
                ), AcquisitionRecord.eligible_at <= now, CollectionRecord.status == "active",
                func.coalesce(active_domains.c.active, 0) < domain_limit, domain_paused.is_(False), domain_ready,
            ).group_by(AcquisitionRecord.id, AcquisitionRecord.created_at).order_by(
                rank, func.min(CollectionRecord.created_at), AcquisitionRecord.created_at, AcquisitionRecord.id,
            ).limit(64)
            # A bounded candidate window handles collections whose deadlines expired
            # since selection. Domain-ineligible work is excluded before the limit.
            requests = list(session.scalars(statement))
            background = []
            if self._background_waiting_reason(control) is None:
                # Public background work has no artificial request. Correlated
                # interest exclusion prevents shared work receiving two weights.
                live_request = select(InterestRecord.id).join(
                    CollectionRecord, CollectionRecord.id == InterestRecord.collection_id,
                ).where(
                    InterestRecord.acquisition_id == AcquisitionRecord.id,
                    InterestRecord.status.in_(("queued", "awaiting_result")),
                    CollectionRecord.status == "active",
                ).exists()
                background = list(session.scalars(select(AcquisitionRecord.id).outerjoin(
                    active_domains, active_domains.c.domain == AcquisitionRecord.domain,
                ).where(
                    AcquisitionRecord.status.in_(("queued", "retry")),
                    AcquisitionRecord.background_reason["rule_id"].as_string().is_not(None),
                    AcquisitionRecord.eligible_at <= now, ~live_request,
                    func.coalesce(active_domains.c.active, 0) < domain_limit, domain_paused.is_(False), domain_ready,
                ).order_by(AcquisitionRecord.eligible_at, AcquisitionRecord.created_at,
                           AcquisitionRecord.id).limit(64)))
            contested = bool(requests and background)
            credit = control.background_credit + control.background_share if contested else 0
            prefer_background = contested and credit >= 100
            lanes = (background, requests) if prefer_background else (requests, background)
            for lane in lanes:
                for identity in lane:
                    work = self._dispatch(session, control, identity, now, lease_seconds)
                    if work is not None:
                        # No unused-capacity debt accumulates while one lane is idle.
                        control.background_credit = credit % 100 if contested else 0
                        return work
            return None

    def defer_unstarted(self, acquisition_id: UUID, generation: int, *,
                        delay_seconds: float, now: datetime | None = None,
                        domain_policy: DomainPolicySnapshot | None = None,
                        reason: str | None = None) -> bool:
        """Release delivery capacity and physical allowance before any CDP start.

        Participants and their consumed page units stay frozen across redelivery.
        NATS owns domain pacing; versioned domain eligibility is only a scheduling hint.
        """
        if not 0 <= delay_seconds <= 86400:
            raise ValueError("domain deferral outside bounds")
        if reason not in (None, "ingestion_delivery_unavailable", "destination_dns_unavailable", "cdp_unavailable", "storage_unavailable"):
            raise ValueError("unknown pre-acquisition dependency reason")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.generation != generation or acquisition.attempt_started_at is not None):
                return False
            self._settle_attempt(control, acquisition, None)
            acquisition.status = "retry"
            acquisition.defer_reason = reason
            acquisition.generation += 1
            acquisition.claim_expires_at = None
            until = now + timedelta(seconds=delay_seconds)
            control.active_count -= 1
            control.pending_count += 1
            if domain_policy is None:
                acquisition.eligible_at = until
            else:
                session.flush()
                session.execute(update(AcquisitionRecord).where(
                    AcquisitionRecord.domain == acquisition.domain,
                    AcquisitionRecord.status.in_(("queued", "retry")),
                ).values(domain_eligible_at=until, domain_policy_id=domain_policy.id,
                         domain_policy_version=domain_policy.version))
            return True

    def set_collection_paused(self, collection_id: UUID, paused: bool) -> None:
        with self._sessions() as session, session.begin():
            self._control(session)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.status == "settled":
                raise CollectionUnavailable("collection is settled")
            collection.status = "paused" if paused else "active"

    def freeze_selection(self, identity: UUID, checkpoint: SelectionCheckpoint, *,
                         seeds: bool) -> SelectionCheckpoint | None:
        if checkpoint.cursor != 0:
            raise ValueError("a new selection must start at cursor zero")
        with self._sessions() as session, session.begin():
            self._control(session)
            record = session.get(CollectionRecord if seeds else InterestRecord, identity)
            if record is None:
                raise KeyError(identity)
            if (seeds and (record.seeds_settled or record.status == "settled")) or (
                not seeds and record.status in ("settled", "cancelled")
            ):
                return None
            if not seeds and record.status != "selecting":
                raise ValueError("cannot select links before acquisition")
            if record.selection_checkpoint is None:
                record.selection_checkpoint = checkpoint.model_dump(mode="json")
                collection = record if seeds else session.get(CollectionRecord, record.collection_id)
                collection.last_progress_at = self._transaction_now(session, None)
            # Concurrent evaluations retain the first committed selection, even if
            # a volatile query produced a different candidate set in a losing worker.
            return SelectionCheckpoint.model_validate(record.selection_checkpoint)

    def advance_selection(self, identity: UUID, cursor: int, *, seeds: bool, excluded: bool = False) -> bool:
        with self._sessions() as session, session.begin():
            control = self._control(session)
            record = session.get(CollectionRecord if seeds else InterestRecord, identity)
            if record is None:
                raise KeyError(identity)
            if record.selection_checkpoint is None:
                return False
            checkpoint = SelectionCheckpoint.model_validate(record.selection_checkpoint)
            if checkpoint.cursor != cursor:
                return False
            if cursor >= len(checkpoint.urls):
                return False
            collection_id = identity if seeds else record.collection_id
            admitted = session.scalar(select(InterestRecord.id).where(
                InterestRecord.collection_id == collection_id,
                InterestRecord.url_key == request_url_key(checkpoint.urls[cursor]),
                InterestRecord.url == checkpoint.urls[cursor],
            ).limit(1))
            if admitted is None:
                if not excluded:
                    raise ValueError("selection cannot advance past unaccounted work")
                if not is_excluded(checkpoint.urls[cursor], control.exclusions):
                    # Policy changed since admission declined this URL. Keep the
                    # checkpoint here so the next pass evaluates it under new rules.
                    return False
            record.selection_checkpoint = checkpoint.model_copy(update={"cursor": cursor + 1}).model_dump(mode="json")
            collection = record if seeds else session.get(CollectionRecord, record.collection_id)
            collection.last_progress_at = self._transaction_now(session, None)
            return True

    def set_waiting_reason(self, collection_id: UUID, reason: str | None) -> None:
        with self._sessions() as session, session.begin():
            self._control(session)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.status != "settled":
                collection.waiting_reason = reason

    def finish_seed_selection(self, collection_id: UUID) -> None:
        """Called only after all frozen seeds are accounted for or a hard limit stops selection."""
        with self._sessions() as session, session.begin():
            self._control(session)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.seeds_settled:
                return
            self._assert_selection_accounted(collection, collection.selection_checkpoint)
            collection.seed_provenance = self._seed_provenance(collection)
            collection.seeds_settled = True
            collection.selection_checkpoint = None
            collection.last_progress_at = self._transaction_now(session, None)

    def finish_link_selection(self, interest_id: UUID) -> None:
        """Commit selection completion independently of the selected children's acquisition."""
        with self._sessions() as session, session.begin():
            self._control(session)
            interest = session.get(InterestRecord, interest_id)
            if interest is None:
                raise KeyError(interest_id)
            if interest.status in ("settled", "cancelled"):
                return
            if interest.status != "selecting":
                raise ValueError("cannot finish selection before its acquisition result")
            collection = session.get(CollectionRecord, interest.collection_id)
            assert collection is not None
            self._assert_selection_accounted(collection, interest.selection_checkpoint)
            interest.status = "settled"
            interest.selection_checkpoint = None
            collection.last_progress_at = self._transaction_now(session, None)

    @staticmethod
    def _assert_selection_accounted(collection: CollectionRecord, payload: dict | None) -> None:
        if payload is None or collection.status == "settled":
            return
        checkpoint = SelectionCheckpoint.model_validate(payload)
        if (checkpoint.cursor < len(checkpoint.urls)
                and collection.reserved + collection.consumed < collection.page_limit):
            raise ValueError("selection still contains unaccounted URLs")

    def settle_collection(self, collection_id: UUID, *,
                          now: datetime | None = None) -> str | None:
        """An unrelated acquisition or background continuation never holds this request open."""
        with self._sessions() as session, session.begin():
            self._control(session)
            now = self._transaction_now(session, now)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.status == "settled":
                return collection.outcome
            if not collection.seeds_settled:
                return None
            active = session.scalar(select(InterestRecord.id).where(
                InterestRecord.collection_id == collection_id,
                InterestRecord.status.not_in(("settled", "cancelled")),
            ).limit(1))
            if active is not None:
                return None
            collection.status = "settled"
            collection.outcome = (
                "budget_reached" if collection.consumed >= collection.page_limit
                else "eligible_links_exhausted"
            )
            collection.completed_at = now
            collection.last_progress_at = now
            collection.waiting_reason = None
            self._collection_outcome(session, collection, now)
            return collection.outcome

    @staticmethod
    def _has_live_interest(session: Session, acquisition_id: UUID, now: datetime) -> bool:
        specifications = session.scalars(select(CollectionRecord.spec).join(
            InterestRecord, InterestRecord.collection_id == CollectionRecord.id,
        ).where(InterestRecord.acquisition_id == acquisition_id,
                InterestRecord.status.in_(("queued", "awaiting_result")), CollectionRecord.status == "active"))
        for value in specifications:
            spec = CollectionSpec.model_validate(value)
            if spec.deadline_at is None or spec.deadline_at > now:
                return True
        return False

    def current_domain_policy(self, acquisition_id: UUID) -> DomainPolicySnapshot:
        with self._sessions() as session:
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if acquisition is None:
                raise StaleDispatch("acquisition no longer exists")
            return domain_policy_snapshot(find_domain_policy_for_url(session, url=acquisition.url))

    def begin_attempt(self, acquisition_id: UUID, generation: int, *,
                      domain_policy: DomainPolicySnapshot | None = None,
                      now: datetime | None = None, lease_seconds: int = 120) -> bool:
        """Fence duplicate/delayed deliveries before the worker calls CDP.

        The worker must also hold the operation lease and domain permit. Rejection
        does not mean a delivery is terminal; reconciliation checks current state.
        """
        if lease_seconds < 1:
            raise ValueError("lease must be positive")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.generation != generation or control.paused
                    or acquisition.attempt_started_at is not None
                    or acquisition.claim_expires_at is None
                    or _aware(acquisition.claim_expires_at) <= now
                    or acquisition.attempt_count >= acquisition.attempt_limit):
                return False
            if is_excluded(acquisition.url, control.exclusions):
                self._exclude_acquisition(session, control, acquisition, now)
                return False
            current_domain = domain_policy_snapshot(find_domain_policy_for_url(session, url=acquisition.url))
            if current_domain.paused or (domain_policy is not None and current_domain != domain_policy):
                return False
            acquisition.attempt_domain_policy = current_domain.model_dump(mode="json")
            active_request = self._has_live_interest(session, acquisition_id, now)
            background = acquisition.background_reason is not None and control.background_share > 0
            if not active_request and not background:
                return False
            if acquisition.attempt_reserved_ms <= 0:
                raise ValueError("capture has no physical allowance reservation")
            # A request may disappear after dispatch. Continuing solely for the
            # background reason must acquire its allowance before the CDP call.
            if not active_request and not acquisition.attempt_background:
                if self._background_waiting_reason(control, acquisition.attempt_reserved_ms):
                    return False
                self._reserve_background(control, acquisition)
            if acquisition.attempt_background:
                control.background_reserved_attempts -= 1
                control.background_started_attempts += 1
            control.reserved_attempts -= 1
            control.started_attempts += 1
            acquisition.attempt_exclusions = list(control.exclusions)
            acquisition.attempt_exclusion_version = control.policy_version
            acquisition.attempt_count += 1
            acquisition.defer_reason = None
            acquisition.attempt_started_at = now
            acquisition.claim_expires_at = now + timedelta(seconds=lease_seconds)
            self._mark_acquisition_progress(session, acquisition_id, now)
            return True

    def defer_retry(self, acquisition_id: UUID, generation: int, result: AcquisitionResult, *,
                    now: datetime | None = None, delay_seconds: float = 5) -> bool:
        if result.attempt_evidence is None or result.success:
            raise ValueError("retry requires failed attempt evidence")
        if not 0 <= delay_seconds <= 3600:
            raise ValueError("retry delay outside bounds")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.generation != generation):
                raise StaleDispatch("retry belongs to an obsolete dispatch")
            if acquisition.attempt_count >= acquisition.attempt_limit:
                return False
            if (result.attempt_evidence.requested_url != acquisition.url
                    or result.attempt_evidence.attempt != acquisition.attempt_count):
                raise ValueError("retry evidence differs from the active attempt")
            self._settle_attempt(control, acquisition, result.attempt_evidence.resource_usage)
            acquisition.prior_results = [*acquisition.prior_results, result.model_dump(
                mode="json", exclude={"html", "document_bytes", "evidence"},
            )]
            self._mark_acquisition_progress(session, acquisition_id, now)
            acquisition.status = "retry"
            acquisition.generation += 1
            acquisition.attempt_started_at = None
            acquisition.claim_expires_at = None
            acquisition.eligible_at = now + timedelta(seconds=delay_seconds)
            control.active_count -= 1
            control.pending_count += 1
            return True

    def needs_navigation(self, acquisition_id: UUID) -> bool:
        with self._sessions() as session:
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if acquisition is None:
                raise KeyError(acquisition_id)
            control = session.get(FrontierControlRecord, 1)
            if (acquisition.visibility == "public" and control is not None
                    and control.background_share > 0):
                return True
            rows = session.execute(select(InterestRecord.context, CollectionRecord.spec).join(
                CollectionRecord, CollectionRecord.id == InterestRecord.collection_id,
            ).where(InterestRecord.acquisition_id == acquisition_id,
                    InterestRecord.status == "awaiting_result"))
            return any(context["depth"] < spec["max_depth"] for context, spec in rows)

    def renew_attempt(self, acquisition_id: UUID, generation: int, *,
                      now: datetime | None = None, lease_seconds: int = 120) -> bool:
        if lease_seconds < 1:
            raise ValueError("lease must be positive")
        with self._sessions() as session, session.begin():
            self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.generation != generation
                    or acquisition.attempt_started_at is None
                    or acquisition.claim_expires_at is None
                    or _aware(acquisition.claim_expires_at) <= now):
                return False
            acquisition.claim_expires_at = now + timedelta(seconds=lease_seconds)
            return True

    def recover_dispatch(self, acquisition_id: UUID, *, now: datetime | None = None) -> None:
        """Fence expired work and return it through normal capacity/rate eligibility."""
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if (acquisition is None or acquisition.status != "dispatched"
                    or acquisition.claim_expires_at is None
                    or _aware(acquisition.claim_expires_at) > now):
                return None
            usage = None
            if acquisition.attempt_started_at is not None:
                usage = AttemptUsage(policy_version=acquisition.dispatch_policy_version,
                                     domain_policy=acquisition.attempt_domain_policy,
                                     exclusion_policy_version=acquisition.attempt_exclusion_version,
                                     reserved_ms=acquisition.attempt_reserved_ms)
                acquisition.uncertain_attempts = [*acquisition.uncertain_attempts, {
                    "generation": acquisition.generation,
                    "started_at": _aware(acquisition.attempt_started_at).isoformat(),
                    "uncertain_at": now.isoformat(),
                    "resource_usage": usage.model_dump(mode="json"),
                }]
            self._settle_attempt(control, acquisition, usage)
            if acquisition.attempt_count >= acquisition.attempt_limit:
                acquisition.generation += 1
                acquisition.attempt_started_at = None
                acquisition.claim_expires_at = None
                evidence = terminal_evidence(acquisition, now, "failed")
                acquisition.background_url_key = None
                acquisition.status = "failed"
                acquisition.completed_at = now
                acquisition.outcome = evidence.model_dump(mode="json")
                control.active_count -= 1
                self._outbox(session, acquisition, "observation", acquisition.outcome,
                             f"observation:{acquisition.id}")
                for interest in session.scalars(select(InterestRecord).where(
                    InterestRecord.acquisition_id == acquisition.id,
                    InterestRecord.status == "awaiting_result",
                )):
                    interest.status = "settled"
                    self._fulfillment(session, interest, acquisition, now)
                session.flush()
                return None
            acquisition.generation += 1
            acquisition.attempt_started_at = None
            acquisition.claim_expires_at = None
            control.active_count -= 1
            remaining = session.scalar(select(InterestRecord.id).where(
                InterestRecord.acquisition_id == acquisition.id,
                InterestRecord.status == "awaiting_result",
            ).limit(1))
            if remaining is None and acquisition.background_reason is None:
                acquisition.status = "cancelled"
                acquisition.completed_at = now
                if acquisition.attempt_count:
                    acquisition.outcome = terminal_evidence(acquisition, now, "cancelled").model_dump(mode="json")
                    self._outbox(session, acquisition, "observation", acquisition.outcome,
                                 f"observation:{acquisition.id}")
            else:
                acquisition.status = "retry"
                acquisition.eligible_at = now
                control.pending_count += 1


    def stop_collection(self, collection_id: UUID, *, reason: str = "cancelled",
                        now: datetime | None = None) -> None:
        if reason not in {"cancelled", "deadline", "failed"}:
            raise ValueError("invalid collection stop reason")
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            collection = session.get(CollectionRecord, collection_id)
            if collection is None:
                raise KeyError(collection_id)
            if collection.status == "settled":
                return
            interests = list(session.scalars(select(InterestRecord).where(
                InterestRecord.collection_id == collection_id,
                InterestRecord.status.not_in(("settled", "cancelled")))))
            affected = set()
            for interest in interests:
                self._detach(collection, interest)
                affected.add(interest.acquisition_id)
            session.flush()
            for identity in affected:
                acquisition = session.get(AcquisitionRecord, identity)
                assert acquisition is not None
                other = session.scalar(select(InterestRecord.id).where(
                    InterestRecord.acquisition_id == identity, InterestRecord.status.in_(("queued", "awaiting_result"))).limit(1))
                if acquisition.status in ("queued", "retry") and other is None and acquisition.background_reason is None:
                    if acquisition.status == "retry":
                        evidence = terminal_evidence(acquisition, now, "cancelled")
                        acquisition.outcome = evidence.model_dump(mode="json")
                        acquisition.generation += 1
                        self._outbox(session, acquisition, "observation", acquisition.outcome,
                                     f"observation:{acquisition.id}")
                    acquisition.status = "cancelled"
                    acquisition.pending_key = None
                    acquisition.completed_at = now
                    control.pending_count -= 1
            collection.status = "settled"
            collection.outcome = reason
            collection.completed_at = now
            collection.last_progress_at = now
            self._collection_outcome(session, collection, now)

    @staticmethod
    def _detach(collection: CollectionRecord, interest: InterestRecord) -> None:
        if interest.budget_state == "reserved":
            collection.reserved -= 1
            interest.budget_state = "released"
        interest.status = "cancelled"

    def complete(self, acquisition_id: UUID, generation: int, *, evidence: VisitEvidence,
                 navigation: NavigationPackage | None = None,
                 now: datetime | None = None) -> bool:
        if evidence.visit.visit_id != acquisition_id:
            raise ValueError("evidence belongs to a different acquisition")
        success = evidence.visit.outcome == "succeeded"
        outcome = evidence.model_dump(mode="json")
        navigation_json = navigation.model_dump(mode="json") if navigation else None
        with self._sessions() as session, session.begin():
            control = self._control(session)
            now = self._transaction_now(session, now)
            acquisition = session.get(AcquisitionRecord, acquisition_id)
            if acquisition is None:
                raise KeyError(acquisition_id)
            if acquisition.generation != generation:
                raise StaleDispatch("acquisition was reclaimed")
            if (evidence.visit.requested_url != acquisition.url
                    or evidence.visit.visibility != acquisition.visibility):
                raise ValueError("evidence URL or visibility differs from acquisition")
            if acquisition.status in ("succeeded", "failed"):
                if (acquisition.outcome != outcome or (acquisition.navigation or acquisition.retired_navigation) != navigation_json
                        or (acquisition.status == "succeeded") != success):
                    raise ValueError("conflicting terminal outcome")
                return False
            if acquisition.status != "dispatched":
                raise StaleDispatch("acquisition is not dispatched")
            if acquisition.attempt_started_at is None or len(evidence.attempts) != acquisition.attempt_count:
                raise ValueError("outcome must include every authorized physical attempt")
            prior = attempt_records(acquisition.id, acquisition_context(acquisition).prior_attempts)
            if evidence.attempts[:-1] != prior:
                raise ValueError("outcome changed previously recorded physical attempts")
            self._settle_attempt(control, acquisition, evidence.attempts[-1].resource_usage)
            acquisition.attempt_started_at = None
            acquisition.background_url_key = None
            acquisition.status = "succeeded" if success else "failed"
            acquisition.outcome = outcome
            acquisition.navigation = navigation_json
            acquisition.completed_at = now
            acquisition.claim_expires_at = None
            control.active_count -= 1
            self._outbox(session, acquisition, "observation", outcome, f"observation:{acquisition.id}")
            interests = session.scalars(select(InterestRecord).where(
                InterestRecord.acquisition_id == acquisition_id, InterestRecord.status == "awaiting_result"))
            for interest in interests:
                interest.status = "selecting" if success else "settled"
                self._fulfillment(session, interest, acquisition, now)
            return True

    @staticmethod
    def _mark_acquisition_progress(session: Session, acquisition_id: UUID, now: datetime) -> None:
        session.execute(update(CollectionRecord).where(
            CollectionRecord.status != "settled",
            CollectionRecord.id.in_(select(InterestRecord.collection_id).where(
                InterestRecord.acquisition_id == acquisition_id,
                InterestRecord.status.in_(("awaiting_result", "selecting")),
            )),
        ).values(last_progress_at=now))

    @staticmethod
    def _fulfillment(session: Session, interest: InterestRecord,
                     acquisition: AcquisitionRecord, now: datetime) -> None:
        collection = session.get(CollectionRecord, interest.collection_id)
        assert collection is not None
        collection.last_progress_at = now
        context = SelectionContext.model_validate(interest.context)
        FrontierStore._lineage(session, FulfillmentRecord(
            record_id=interest.id, collection_id=interest.collection_id,
            observation_id=acquisition.id, requested_url=interest.url,
            parent_observation_id=context.parent_observation_id, depth=context.depth,
            rule_id=context.rule_id, mode=interest.mode,
            visibility=collection.spec["visibility"], recorded_at=now,
        ), acquisition_id=acquisition.id, collection_id=collection.id)

    @staticmethod
    def _seed_provenance(collection: CollectionRecord) -> dict | None:
        if collection.seed_provenance is not None:
            return collection.seed_provenance
        checkpoint = collection.selection_checkpoint
        return None if checkpoint is None else {
            "source_snapshot": checkpoint.get("source_snapshot"),
            "source_query_id": checkpoint.get("source_query_id"),
            "selected_at": checkpoint.get("selected_at"),
            "discovery": None if not collection.discovery_state else {
                "model": collection.discovery_state.get("model"), "queries": collection.discovery_state.get("queries", []),
            },
            "candidates_sha256": hashlib.sha256(json.dumps(checkpoint["urls"], separators=(",", ":")).encode()).hexdigest(),
        }

    @staticmethod
    def _collection_outcome(session: Session, collection: CollectionRecord, now: datetime) -> None:
        session.flush()
        statuses = list(session.scalars(select(AcquisitionRecord.status).join(
            InterestRecord, InterestRecord.acquisition_id == AcquisitionRecord.id
        ).where(InterestRecord.collection_id == collection.id,
                InterestRecord.budget_state == "consumed")))
        provenance = FrontierStore._seed_provenance(collection)
        FrontierStore._lineage(session, CollectionOutcome(
            record_id=collection.id, collection_id=collection.id,
            visibility=collection.spec["visibility"], recorded_at=now,
            outcome=collection.outcome, seed_provenance=provenance, consumed_pages=collection.consumed,
            supplied_pages=statuses.count("succeeded"), failed_pages=statuses.count("failed"),
        ), collection_id=collection.id)

    @staticmethod
    def _lineage(session: Session, evidence: LineageEvidence, *,
                 acquisition_id: UUID | None = None, collection_id: UUID | None = None) -> None:
        session.add(FrontierOutboxRecord(
            message_id=f"lineage:{evidence.kind}:{evidence.record_id}",
            acquisition_id=acquisition_id, collection_id=collection_id,
            kind="lineage", payload=evidence.model_dump(mode="json"),
        ))

    @staticmethod
    def _outbox(session: Session, acquisition: AcquisitionRecord, kind: str,
                payload: dict, identity: str) -> None:
        session.add(FrontierOutboxRecord(message_id=identity, acquisition_id=acquisition.id,
                                         kind=kind, payload=payload))
