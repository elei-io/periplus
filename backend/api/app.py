from fastapi import FastAPI

from api.routers import index, scrape, search

app = FastAPI(title="Atlas API")
app.include_router(index.router)
app.include_router(scrape.router)
app.include_router(search.router)
