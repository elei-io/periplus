"""Process-local telemetry. Payloads, native exception text and request URLs never log."""
from __future__ import annotations

import json
import logging
import time
import sys
from uuid import UUID, uuid4
from contextvars import ContextVar
_request_id = ContextVar("telemetry_request_id", default=None)
import traceback
from datetime import UTC, datetime
from prometheus_client import Counter, Gauge, Histogram

DURATION_BUCKETS = (.01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300)
BYTE_BUCKETS = (1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864)
ITEM_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 10000)

class SafeFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record):
        # Existing call sites use static message templates. Arguments may contain
        # SQL, exception messages or object URLs: never interpolate them.
        data = dict(time=datetime.now(UTC).isoformat(), level=record.levelname,
                    service=self.service, logger=record.name,
                    event=str(record.msg) if '/src/periplus/' in record.pathname.replace('\\', '/') or record.name.startswith('periplus') else 'library_event')
        fields = getattr(record, 'telemetry', {})
        if isinstance(fields, dict):
            data.update({key: value for key, value in fields.items()
                         if key in {'operation', 'operation_id', 'outcome', 'code', 'elapsed_ms',
                                    'status', 'route', 'method', 'actor', 'truncated', 'rows', 'bytes', 'attempt', 'request_id'}
                         and isinstance(value, (str, int, float, bool))})
            data.update({key: value for key, value in fields.items()
                         if key in {'batches', 'collections_removed', 'acquisitions_removed', 'more',
                                    'candidates', 'observations_retired', 'requests_retired',
                                    'blocked_observations', 'blocked_requests', 'deferred', 'removed'}
                         and isinstance(value, (int, bool))})
            if fields.get('mode') in ('disabled', 'dry_run', 'purge'):
                data['mode'] = fields['mode']
        if record.exc_info and record.exc_info[0]:
            data['exception_type'] = record.exc_info[0].__name__
            data['frames'] = [{'function': f.name, 'line': f.lineno}
                              for f in traceback.extract_tb(record.exc_info[2])[-12:]]
        return json.dumps(data, ensure_ascii=True)


def configure_logging(service: str, level: str = 'INFO') -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter(service))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger('uvicorn.access').disabled = True
    sys.excepthook = lambda kind, value, tb: logging.getLogger(__name__).critical("process_failed", exc_info=(kind, value, tb))


def event(name: str, **fields) -> None:
    if _request_id.get() is not None:
        fields.setdefault('request_id', _request_id.get())
    logging.getLogger(__name__).info(name, extra={'telemetry': fields})

_requests = Counter('periplus_http_requests_total', 'Completed HTTP requests.', ('service', 'method', 'route', 'status'))
_duration = Histogram('periplus_http_duration_seconds', 'HTTP request duration.', ('service', 'route'), buckets=DURATION_BUCKETS)
_active = Gauge('periplus_http_active_requests', 'Requests executing in this process.', ('service',))

class HttpTelemetry:
    def __init__(self, app, service: str):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] in {'/metrics', '/healthz', '/livez'}:
            return await self.app(scope, receive, send)
        started, status = time.monotonic(), 500
        try:
            request_id = str(UUID(dict(scope.get('headers', [])).get(b'x-request-id', b'').decode('ascii')))
        except (ValueError, UnicodeError):
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        context_token = _request_id.set(request_id)
        method = scope['method'] if scope['method'] in {'GET','POST','PUT','PATCH','DELETE','OPTIONS','HEAD'} else 'OTHER'
        async def respond(message):
            nonlocal status
            if message['type'] == 'http.response.start': status = message['status']
            await send(message)
        _active.labels(self.service).inc()
        try:
            await self.app(scope, receive, respond)
        finally:
            _active.labels(self.service).dec()
            route = getattr(scope.get('route'), 'path', 'unmatched')
            elapsed = time.monotonic() - started
            _requests.labels(self.service, method, route, str(status)).inc()
            _duration.labels(self.service, route).observe(elapsed)
            event('http_finished', operation_id=str(uuid4()), method=method, route=route, status=status, elapsed_ms=elapsed*1000,
                  actor=scope.get('state', {}).get('api_role', 'anonymous'))
            _request_id.reset(context_token)
