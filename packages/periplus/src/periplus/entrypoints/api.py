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
from periplus.ingestion import http as ingestion
from periplus.ingestion import documents_http as documents
from periplus.materialization import http as materializations
from periplus.operations.api import data_status, storage, ingestion_status
from periplus.operations.api.access import router as access_router
from periplus.operations.storage.service import StorageService
from periplus.operations.api import ingestion as repository_operations
from periplus.operations.api import metrics as operational_metrics
from periplus.platform.messaging.catalogue_workers import ensure_catalogue_worker_storage
from periplus.query import http as sql_console
from periplus.query import admin as admin_sql
from periplus.platform.catalogue.config import catalogue_config_from_env
from periplus.platform.api_access import ApiAccessMiddleware
from periplus.platform.catalogue.control import CatalogueControl
from periplus.crawl.control.collections.history import CollectionHistory
from periplus.crawl.runtime.frontier_store import FrontierStore
from periplus.crawl.runtime.frontier_queue import ensure_crawler_presence
from periplus.crawl.runtime.frontier_health import CrawlerPresenceReader
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.messaging.client import connect_nats
from periplus.ingestion.external import EvidenceImportService
from periplus.materialization.store import AsyncMaterializationRunStore
from periplus.platform.messaging.catalogue_queue import ensure_catalogue_work_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("api")
    nats_client = await connect_nats()
    catalogue_control = None
    evidence_import_service = None
    storage_service = None
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
        app.state.catalogue_workers = await ensure_catalogue_worker_storage(jetstream)
        app.state.materialization_runs = AsyncMaterializationRunStore()
        evidence_import_service = EvidenceImportService()
        await evidence_import_service.start()
        app.state.evidence_import_service = evidence_import_service
        app.state.document_store = evidence_import_service.document_repository.store
        catalogue_control = CatalogueControl()
        await catalogue_control.start()
        app.state.catalogue_control = catalogue_control
        app.state.collection_history = CollectionHistory(catalogue_control)
        app.state.admin_sql = admin_sql.AdminSqlService(catalogue_config_from_env())
        app.state.admin_sql_slot = asyncio.Semaphore(1)
        storage_service = StorageService(catalogue_config_from_env(), SessionLocal, app.state.document_store, jetstream)
        app.state.storage = storage_service
        yield
    finally:
        if storage_service is not None:
            await storage_service.close()
        if catalogue_control is not None:
            await catalogue_control.close()
        if evidence_import_service is not None:
            await evidence_import_service.close()
        await nats_client.drain()


app = FastAPI(title="Periplus API", lifespan=lifespan)
app.add_middleware(ApiAccessMiddleware)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


app.include_router(operational_metrics.router)
app.include_router(data_status.router)
app.include_router(ingestion_status.router)
app.include_router(storage.router)
app.include_router(repository_operations.router)
app.include_router(materializations.router)
app.include_router(ingestion.router)
app.include_router(documents.router)
app.include_router(sql_console.router)
app.include_router(admin_sql.router)
app.include_router(content_policies.router)
app.include_router(domain_policies.router)

app.include_router(collections.router)
app.include_router(frontier.router)

app.include_router(schedules.router)

app.add_middleware(HttpTelemetry, service="api")

app.include_router(access_router)

from periplus.operations.api.query_history import router as query_history_router
app.include_router(query_history_router)
