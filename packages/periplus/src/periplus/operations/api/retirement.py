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
        database = await request.app.state.crawl_results.material_database()
        rows = (await request.app.state.crawl_results._read(
            f"SELECT archive_record_key,lower(hex(evidence_digest)) AS digest FROM {database}.captures "
            "WHERE capture_id={identity:UUID} LIMIT 1", {"identity": str(identity)},
        ))["data"]
        if not rows:
            raise RepositoryObjectNotFound(str(identity))
        archive = Archive(request.app.state.document_store)
        await asyncio.to_thread(archive.read_location, rows[0]["archive_record_key"], identity, rows[0]["digest"])
        await asyncio.to_thread(request_retirement, identity, archive)
    except RepositoryObjectNotFound as error:
        raise HTTPException(404, "Capture not found") from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"capture_id": identity, "status": "requested"}
