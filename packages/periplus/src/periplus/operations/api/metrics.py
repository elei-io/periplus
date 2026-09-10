from datetime import UTC, datetime
from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from sqlalchemy import func, select
from periplus.crawl.runtime.frontier_models import AcquisitionRecord, FrontierControlRecord

router = APIRouter(tags=["operations"])
pending = Gauge('periplus_frontier_pending_acquisitions', 'Current queued and retrying acquisitions; shared across API replicas.')
oldest = Gauge('periplus_frontier_oldest_pending_seconds', 'Age of the oldest queued or retrying acquisition; zero when empty.')


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request) -> Response:
    # The query service shares this endpoint but does not own a frontier database.
    sessions = getattr(request.app.state, "frontier_sessions", None)
    if sessions is not None:
        refresh_frontier_metrics(sessions)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def refresh_frontier_metrics(sessions) -> None:
    with sessions() as session:
        oldest_created = select(func.min(AcquisitionRecord.created_at)).where(
            AcquisitionRecord.status.in_(("queued", "retry"))).scalar_subquery()
        count, created = session.execute(select(FrontierControlRecord.pending_count, oldest_created).where(
            FrontierControlRecord.id == 1)).one()
        pending.set(count)
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        oldest.set(max(0, (datetime.now(UTC) - created).total_seconds()) if created else 0)
