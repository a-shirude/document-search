"""Tenant + auth middleware.

Authentication is a deliberate stub: HMAC of the tenant_id with a shared
secret. Production replacement (OIDC/JWT validation against a JWKS endpoint)
is described in PRODUCTION.md. The interesting property to demonstrate here
is that the request context carries an *immutable* tenant_id that is then
threaded through every downstream call.
"""

from __future__ import annotations

import hmac
import hashlib
import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

log = logging.getLogger(__name__)

PUBLIC_PATHS = {"/healthz", "/readyz", "/docs", "/openapi.json", "/redoc"}


def expected_token(tenant_id: str) -> str:
    return hmac.new(settings.auth_secret.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:32]


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)

        tenant_id = request.headers.get("x-tenant-id")
        auth = request.headers.get("authorization", "")

        if not tenant_id:
            return JSONResponse({"error": "missing X-Tenant-Id header"}, status_code=400)

        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        token = auth.split(" ", 1)[1].strip()

        if not hmac.compare_digest(token, expected_token(tenant_id)):
            return JSONResponse({"error": "invalid token for tenant"}, status_code=403)

        request.state.tenant_id = tenant_id
        return await call_next(request)
