"""HTTP adapters for the isolated query service."""
from hmac import compare_digest
import logging
import os
from prometheus_client import Counter
from periplus.platform.telemetry import event

_query_outcomes = Counter("periplus_query_operations_total", "Query operation outcomes including rejection.", ("operation", "outcome"))

import duckdb
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from periplus.query.helpers import QueryHelpers, query_helpers
from periplus.query.errors import query_error
from periplus.query.limits import QueryLimitsUnavailable
from periplus.query.service import BusyError, PreparedQuery, QueryRequest, QueryResult

router = APIRouter(prefix="/query", tags=["query"])


class QueryAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["path"] in {"/healthz", "/metrics"} and scope["method"] == "GET"):
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
            if len(body) > 16 * 1024 * 1024:
                return await JSONResponse({"detail": "Query request exceeds the 16 MiB transport ceiling."}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        scope.setdefault("state", {})["query_request_bytes"] = len(body)
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
    from periplus.query.history import track, result_fields
    async with track(request, payload, operation) as record:
        from periplus.operations.query_history.schemas import PreparationEvidence
        evidence = PreparationEvidence(compiler_version=request.app.state.query_service.compiler_version)
        try:
            result = await _run_operation(request, payload, operation, evidence)
        finally:
            record.update(evidence.model_dump())
        if isinstance(result, JSONResponse):
            import json
            code = json.loads(result.body).get("code", "internal_error")
            record.update(outcome="timeout" if result.status_code == 408 else "rejected" if result.status_code < 500 else "failed", error_code=code)
        else:
            record.update(result_fields(result))
        return result


async def _run_operation(request, payload, operation, evidence):
    slot = request.app.state.query_slot
    if slot.locked():
        _query_outcomes.labels(operation, "service_busy").inc()
        return JSONResponse({"code": "service_busy", "detail": "Query server is busy."}, status_code=429, headers={"Retry-After": "1"})
    try:
        async with slot:
            limits = await request.app.state.query_limits.read()
            denial = _input_denial(request, payload, limits)
            if denial is not None:
                _query_outcomes.labels(operation, "input_limit").inc()
                return denial
            result = await run_in_threadpool(getattr(request.app.state.query_service, operation), payload, limits=limits, evidence=evidence)
            _query_outcomes.labels(operation, "success").inc()
            return result
    except QueryLimitsUnavailable:
        _query_outcomes.labels(operation, "access_unavailable").inc()
        return JSONResponse({"code": "access_unavailable", "detail": "Query limits are temporarily unavailable."}, status_code=503)
    except (BusyError, TimeoutError, ValueError, duckdb.Error) as exc:
        status, error = query_error(exc)
        _query_outcomes.labels(operation, error.code).inc()
        event("query_failed", operation=operation, code=error.code)
        if status >= 500:
            # SafeFormatter retains the exception class and frame locations only.
            # Native messages can contain lake URLs or credentials.
            logging.getLogger(__name__).warning("query_engine_failure", exc_info=True)
        return JSONResponse(error.model_dump(), status_code=status,
                            headers={"Retry-After": "1"} if status == 429 else None)


@router.post("/prep", response_model=PreparedQuery)
async def prepare(payload: QueryRequest, request: Request):
    return await _run(request, payload, "prepare")


@router.post("/exec", response_model=QueryResult)
async def execute(payload: QueryRequest, request: Request):
    from periplus.query.streaming import MEDIA_TYPE, QueryStreamResponse
    if MEDIA_TYPE in request.headers.get("accept", ""):
        slot = request.app.state.query_slot
        if slot.locked():
            return await _stream_denial(request, payload, JSONResponse({"code": "service_busy", "detail": "Query server is busy."}, status_code=429,
                                headers={"Retry-After": "1"}))
        await slot.acquire()
        owned = True
        try:
            limits = await request.app.state.query_limits.read()
            denial = _input_denial(request, payload, limits)
            if denial is not None:
                slot.release()
                owned = False
                return await _stream_denial(request, payload, denial)
            return QueryStreamResponse(request, payload, limits)
        except QueryLimitsUnavailable:
            slot.release()
            owned = False
            return await _stream_denial(request, payload, JSONResponse({"code": "access_unavailable", "detail": "Query limits are temporarily unavailable."}, status_code=503))
        except BaseException:
            if owned:
                slot.release()
            raise
    return await _run(request, payload, "execute")


@router.get("/helpers", response_model=QueryHelpers)
async def helpers():
    return query_helpers()


def _input_denial(request, payload, limits):
    size = getattr(request.state, "query_request_bytes", None)
    if size is None:
        size = len(payload.model_dump_json().encode())
    if size > limits.max_request_bytes:
        return JSONResponse({"code": "request_limit", "detail": f"Query request exceeds max_request_bytes ({limits.max_request_bytes} bytes)."}, status_code=413)
    pending = list(payload.parameters)
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        if count > limits.max_parameter_values:
            return JSONResponse({"code": "parameter_limit", "detail": f"Query parameters exceed max_parameter_values ({limits.max_parameter_values})."}, status_code=413)
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    return None


async def _stream_denial(request, payload, response):
    import json
    from periplus.query.history import track
    code = json.loads(response.body)["code"]
    _query_outcomes.labels("execute", code).inc()
    async with track(request, payload, "execute") as record:
        record.update(outcome="rejected" if response.status_code < 500 else "failed", error_code=code)
    return response
