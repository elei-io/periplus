from fastapi import FastAPI

from api.routers import (
    calibrate,
    catalogue,
    crawl,
    crawl_policies,
    data_schemas,
    extract,
    index,
    operational_metrics,
    query_schemas,
    repository_operations,
    schema,
    search,
    tasks,
    task_runs,
)

app = FastAPI(title="Atlas API")
app.include_router(calibrate.router)
app.include_router(catalogue.router)
app.include_router(data_schemas.router)
app.include_router(extract.router)
app.include_router(index.router)
app.include_router(operational_metrics.router)
app.include_router(query_schemas.router)
app.include_router(repository_operations.router)
app.include_router(schema.router)
app.include_router(crawl.router)
app.include_router(crawl_policies.router)
app.include_router(search.router)
app.include_router(tasks.router)
app.include_router(task_runs.router)
