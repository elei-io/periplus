from fastapi import FastAPI

from api.routers import index, search

app = FastAPI(title="Atlas API")
app.include_router(index.router)
app.include_router(search.router)
