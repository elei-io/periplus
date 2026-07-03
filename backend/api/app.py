from fastapi import FastAPI

from api.routers import search

app = FastAPI(title="Atlas API")
app.include_router(search.router)
