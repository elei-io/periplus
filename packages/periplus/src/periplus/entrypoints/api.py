from periplus.crawl.api import schedules
import asyncio
from periplus.platform.telemetry import configure_logging, HttpTelemetry
from contextlib import asynccontextmanager

from fastapi import FastAPI

from periplus.crawl.api import (
    content_policies,
    collections,
    frontier,
    domain_policies,
)
from periplus.operations.api import ingestion_status
from periplus.operations.api.access import router as access_router
from periplus.operations.api import metrics as operational_metrics
from periplus.operations.api import catalogue as sql_console
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.crawl.control.collections.results import CrawlResults
from periplus.platform.clickhouse import connect_clickhouse
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.frontier_queue import ensure_crawler_presence
from periplus.crawl.runtime.frontier_health import CrawlerPresenceReader
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.messaging.client import connect_nats
from periplus.ingestion.objects.config import object_store_from_env
from periplus.platform.messaging.catalogue_queue import ensure_catalogue_work_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("api")
    nats_client = await connect_nats()
    crawl_results = None
    try:
        jetstream = nats_client.jetstream()
        frontier = FrontierStore(SessionLocal)
        await asyncio.to_thread(frontier.validate_installed)
        await ensure_catalogue_work_stream(jetstream)
        app.state.frontier = frontier
        app.state.frontier_sessions = SessionLocal
        from periplus.operations.query_history.store import QueryHistoryStore

        class LocalHistory:
            async def record(self, value):
                await asyncio.to_thread(QueryHistoryStore(SessionLocal).record, value)

        app.state.query_history = LocalHistory()
        app.state.jetstream = jetstream
        await ensure_crawler_presence(jetstream)
        app.state.crawler_presence = CrawlerPresenceReader(jetstream)
        app.state.document_store = object_store_from_env()
        app.state.download_slot = asyncio.Semaphore(2)
        app.state.admin_sql_slot = asyncio.Semaphore(1)
        crawl_results = CrawlResults(
            await asyncio.to_thread(connect_clickhouse), SessionLocal
        )
        app.state.crawl_results = crawl_results
        yield
    finally:
        if crawl_results is not None:
            await crawl_results.close()
        await nats_client.drain()


app = FastAPI(title="Periplus API", lifespan=lifespan)
app.add_middleware(ApiAccessMiddleware)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


app.include_router(operational_metrics.router)
app.include_router(ingestion_status.router)
app.include_router(sql_console.router)
app.include_router(content_policies.router)
app.include_router(domain_policies.router)

app.include_router(collections.router)
app.include_router(frontier.router)

app.include_router(schedules.router)

app.add_middleware(HttpTelemetry, service="api")

app.include_router(access_router)

from periplus.operations.api.query_history import router as query_history_router

app.include_router(query_history_router)

from periplus.materialization.rebuilds.http import router as rebuild_router

app.include_router(rebuild_router)

from periplus.operations.api.archive_imports import router as archive_import_router

app.include_router(archive_import_router)

from periplus.operations.api.retirement import router as retirement_router

app.include_router(retirement_router)
