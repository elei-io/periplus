import asyncio
import json
import signal

from nats.js.errors import NotFoundError

from .jobs import _JOBS_SUBJECT, _jobs_kv, connect_nats, get_index_job, update_index_job
from .service import index

_STREAM = "ATLAS_INDEX"
_DURABLE = "atlas-index-worker"


async def _ensure_stream(js):
    try:
        await js.stream_info(_STREAM)
    except NotFoundError:
        await js.add_stream(name=_STREAM, subjects=[_JOBS_SUBJECT])


async def _process_job(job_id: str) -> None:
    job = await get_index_job(job_id)
    if job is None:
        return

    await update_index_job(job_id, "running")
    try:
        result = await index(**job.request.model_dump())
    except Exception as exc:
        await update_index_job(job_id, "failed", error=str(exc))
        return

    await update_index_job(job_id, "succeeded", result=result)


async def run_worker() -> None:
    nc = await connect_nats()
    stop = asyncio.Event()

    for signame in ("SIGINT", "SIGTERM"):
        asyncio.get_running_loop().add_signal_handler(
            getattr(signal, signame),
            stop.set,
        )

    try:
        js = nc.jetstream()
        await _ensure_stream(js)
        await _jobs_kv(js)
        sub = await js.pull_subscribe(_JOBS_SUBJECT, durable=_DURABLE, stream=_STREAM)

        while not stop.is_set():
            try:
                messages = await sub.fetch(batch=1, timeout=1)
            except TimeoutError:
                continue

            for message in messages:
                try:
                    payload = json.loads(message.data.decode())
                    await _process_job(payload["id"])
                    await message.ack()
                except Exception:
                    await message.nak()
    finally:
        await nc.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
