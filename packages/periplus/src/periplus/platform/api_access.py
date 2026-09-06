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
            (method in {"GET", "POST"} and path == "/coverage-requests")
            or (method == "GET" and re.fullmatch(r"/coverage-requests/[0-9a-fA-F-]{36}", path))
        )
        if role == "public" and not allowed:
            return await JSONResponse(
                {"detail": "This operation requires administrative access."},
                status_code=403,
            )(scope, receive, send)
        scope.setdefault("state", {})["api_role"] = role
        await self.app(scope, receive, send)
