from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_pool import CatalogueReadPool
from config import get_float, get_int
from api.routers import (
    catalogue,
    catalogue_queries,
    catalogue_views,
    crawl_graphs,
    crawl_policies,
    data_schemas,
    graph_runs,
    catalogue_materializations,
    operational_metrics,
    query_schemas,
    repository_operations,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = CatalogueReadPool(
        get_int("ATLAS_CATALOGUE_READ_POOL_SIZE"),
        threads=get_int("ATLAS_CATALOGUE_READ_THREADS"),
        wait_timeout_seconds=get_float("ATLAS_CATALOGUE_READ_POOL_WAIT_SECONDS"),
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
app.include_router(catalogue_views.router)
app.include_router(data_schemas.router)
app.include_router(catalogue_materializations.router)
app.include_router(operational_metrics.router)
app.include_router(query_schemas.router)
app.include_router(repository_operations.router)
app.include_router(crawl_graphs.router)
app.include_router(graph_runs.trigger_router)
app.include_router(graph_runs.router)
app.include_router(crawl_policies.router)
