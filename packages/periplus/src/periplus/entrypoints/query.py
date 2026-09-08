"""Query-only process: no control database, NATS, or writer composition."""
import asyncio
from contextlib import asynccontextmanager
from periplus.platform.telemetry import configure_logging, HttpTelemetry
import os

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from periplus.platform.catalogue.config import catalogue_config_from_env
from periplus.query.server_http import QueryAccessMiddleware, router
from periplus.query.service import QueryService
from periplus.query.limits import QueryLimitsClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("PERIPLUS_QUERY_API_TOKEN"):
        raise RuntimeError("PERIPLUS_QUERY_API_TOKEN is required")
    configure_logging("query")
    service = await run_in_threadpool(QueryService, catalogue_config_from_env())
    from periplus.query.history import HistoryClient
    app.state.query_history = HistoryClient()
    app.state.query_limits = QueryLimitsClient()
    app.state.query_service = service
    app.state.query_slot = asyncio.Semaphore(1)
    try:
        yield
    finally:
        await app.state.query_limits.close()
        await app.state.query_history.close()
        await run_in_threadpool(service.close)


app = FastAPI(title="Periplus Query", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(QueryAccessMiddleware)
app.include_router(router)


@app.get("/healthz")
async def healthz(request: Request):
    if not request.app.state.query_service.healthy:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}

app.add_middleware(HttpTelemetry, service="query")

from periplus.operations.api.metrics import router as metrics_router
app.include_router(metrics_router)
