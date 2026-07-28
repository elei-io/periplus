import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_control import CatalogueControl
from api.graph_runtime import ApiGraphRuntime
from api.routers import (
    crawl_graphs,
    crawl_schedules,
    crawl_policies,
    data_status,
    domain_policies,
    graph_runs,
    ingestion,
    materializations,
    operational_metrics,
    repository_operations,
    sql_console,
)
from runtime.catalogue_workers import ensure_catalogue_worker_storage
from runtime.crawl_scheduler import run_scheduler
from runtime.graph_queue import (
    ensure_graph_storage,
)
from runtime.graph_outbox import run_outbox_relay
from runtime.nats_client import connect_nats
from repository.ingestion.external import EvidenceImportService
from materialization.store import AsyncMaterializationRunStore
from runtime.catalogue_queue import ensure_catalogue_work_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    nats_client = await connect_nats()
    catalogue_control = None
    scheduler_stop = None
    scheduler_task = None
    outbox_stop = None
    outbox_task = None
    evidence_import_service = None
    try:
        jetstream = nats_client.jetstream()
        await ensure_catalogue_work_stream(jetstream)
        runs, requests, workers = await ensure_graph_storage(jetstream)
        catalogue_workers = await ensure_catalogue_worker_storage(jetstream)
        graph_runtime = ApiGraphRuntime(
            nats_client=nats_client,
            jetstream=jetstream,
            runs=runs,
            requests=requests,
            workers=workers,
            catalogue_workers=catalogue_workers,
        )
        app.state.graph_runtime = graph_runtime
        app.state.materialization_runs = AsyncMaterializationRunStore()
        evidence_import_service = EvidenceImportService()
        await evidence_import_service.start()
        app.state.evidence_import_service = evidence_import_service
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(
            run_outbox_relay(runs, jetstream, stop=outbox_stop),
            name="graph-outbox",
        )
        catalogue_control = CatalogueControl()
        await catalogue_control.start()
        app.state.catalogue_control = catalogue_control
        scheduler_stop = asyncio.Event()
        scheduler_task = asyncio.create_task(
            run_scheduler(
                scheduler_stop,
                runs=runs,
                requests=requests,
                progress=runs,
                jetstream=jetstream,
                catalogue_snapshot_resolver=catalogue_control.latest_snapshot,
            ),
            name="crawl-scheduler",
        )
        yield
    finally:
        if scheduler_stop is not None:
            scheduler_stop.set()
        if outbox_stop is not None:
            outbox_stop.set()
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        if outbox_task is not None:
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        if catalogue_control is not None:
            await catalogue_control.close()
        if evidence_import_service is not None:
            await evidence_import_service.close()
        await nats_client.drain()


app = FastAPI(title="Atlas API", lifespan=lifespan)
app.include_router(operational_metrics.router)
app.include_router(data_status.router)
app.include_router(repository_operations.router)
app.include_router(materializations.router)
app.include_router(ingestion.router)
app.include_router(sql_console.router)
app.include_router(crawl_graphs.router)
app.include_router(crawl_schedules.router)
app.include_router(crawl_schedules.resource_router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(domain_policies.router)
