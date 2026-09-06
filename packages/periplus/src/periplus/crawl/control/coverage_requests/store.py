from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CoverageRequest
from .schemas import CoverageRequestCreate, CoverageStatus


def create_request(session: Session, payload: CoverageRequestCreate) -> CoverageRequest:
    request = CoverageRequest(**payload.model_dump(), status="pending")
    session.add(request)
    session.flush()
    return request


def get_request(session: Session, request_id: UUID) -> CoverageRequest | None:
    return session.get(CoverageRequest, request_id)


def list_requests(session: Session, status: CoverageStatus, limit: int, offset: int):
    filters = [CoverageRequest.status == status]
    if status == "completed":
        filters.append(CoverageRequest.completed_at >= datetime.now(UTC) - timedelta(days=30))
    total = session.scalar(select(func.count()).select_from(CoverageRequest).where(*filters)) or 0
    items = session.scalars(
        select(CoverageRequest).where(*filters)
        .order_by(CoverageRequest.created_at.desc(), CoverageRequest.id.desc())
        .offset(offset).limit(limit)
    ).all()
    return items, total


def read_requests(session: Session, items: list[CoverageRequest]):
    from periplus.crawl.runtime.graph_models import GraphRunRecord
    from .schemas import CoverageRequestRead, CoverageProgress
    ids = [item.run_id for item in items if item.run_id]
    runs = {run.id: run for run in session.scalars(select(GraphRunRecord).where(GraphRunRecord.id.in_(ids)))} if ids else {}
    return [CoverageRequestRead.model_validate(item).model_copy(update={
        "search_queries": item.resolution.get("queries", []),
        "progress": CoverageProgress.model_validate(runs[item.run_id]) if item.run_id in runs else None,
    }) for item in items]


class CoverageRequestStore:
    """Short Postgres transactions; no transaction spans provider or crawl work."""
    def __init__(self, session_factory=None):
        if session_factory is None:
            from periplus.platform.postgres.session import SessionLocal
            session_factory = SessionLocal
        self.sessions = session_factory

    def candidates(self, now: datetime) -> list[UUID]:
        with self.sessions() as session:
            return list(session.scalars(select(CoverageRequest.id).where(
                CoverageRequest.status.in_(["pending", "resolving", "ongoing"]),
                (CoverageRequest.retry_at.is_(None) | (CoverageRequest.retry_at <= now)),
            ).order_by(func.coalesce(CoverageRequest.retry_at, CoverageRequest.created_at), CoverageRequest.id).limit(20)))

    def get(self, identity: UUID) -> CoverageRequest | None:
        with self.sessions() as session:
            return session.get(CoverageRequest, identity)

    def update(self, identity: UUID, **values) -> None:
        with self.sessions() as session, session.begin():
            row = session.get(CoverageRequest, identity)
            if row is None:
                raise LookupError("Coverage request disappeared")
            for key, value in values.items():
                setattr(row, key, value)

    def policies(self, urls: list[str]):
        from periplus.crawl.runtime.graph_runs import resolve_policy_snapshots
        with self.sessions() as session:
            return resolve_policy_snapshots(session, urls)
