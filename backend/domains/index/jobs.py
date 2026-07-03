import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.errors import BucketNotFoundError, KeyNotFoundError, NoKeysError

from .models import IndexJob, IndexJobSummary, Input, JobStatus

_DEFAULT_NATS_URL = "nats://127.0.0.1:4222"
_JOBS_BUCKET = "index_jobs"
_JOBS_SUBJECT = "atlas.index.jobs"


def _nats_url() -> str:
    return os.getenv("NATS_URL", _DEFAULT_NATS_URL)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def connect_nats() -> NATS:
    nc = NATS()
    await nc.connect(_nats_url())
    return nc


async def _jobs_kv(js: JetStreamContext):
    try:
        return await js.key_value(_JOBS_BUCKET)
    except BucketNotFoundError:
        return await js.create_key_value(bucket=_JOBS_BUCKET)


def _decode_job(data: bytes) -> IndexJob:
    return IndexJob.model_validate_json(data)


async def create_index_job(request: Input) -> IndexJob:
    nc = await connect_nats()
    try:
        js = nc.jetstream()
        kv = await _jobs_kv(js)
        job = IndexJob(
            id=uuid4().hex,
            status="queued",
            url=request.url,
            created_at=_now(),
            updated_at=_now(),
            request=request,
        )
        await kv.put(job.id, job.model_dump_json().encode())
        await js.publish(_JOBS_SUBJECT, json.dumps({"id": job.id}).encode())
        return job
    finally:
        await nc.close()


async def get_index_job(job_id: str) -> IndexJob | None:
    nc = await connect_nats()
    try:
        kv = await _jobs_kv(nc.jetstream())
        try:
            entry = await kv.get(job_id)
        except KeyNotFoundError:
            return None
        return _decode_job(entry.value)
    finally:
        await nc.close()


async def list_index_jobs(status: JobStatus | None = None) -> list[IndexJobSummary]:
    nc = await connect_nats()
    try:
        kv = await _jobs_kv(nc.jetstream())
        try:
            keys = await kv.keys()
        except NoKeysError:
            return []
        jobs: list[IndexJobSummary] = []
        for key in keys:
            try:
                entry = await kv.get(key)
            except KeyNotFoundError:
                continue
            job = _decode_job(entry.value)
            if status is None or job.status == status:
                jobs.append(IndexJobSummary(**job.model_dump(exclude={"request", "result", "error"})))

        return sorted(jobs, key=lambda job: job.created_at, reverse=True)
    finally:
        await nc.close()


async def update_index_job(
    job_id: str,
    status: JobStatus,
    *,
    result: list | None = None,
    error: str | None = None,
) -> IndexJob | None:
    nc = await connect_nats()
    try:
        kv = await _jobs_kv(nc.jetstream())
        try:
            entry = await kv.get(job_id)
        except KeyNotFoundError:
            return None

        job = _decode_job(entry.value)
        job.status = status
        job.updated_at = _now()
        job.result = result
        job.error = error
        await kv.put(job.id, job.model_dump_json().encode())
        return job
    finally:
        await nc.close()
