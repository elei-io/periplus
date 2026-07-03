import asyncio
from contextlib import suppress
import json
import os
import signal

from nats.js.errors import NotFoundError

from domains.cache import cache_root, cleanup_cache

from .jobs import _JOBS_SUBJECT, _jobs_kv, connect_nats, get_index_job, update_index_job
from .service import index

_STREAM = "ATLAS_INDEX"
_DURABLE = "atlas-index-worker"
_DEFAULT_CACHE_CLEANUP_INTERVAL = 3600
_DEFAULT_CACHE_TTL_SECONDS = 86400


def _env_seconds(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value)
    except ValueError:
        return default


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


async def _cache_cleanup_loop(stop: asyncio.Event) -> None:
    interval = _env_seconds("CACHE_CLEANUP_INTERVAL", _DEFAULT_CACHE_CLEANUP_INTERVAL)
    ttl_seconds = _env_seconds("CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)
    if interval <= 0 or ttl_seconds <= 0:
        return

    while not stop.is_set():
        try:
            removed = await asyncio.to_thread(cleanup_cache, ttl_seconds)
            if removed:
                print(f"removed {removed} expired cache folders from {cache_root()}", flush=True)
        except Exception as exc:
            print(f"cache cleanup failed: {exc}", flush=True)

        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def run_worker() -> None:
    stop = asyncio.Event()
    cleanup_task = asyncio.create_task(_cache_cleanup_loop(stop))
    nc = None

    for signame in ("SIGINT", "SIGTERM"):
        asyncio.get_running_loop().add_signal_handler(
            getattr(signal, signame),
            stop.set,
        )

    try:
        nc = await connect_nats()
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
        stop.set()
        cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup_task
        if nc is not None:
            await nc.close()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
