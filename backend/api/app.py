import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_pool import CatalogueReadPool
from config.performance import (
    CATALOGUE_READ_POOL_WAIT_SECONDS,
    catalogue_read_pool_size,
    catalogue_read_threads,
)
from api.routers import (
    catalogue,
    catalogue_queries,
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
    pool = CatalogueReadPool(
        catalogue_read_pool_size(),
        threads=catalogue_read_threads(),
        wait_timeout_seconds=CATALOGUE_READ_POOL_WAIT_SECONDS,
    )
    pool.open()
    app.state.catalogue_read_pool = pool
    scheduler_stop = asyncio.Event()
    scheduler_task = asyncio.create_task(
        run_scheduler(scheduler_stop), name="crawl-scheduler"
    )
    try:
        yield
    finally:
        scheduler_stop.set()
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        pool.close()


app = FastAPI(title="Atlas API", lifespan=lifespan)
app.include_router(catalogue.router)
app.include_router(catalogue_queries.router)
app.include_router(catalogue_table_macros.router)
app.include_router(catalogue_views.router)
app.include_router(catalogue_materializations.router)
app.include_router(operational_metrics.router)
app.include_router(repository_operations.router)
app.include_router(crawl_graphs.router)
app.include_router(crawl_schedules.router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(domain_policies.router)
