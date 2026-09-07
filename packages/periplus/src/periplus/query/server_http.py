"""HTTP adapters for the isolated query service."""
from hmac import compare_digest
import logging
import os

import duckdb
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from periplus.query.helpers import QueryHelpers, query_helpers
from periplus.query.errors import query_error
from periplus.query.service import BusyError, PreparedQuery, QueryRequest, QueryResult

router = APIRouter(prefix="/query", tags=["query"])


class QueryAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["path"] == "/healthz" and scope["method"] == "GET"):
            return await self.app(scope, receive, send)
        token = os.environ.get("PERIPLUS_QUERY_API_TOKEN", "")
        if not token or not compare_digest(dict(scope["headers"]).get(b"authorization", b""), f"Bearer {token}".encode()):
            return await JSONResponse({"detail": "A valid query service credential is required."}, status_code=401)(scope, receive, send)
        if scope["path"] == "/query/helpers" and scope["method"] == "GET":
            return await self.app(scope, receive, send)
        if scope["path"] not in {"/query/prep", "/query/exec"} or scope["method"] != "POST":
            return await JSONResponse({"detail": "Not found."}, status_code=404)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 128 * 1024:
                return await JSONResponse({"detail": "Query request exceeds 128 KiB."}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        original_receive = receive
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await original_receive()
        await self.app(scope, bounded_receive, send)


async def _run(request, payload, operation):
    slot = request.app.state.query_slot
    if slot.locked():
        return JSONResponse({"code": "service_busy", "detail": "Query server is busy."}, status_code=429, headers={"Retry-After": "1"})
    try:
        async with slot:
            return await run_in_threadpool(getattr(request.app.state.query_service, operation), payload)
    except (BusyError, TimeoutError, ValueError, duckdb.Error) as exc:
        status, error = query_error(exc)
        logging.getLogger(__name__).warning("Query failed code=%s exception=%s", error.code, type(exc).__name__)
        return JSONResponse(error.model_dump(), status_code=status,
                            headers={"Retry-After": "1"} if status == 429 else None)


@router.post("/prep", response_model=PreparedQuery)
async def prepare(payload: QueryRequest, request: Request):
    return await _run(request, payload, "prepare")


@router.post("/exec", response_model=QueryResult)
async def execute(payload: QueryRequest, request: Request):
    return await _run(request, payload, "execute")


@router.get("/helpers", response_model=QueryHelpers)
async def helpers():
    return query_helpers()
