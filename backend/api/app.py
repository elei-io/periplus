from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.catalogue_pool import CatalogueReadPool
from config import get_float, get_int
from api.routers import (
    calibrate,
    catalogue,
    catalogue_queries,
    catalogue_views,
    crawl,
    crawl_policies,
    data_schemas,
    extract,
    index,
    materialized_views,
    operational_metrics,
    query_schemas,
    repository_operations,
    schema,
    search,
    tasks,
    task_runs,
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
app.include_router(calibrate.router)
app.include_router(catalogue.router)
app.include_router(catalogue_queries.router)
app.include_router(catalogue_views.router)
app.include_router(data_schemas.router)
app.include_router(extract.router)
app.include_router(index.router)
app.include_router(materialized_views.router)
app.include_router(operational_metrics.router)
app.include_router(query_schemas.router)
app.include_router(repository_operations.router)
app.include_router(schema.router)
app.include_router(crawl.router)
app.include_router(crawl_policies.router)
app.include_router(search.router)
app.include_router(tasks.router)
app.include_router(task_runs.router)
