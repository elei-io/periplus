from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response
from nats.js.errors import NotFoundError

from repository.exceptions import RepositoryObjectNotFound
from repository.ingestion.admin import (
    DeadLetterList,
    DeadLetterRecord,
    list_dead_letters,
    requeue_repository_dead_letter,
)
from repository.objects.artifact import RawArtifactRepository, artifact_object_key
from repository.objects.config import object_store_from_env
from repository.objects.html import RawHtmlRepository, html_object_key

router = APIRouter(prefix="/operations/repository", tags=["operations"])


@router.get("/documents/{document_id}/content", response_class=Response)
def document_content(document_id: str) -> Response:
    sha256 = _content_sha256(document_id, kind="document")
    try:
        content = RawHtmlRepository(object_store_from_env()).read_bytes(
            html_object_key(sha256)
        )
    except RepositoryObjectNotFound as exc:
        raise HTTPException(status_code=404, detail="document was not found") from exc

    return Response(
        content=content,
        media_type="text/html",
        headers={
            "Content-Disposition": f'inline; filename="{sha256}.html"',
        },
    )


@router.get("/artifacts/{artifact_id}/content", response_class=Response)
def artifact_content(artifact_id: str) -> Response:
    sha256 = _content_sha256(artifact_id, kind="artifact")
    try:
        content = RawArtifactRepository(object_store_from_env()).read_bytes(
            artifact_object_key(sha256)
        )
    except RepositoryObjectNotFound as exc:
        raise HTTPException(status_code=404, detail="artifact was not found") from exc

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{sha256}"',
        },
    )


def _content_sha256(identity: str, *, kind: str) -> str:
    prefix = "sha256:"
    value = identity[len(prefix) :] if identity.startswith(prefix) else ""
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise HTTPException(status_code=404, detail=f"{kind} was not found")
    return value


@router.get("/dead-letters", response_model=DeadLetterList)
async def dead_letters(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> DeadLetterList:
    return await list_dead_letters(limit)


@router.post("/dead-letters/{sequence}/requeue", response_model=DeadLetterRecord)
async def requeue(sequence: int) -> DeadLetterRecord:
    if sequence < 1:
        raise HTTPException(
            status_code=422, detail="sequence must be greater than zero"
        )
    try:
        return await requeue_repository_dead_letter(sequence)
    except NotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="repository dead letter was not found"
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
