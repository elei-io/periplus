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
    crawl_policies,
    graph_runs,
    catalogue_materializations,
    operational_metrics,
    repository_operations,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = CatalogueReadPool(
        catalogue_read_pool_size(),
        threads=catalogue_read_threads(),
        wait_timeout_seconds=CATALOGUE_READ_POOL_WAIT_SECONDS,
    )
    pool.open()
    app.state.catalogue_read_pool = pool
    try:
        yield
    finally:
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
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
app.include_router(crawl_policies.profile_router)
