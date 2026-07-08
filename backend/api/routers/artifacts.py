from typing import Annotated

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from artifacts.schemas import (
    ArtifactDetailRecord,
    ArtifactInvalidateRequest,
    ArtifactInvalidateResponse,
    ArtifactListRecord,
    ArtifactListResponse,
)
from artifacts.service import count_artifacts, get_artifact, invalidate_artifacts, list_artifacts, warning_count
from db.session import get_session

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/", response_model=ArtifactListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    url_pattern: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
    invalidated: Annotated[bool | None, Query()] = None,
    warnings: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ArtifactListResponse:
    artifacts = list_artifacts(
        session=session,
        url_pattern=url_pattern,
        kind=kind,
        invalidated=invalidated,
        warnings=warnings,
        limit=limit,
        offset=offset,
    )
    total = count_artifacts(
        session=session,
        url_pattern=url_pattern,
        kind=kind,
        invalidated=invalidated,
        warnings=warnings,
    )
    return ArtifactListResponse(
        items=[
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
                url=artifact.url.url if artifact.url else None,
                normalized_url=artifact.url.normalized_url if artifact.url else None,
                domain=artifact.url.domain if artifact.url else None,
                path_name=artifact.url.path if artifact.url else None,
            )
            for artifact in artifacts
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{artifact_id}", response_model=ArtifactDetailRecord)
def get(
    artifact_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ArtifactDetailRecord:
    artifact = get_artifact(session=session, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    return ArtifactDetailRecord(
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
        meta=artifact.meta,
        warnings_json=artifact.warnings_json,
        warning_count=warning_count(artifact),
        invalidated_at=artifact.invalidated_at,
        invalidated_reason=artifact.invalidated_reason,
        created_at=artifact.created_at,
        url=artifact.url.url if artifact.url else None,
        normalized_url=artifact.url.normalized_url if artifact.url else None,
        domain=artifact.url.domain if artifact.url else None,
        path_name=artifact.url.path if artifact.url else None,
    )


@router.post("/invalidate", response_model=ArtifactInvalidateResponse)
def invalidate(
    request: ArtifactInvalidateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> ArtifactInvalidateResponse:
    count = invalidate_artifacts(
        session,
        artifact_ids=request.artifact_ids,
        url_ids=request.url_ids,
        url_pattern=request.url_pattern,
        kind=request.kind,
        warnings=request.warnings,
        reason=request.reason,
    )
    return ArtifactInvalidateResponse(invalidated=count)
