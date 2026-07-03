from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from domains.index.jobs import create_index_job, get_index_job, list_index_jobs
from domains.index.models import IndexJob, IndexJobSummary, IndexLink, Input, JobStatus
from domains.index.service import index as index_service

router = APIRouter(prefix="/index", tags=["index"])


@router.post("/", response_model=list[IndexLink])
async def index(
    request: Input,
) -> list[IndexLink]:
    return await index_service(
        **request.model_dump(),
    )


@router.post("/jobs/", response_model=IndexJob)
async def create_job(request: Input) -> IndexJob:
    return await create_index_job(request)


@router.get("/jobs/", response_model=list[IndexJobSummary])
async def list_jobs(status: Annotated[JobStatus | None, Query()] = None) -> list[IndexJobSummary]:
    return await list_index_jobs(status=status)


@router.get("/jobs/{job_id}", response_model=IndexJob)
async def get_job(job_id: str) -> IndexJob:
    job = await get_index_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return job
