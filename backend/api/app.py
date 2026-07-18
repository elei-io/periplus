import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_control import CatalogueControl
from repository.catalogue.browser_runtime import browser_quack_runtime_from_env
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
)
from runtime.crawl_scheduler import run_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail startup if the browser cannot be given a complete Quack runtime.
    browser_quack_runtime_from_env()
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
    try:
        yield
    finally:
        scheduler_stop.set()
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        await catalogue_control.close()


app = FastAPI(title="Atlas API", lifespan=lifespan)
app.include_router(catalogue.router)
app.include_router(catalogue_queries.router)
app.include_router(catalogue_scalar_macros.router)
app.include_router(catalogue_table_macros.router)
app.include_router(catalogue_views.router)
app.include_router(catalogue_materializations.router)
app.include_router(operational_metrics.router)
app.include_router(repository_operations.router)
app.include_router(crawl_graphs.router)
app.include_router(crawl_schedules.router)
app.include_router(crawl_schedules.resource_router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(domain_policies.router)
