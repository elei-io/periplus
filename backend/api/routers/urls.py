from typing import Annotated

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from artifacts.models import Artifact
from artifacts.schemas import ArtifactListRecord
from artifacts.service import BYTE_ARTIFACT_KINDS, is_cache_eligible, warning_count
from crawls.models import Crawl
from crawls.service import _error_message, _warning_count
from crawls.schemas import CrawlListRecord
from db.session import get_session
from metrics.history import DEFAULT_WINDOW_SECONDS, url_metrics
from metrics.schemas import HistoryMetricsResponse
from urls.schemas import UrlDetailRecord, UrlListResponse
from urls.service import count_urls, get_url, list_urls

router = APIRouter(prefix="/urls", tags=["urls"])


@router.get("/metrics", response_model=HistoryMetricsResponse)
def metrics(
    session: Annotated[Session, Depends(get_session)],
    url_pattern: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    window_seconds: Annotated[int, Query(ge=60, le=7 * 24 * 60 * 60)] = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    return url_metrics(
        session=session,
        url_pattern=url_pattern,
        domain=domain,
        window_seconds=window_seconds,
    )


@router.get("/", response_model=UrlListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    url_pattern: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UrlListResponse:
    return UrlListResponse(
        items=list_urls(
            session=session,
            url_pattern=url_pattern,
            domain=domain,
            limit=limit,
            offset=offset,
        ),
        total=count_urls(session=session, url_pattern=url_pattern, domain=domain),
        limit=limit,
        offset=offset,
    )


@router.get("/{url_id}", response_model=UrlDetailRecord)
def get(
    url_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> UrlDetailRecord:
    url = get_url(session=session, url_id=url_id)
    if url is None:
        raise HTTPException(status_code=404, detail="URL not found.")

    crawls = list(
        session.scalars(
            select(Crawl)
            .where(Crawl.url_id == url.id)
            .order_by(Crawl.started_at.desc())
            .limit(20)
        )
    )
    artifacts = list(
        session.scalars(
            select(Artifact)
            .where(Artifact.url_id == url.id)
            .where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
            .order_by(Artifact.created_at.desc())
            .limit(20)
        )
    )
    all_artifacts = list(
        session.scalars(
            select(Artifact)
            .where(Artifact.url_id == url.id)
            .where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
        )
    )
    latest_crawl = crawls[0] if crawls else None
    latest_artifact = artifacts[0] if artifacts else None
    crawl_warning_count = sum(_warning_count(crawl) for crawl in crawls)
    artifact_warning_count = sum(warning_count(artifact) for artifact in artifacts)

    return UrlDetailRecord(
        id=url.id,
        url=url.url,
        normalized_url=url.normalized_url,
        scheme=url.scheme,
        host=url.host,
        domain=url.domain,
        path=url.path,
        query=url.query,
        query_fingerprint=url.query_fingerprint,
        crawl_count=count_urls_crawls(session, url.id),
        artifact_count=count_urls_artifacts(session, url.id),
        active_artifact_count=count_urls_artifacts(session, url.id, invalidated=False),
        invalidated_artifact_count=count_urls_artifacts(session, url.id, invalidated=True),
        cache_eligible_count=sum(1 for artifact in all_artifacts if is_cache_eligible(artifact)),
        latest_status_code=latest_crawl.status_code if latest_crawl else None,
        latest_crawl_at=latest_crawl.started_at if latest_crawl else None,
        latest_artifact_at=latest_artifact.created_at if latest_artifact else None,
        warning_count=crawl_warning_count + artifact_warning_count,
        crawl_warning_count=crawl_warning_count,
        artifact_warning_count=artifact_warning_count,
        recent_crawls=[
            CrawlListRecord(
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
                artifact_count=count_crawl_artifacts(session, crawl.id),
                url=url.url,
                normalized_url=url.normalized_url,
                domain=url.domain,
                path_name=url.path,
            )
            for crawl in crawls
        ],
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
                url=url.url,
                normalized_url=url.normalized_url,
                domain=url.domain,
                path_name=url.path,
            )
            for artifact in artifacts
        ],
    )


def count_urls_crawls(session: Session, url_id: UUID) -> int:
    from sqlalchemy import func

    return int(session.scalar(select(func.count()).select_from(Crawl).where(Crawl.url_id == url_id)) or 0)


def count_urls_artifacts(session: Session, url_id: UUID, invalidated: bool | None = None) -> int:
    from sqlalchemy import func

    statement = select(func.count()).select_from(Artifact).where(
        Artifact.url_id == url_id,
        Artifact.kind.in_(BYTE_ARTIFACT_KINDS),
    )
    if invalidated is True:
        statement = statement.where(Artifact.invalidated_at.is_not(None))
    if invalidated is False:
        statement = statement.where(Artifact.invalidated_at.is_(None))
    return int(session.scalar(statement) or 0)


def count_crawl_artifacts(session: Session, crawl_id: UUID) -> int:
    from sqlalchemy import func

    return int(session.scalar(select(func.count()).select_from(Artifact).where(Artifact.crawl_id == crawl_id)) or 0)
