"""Administrative storage dashboard adapter."""
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse
from periplus.operations.storage.models import StorageReport

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/storage", response_model=StorageReport)
async def storage(request: Request):
    try:
        return await request.app.state.storage.read()
    except TimeoutError:
        return JSONResponse({"detail": "Storage measurement is still running. Try again shortly."}, status_code=503, headers={"Retry-After": "5"})
