from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy.orm import Session

from db.session import get_session
from observability.operations import SUPPORTED_WINDOWS, collect_operations_metrics
from observability.prometheus import api_metrics_payload
from observability.prometheus_source import collect_prometheus_metrics
from observability.schemas import OperationsMetricsResponse
from tasks.heartbeats import purge_stale_worker_heartbeats
from tasks.schemas import WorkerHeartbeatPurgeRecord

router = APIRouter(tags=["operations"])


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(content=api_metrics_payload(), media_type=CONTENT_TYPE_LATEST)


@router.get("/operations/metrics", response_model=OperationsMetricsResponse)
def operations_metrics(
    session: Annotated[Session, Depends(get_session)],
    window_seconds: Annotated[int, Query()] = 21600,
) -> OperationsMetricsResponse:
    if window_seconds not in SUPPORTED_WINDOWS:
        raise HTTPException(
            status_code=422,
            detail=f"window_seconds must be one of {sorted(SUPPORTED_WINDOWS)}.",
        )
    snapshot = collect_operations_metrics(session, window_seconds=window_seconds)
    session.commit()
    return snapshot.model_copy(
        update={"prometheus": collect_prometheus_metrics(window_seconds)}
    )


@router.post(
    "/operations/purge/workers",
    response_model=WorkerHeartbeatPurgeRecord,
)
def purge_workers(
    session: Annotated[Session, Depends(get_session)],
) -> WorkerHeartbeatPurgeRecord:
    deleted = purge_stale_worker_heartbeats(session)
    session.commit()
    return WorkerHeartbeatPurgeRecord(deleted=deleted)
