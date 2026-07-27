"""Public external HTML evidence ingestion."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from api.import_runtime import get_evidence_import_service
from repository.ingestion.external import (
    EvidenceImportService,
    ExternalHtmlMetadata,
    HtmlIngestionResult,
)


router = APIRouter(prefix="/ingest", tags=["ingestion"])


@router.post("/html", response_model=HtmlIngestionResult, status_code=202)
async def ingest_html(
    metadata: Annotated[str, Form()],
    content: Annotated[UploadFile, File()],
    service: Annotated[
        EvidenceImportService,
        Depends(get_evidence_import_service),
    ],
) -> HtmlIngestionResult:
    try:
        parsed = ExternalHtmlMetadata.model_validate_json(metadata)
        return await service.ingest_external_html(content.file, parsed)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await content.close()
