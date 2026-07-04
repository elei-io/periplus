from fastapi import FastAPI

from api.routers import extract, index, schema, scrape, search, tasks

app = FastAPI(title="Atlas API")
app.include_router(extract.router)
app.include_router(index.router)
app.include_router(schema.router)
app.include_router(scrape.router)
app.include_router(search.router)
app.include_router(tasks.router)
