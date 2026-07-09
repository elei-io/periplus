from typing import Annotated

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from artifacts.models import Artifact
from artifacts.schemas import ArtifactListRecord
from artifacts.service import BYTE_ARTIFACT_KINDS, warning_count
from crawls.schemas import CrawlDetailRecord, CrawlListResponse
from crawls.service import _error_message, _warning_count, count_crawls, get_crawl, list_crawls
from db.session import get_session
from metrics.history import DEFAULT_WINDOW_SECONDS, crawl_metrics
from metrics.schemas import HistoryMetricsResponse

router = APIRouter(prefix="/crawls", tags=["crawls"])


@router.get("/metrics", response_model=HistoryMetricsResponse)
def metrics(
    session: Annotated[Session, Depends(get_session)],
    url_pattern: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    warnings: Annotated[bool | None, Query()] = None,
    window_seconds: Annotated[int, Query(ge=60, le=7 * 24 * 60 * 60)] = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    return crawl_metrics(
        session=session,
        url_pattern=url_pattern,
        domain=domain,
        success=success,
        status_code=status_code,
        warnings=warnings,
        window_seconds=window_seconds,
    )


@router.get("/", response_model=CrawlListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    url_pattern: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    success: Annotated[bool | None, Query()] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    warnings: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CrawlListResponse:
    return CrawlListResponse(
        items=list_crawls(
            session=session,
            url_pattern=url_pattern,
            domain=domain,
            success=success,
            status_code=status_code,
            warnings=warnings,
            limit=limit,
            offset=offset,
        ),
        total=count_crawls(
            session=session,
            url_pattern=url_pattern,
            domain=domain,
            success=success,
            status_code=status_code,
            warnings=warnings,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/{crawl_id}", response_model=CrawlDetailRecord)
def get(
    crawl_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> CrawlDetailRecord:
    crawl = get_crawl(session=session, crawl_id=crawl_id)
    if crawl is None:
        raise HTTPException(status_code=404, detail="Crawl not found.")

    artifacts = list(
        session.scalars(
            select(Artifact)
            .where(Artifact.crawl_id == crawl.id)
            .where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
            .order_by(Artifact.created_at.desc())
        )
    )

    return CrawlDetailRecord(
        id=crawl.id,
        url_id=crawl.url_id,
        task_run_id=crawl.task_run_id,
        started_at=crawl.started_at,
        finished_at=crawl.finished_at,
        duration_ms=crawl.duration_ms,
        input_hash=crawl.input_hash,
        success=crawl.success,
        status_code=crawl.status_code,
        retry_count=crawl.retry_count,
        warning_count=_warning_count(crawl),
        error_message=_error_message(crawl),
        artifact_count=len(artifacts),
        url=crawl.url.url,
        normalized_url=crawl.url.normalized_url,
        domain=crawl.url.domain,
        path_name=crawl.url.path,
        inputs_json=crawl.inputs_json,
        redirects_json=crawl.redirects_json,
        errors_json=crawl.errors_json,
        warnings_json=crawl.warnings_json,
        meta=crawl.meta,
        created_at=crawl.created_at,
        artifacts=[
            ArtifactListRecord(
                id=artifact.id,
                crawl_id=artifact.crawl_id,
                url_id=artifact.url_id,
                task_run_id=artifact.task_run_id,
                kind=artifact.kind,
                path=artifact.path,
                content_type=artifact.content_type,
                size_bytes=artifact.size_bytes,
                sha256=artifact.sha256,
                input_hash=artifact.input_hash,
                warning_count=warning_count(artifact),
                invalidated_at=artifact.invalidated_at,
                invalidated_reason=artifact.invalidated_reason,
                created_at=artifact.created_at,
                url=crawl.url.url,
                normalized_url=crawl.url.normalized_url,
                domain=crawl.url.domain,
                path_name=crawl.url.path,
            )
            for artifact in artifacts
        ],
    )
