from fastapi import FastAPI

from api.routers import (
    artifacts,
    crawl,
    crawl_policies,
    crawls,
    data_schemas,
    extract,
    index,
    query_schemas,
    schema,
    search,
    tasks,
    urls,
)

app = FastAPI(title="Atlas API")
app.include_router(artifacts.router)
app.include_router(data_schemas.router)
app.include_router(extract.router)
app.include_router(index.router)
app.include_router(query_schemas.router)
app.include_router(schema.router)
app.include_router(crawl.router)
app.include_router(crawl_policies.router)
app.include_router(crawls.router)
app.include_router(search.router)
app.include_router(tasks.router)
app.include_router(urls.router)
