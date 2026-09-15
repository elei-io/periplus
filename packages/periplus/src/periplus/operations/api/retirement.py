import asyncio
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from periplus.ingestion.archive import Archive
from periplus.ingestion.objects.exceptions import RepositoryObjectNotFound
from periplus.retention.captures import request_retirement

router = APIRouter()


@router.post("/operations/captures/{identity}/retirement", status_code=202)
async def retire_capture(identity: UUID, request: Request):
    try:
        await asyncio.to_thread(
            request_retirement, identity, Archive(request.app.state.document_store)
        )
    except RepositoryObjectNotFound as error:
        raise HTTPException(404, "Capture not found") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"capture_id": identity, "status": "requested"}
