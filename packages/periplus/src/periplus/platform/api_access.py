"""Service credentials and the public client's infrastructure capabilities."""

from hmac import compare_digest
import re

from starlette.responses import JSONResponse

from periplus.platform.config.environment import get_optional


class ApiAccessMiddleware:
    """Deny by default; no user identity crosses the infrastructure boundary."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        if path in {"/healthz", "/metrics"} and method == "GET":
            return await self.app(scope, receive, send)
        # The query token grants only append access, never control or history reads.
        if path == "/internal/query-history" and method == "POST":
            token = get_optional("PERIPLUS_QUERY_API_TOKEN")
            if not token or not compare_digest(dict(scope["headers"]).get(b"authorization", b""), f"Bearer {token}".encode()):
                return await JSONResponse({"detail": "Internal service credential required."}, status_code=401)(scope, receive, send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 1024 * 1024:
                    return await JSONResponse({"detail": "History record exceeds 1 MiB."}, status_code=413)(scope, receive, send)
                if not message.get("more_body", False):
                    break
            original_receive, delivered = receive, False
            async def history_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()
            scope.setdefault("state", {})["api_role"] = "query"
            return await self.app(scope, history_receive, send)
        admin = get_optional("PERIPLUS_ADMIN_API_TOKEN")
        public = get_optional("PERIPLUS_PUBLIC_API_TOKEN")
        if not admin or not public or compare_digest(admin, public):
            return await JSONResponse(
                {"detail": "API credentials are not configured."}, status_code=503
            )(scope, receive, send)
        headers = dict(scope["headers"])
        authorization = headers.get(b"authorization", b"")
        role = None
        if compare_digest(authorization, f"Bearer {admin}".encode()):
            role = "admin"
        elif compare_digest(authorization, f"Bearer {public}".encode()):
            role = "public"
        if role is None:
            return await JSONResponse(
                {"detail": "A valid service credential is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
        allowed = (
            (method == "GET" and path == "/access")
            or (method == "POST" and path in {"/access/admit/assistant", "/access/admit/sql"})
            or
            (method in {"GET", "POST"} and path == "/collections")
            or (method == "GET" and path in {"/collections/history", "/frontier/live", "/frontier/captures"})
            or (method == "GET" and re.fullmatch(r"/collections/[0-9a-fA-F-]{36}(?:/(?:items|arrivals))?", path))
            or (method == "GET" and re.fullmatch(r"/frontier/items/[0-9a-fA-F-]{36}", path))
            or (method == "GET" and re.fullmatch(r"/frontier/observations/[0-9a-fA-F-]{36}/lineage", path))
        )
        if role == "public" and not allowed:
            return await JSONResponse(
                {"detail": "This operation requires administrative access."},
                status_code=403,
            )(scope, receive, send)
        if path in {"/collections", "/admin/sql/exec"} and method == "POST":
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 512 * 1024:
                    return await JSONResponse({"detail": "Request exceeds 512 KiB."}, status_code=413)(scope, receive, send)
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
            receive = bounded_receive
        scope.setdefault("state", {})["api_role"] = role
        await self.app(scope, receive, send)
