"""Query-only process: no control database, NATS, or writer composition."""
import asyncio
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from periplus.platform.catalogue.config import catalogue_config_from_env
from periplus.query.server_http import QueryAccessMiddleware, router
from periplus.query.service import QueryService


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("PERIPLUS_QUERY_API_TOKEN"):
        raise RuntimeError("PERIPLUS_QUERY_API_TOKEN is required")
    logging.basicConfig(level=logging.INFO)
    service = await run_in_threadpool(QueryService, catalogue_config_from_env())
    app.state.query_service = service
    app.state.query_slot = asyncio.Semaphore(1)
    try:
        yield
    finally:
        await run_in_threadpool(service.close)


app = FastAPI(title="Periplus Query", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(QueryAccessMiddleware)
app.include_router(router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
