import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_control import CatalogueControl
from api.routers import (
    catalogue,
    catalogue_queries,
    catalogue_scalar_macros,
    catalogue_table_macros,
    catalogue_views,
    crawl_graphs,
    crawl_schedules,
    crawl_policies,
    domain_policies,
    graph_runs,
    catalogue_materializations,
    operational_metrics,
    repository_operations,
    search,
)
from repository.catalogue.quack_runtime import QuackQueryRuntime
from runtime.catalogue_queries import ensure_catalogue_query_storage
from runtime.crawl_scheduler import run_scheduler
from runtime.nats_client import connect_nats
from runtime.resource_governor import ensure_resource_governor_storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    nats_client = await connect_nats()
    quack_runtime = None
    catalogue_control = None
    scheduler_stop = None
    scheduler_task = None
    try:
        jetstream = nats_client.jetstream()
        query_bucket = await ensure_catalogue_query_storage(jetstream)
        resource_bucket = await ensure_resource_governor_storage(jetstream)
        quack_runtime = QuackQueryRuntime(query_bucket, resource_bucket)
        await quack_runtime.start()
        app.state.quack_runtime = quack_runtime
        catalogue_control = CatalogueControl()
        await catalogue_control.start()
        app.state.catalogue_control = catalogue_control
        scheduler_stop = asyncio.Event()
        scheduler_task = asyncio.create_task(
            run_scheduler(
                scheduler_stop,
                catalogue_snapshot_resolver=catalogue_control.latest_snapshot,
            ),
            name="crawl-scheduler",
        )
        yield
    finally:
        if scheduler_stop is not None:
            scheduler_stop.set()
        if scheduler_task is not None:
            scheduler_task.cancel()
            await asyncio.gather(scheduler_task, return_exceptions=True)
        if catalogue_control is not None:
            await catalogue_control.close()
        if quack_runtime is not None:
            await quack_runtime.close()
        await nats_client.drain()


app = FastAPI(title="Atlas API", lifespan=lifespan)
app.include_router(catalogue.router)
app.include_router(catalogue_queries.router)
app.include_router(catalogue_scalar_macros.router)
app.include_router(catalogue_table_macros.router)
app.include_router(catalogue_views.router)
app.include_router(catalogue_materializations.router)
app.include_router(operational_metrics.router)
app.include_router(repository_operations.router)
app.include_router(search.router)
app.include_router(crawl_graphs.router)
app.include_router(crawl_schedules.router)
app.include_router(crawl_schedules.resource_router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(domain_policies.router)
