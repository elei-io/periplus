"""Standard-CDP crawler process over the continuous shared frontier."""
import asyncio
from datetime import UTC, datetime
import os

import httpx

from playwright.async_api import async_playwright

from periplus.crawl.control.collections.discovery import SourceDiscovery
from periplus.crawl.acquisition.errors import PlaywrightRuntimeLost
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.content_policies.service import content_policy_snapshot, find_content_policy_for_url
from periplus.crawl.control.domain_policies.service import domain_policy_snapshot, find_domain_policy_for_url
from periplus.crawl.runtime.domain_pacing import ensure_domain_pacing_storage
from periplus.crawl.runtime.frontier_queue import (
    CAPTURE_CONSUMER, CAPTURE_STREAM, CAPTURE_SUBJECT, CrawlerPresence, ensure_capture_queue,
    ensure_crawler_presence,
)
from periplus.crawl.runtime.frontier_runtime import run_frontier
from periplus.crawl.runtime.frontier_health import DispatchHealth
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.seed_query import SeedQueryClient
from periplus.ingestion.acquisition import AcquisitionPipeline
from periplus.platform.config import get_float, get_optional, get_str
from periplus.platform.config.performance import CRAWL_ACQUISITION_LANES, OPERATIONAL_STATE_REPLICAS
from periplus.platform.health import HealthMonitor
from periplus.platform.messaging.client import connect_nats
from periplus.platform.messaging.leases import ensure_operation_lease_storage
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.process import WorkerEndpointConfig, WorkerEndpoints, install_signal_handlers


def _resolve_policy(url: str) -> EffectivePolicySnapshot:
    with SessionLocal() as session:
        return EffectivePolicySnapshot(
            content=content_policy_snapshot(find_content_policy_for_url(session, url=url)),
            domain=domain_policy_snapshot(find_domain_policy_for_url(session, url=url)),
        )


async def _watch_playwright_driver(playwright_context) -> None:
    try:
        await asyncio.shield(playwright_context._connection._transport.on_error_future)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        raise PlaywrightRuntimeLost("local Playwright driver process exited") from exc
    raise PlaywrightRuntimeLost("local Playwright driver process exited")


async def _heartbeat(monitor: HealthMonitor, stop: asyncio.Event) -> None:
    while not stop.is_set():
        monitor.heartbeat()
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def _presence(bucket, jetstream, worker_id: str, started: datetime,
                    monitor: HealthMonitor, stop: asyncio.Event, dispatch_health: DispatchHealth) -> None:
    while not stop.is_set():
        # Failure exits the task group: a replica with broken control/delivery
        # connectivity must not silently retain dispatch or browser capacity.
        await bucket.put(worker_id.replace(":", "-"), CrawlerPresence(
            worker_id=worker_id, started_at=started, last_seen_at=datetime.now(UTC),
            capture_lanes=CRAWL_ACQUISITION_LANES, dispatch=dispatch_health.snapshot(),
        ).model_dump_json().encode())
        consumer = await jetstream.consumer_info(CAPTURE_STREAM, CAPTURE_CONSUMER)
        monitor.queue_observed(
            "capture", pending=max(0, consumer.num_pending) + max(0, consumer.num_ack_pending),
            progress_marker=(consumer.ack_floor.stream_seq, consumer.num_pending, consumer.num_ack_pending),
            stalled_after_seconds=get_float("PERIPLUS_WORKER_QUEUE_STALL_SECONDS"),
        )
        monitor.subsystem_ready("presence")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


async def run() -> None:
    stop = asyncio.Event()
    install_signal_handlers(stop)
    worker_id = get_optional("PERIPLUS_CRAWLER_ID") or f"crawler:{os.uname().nodename}:{os.getpid()}"
    endpoints = WorkerEndpoints(WorkerEndpointConfig.from_env("crawler"))
    monitor = HealthMonitor(heartbeat_timeout_seconds=get_float("PERIPLUS_CRAWLER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"))
    endpoints.start_metrics()
    endpoints.start_health(monitor)
    client = None
    seeds = None
    try:
        store = FrontierStore(SessionLocal)
        await asyncio.to_thread(store.validate_installed)
        client = await connect_nats()
        jetstream = client.jetstream()
        await ensure_capture_queue(jetstream, replicas=OPERATIONAL_STATE_REPLICAS)
        domain = await ensure_domain_pacing_storage(jetstream)
        operations = await ensure_operation_lease_storage(jetstream)
        presence = await ensure_crawler_presence(jetstream)
        subscription = await jetstream.pull_subscribe(CAPTURE_SUBJECT, durable=CAPTURE_CONSUMER, stream=CAPTURE_STREAM)
        seeds = SeedQueryClient(get_str("PERIPLUS_QUERY_URL"), get_optional("PERIPLUS_QUERY_API_TOKEN"))
        playwright_context = async_playwright()
        async with (playwright_context as playwright,
                    AcquisitionPipeline(maximum_concurrency=CRAWL_ACQUISITION_LANES) as pipeline,
                    httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False,
                                      limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)) as discovery_client):
            dispatch_health = DispatchHealth()
            monitor.dependencies_ready()
            monitor.subsystem_unavailable("presence", "starting")
            # The driver watcher has no normal completion; cancel it after the
            # frontier drains on a signal. Any other failure cancels all work.
            async with asyncio.TaskGroup() as tasks:
                driver = tasks.create_task(_watch_playwright_driver(playwright_context), name="playwright-driver")
                tasks.create_task(_heartbeat(monitor, stop), name="crawler-heartbeat")
                tasks.create_task(_presence(presence, jetstream, worker_id, datetime.now(UTC), monitor, stop, dispatch_health), name="crawler-presence")
                try:
                    await run_frontier(
                        store, subscription=subscription, jetstream=jetstream, ingestion=pipeline.queue,
                        pipeline=pipeline, playwright=playwright, operation_bucket=operations, domain_bucket=domain,
                        policy=_resolve_policy, stop=stop, capture_lanes=CRAWL_ACQUISITION_LANES, seed_query=seeds.select,
                        discovery=SourceDiscovery(discovery_client), dispatch_health=dispatch_health,
                    )
                finally:
                    stop.set()
                    driver.cancel()
    finally:
        stop.set()
        try:
            if seeds is not None:
                seeds.close()
            if client is not None:
                await client.drain()
        finally:
            await endpoints.close()
