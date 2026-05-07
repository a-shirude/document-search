from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.tenancy.middleware import PUBLIC_PATHS


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Looks up the bucket lazily from app.state so it can be added at module
    load time (before lifespan runs) without needing the bucket to exist yet."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            # tenant middleware should always run first; if we're here without
            # a tenant the request is already on its way to a 4xx — let it through.
            return await call_next(request)
        bucket = getattr(request.app.state, "bucket", None)
        if bucket is None:
            return await call_next(request)
        allowed, remaining, retry_ms = await bucket.allow(tenant_id)
        if not allowed:
            return JSONResponse(
                {"error": "rate limit exceeded", "retry_after_ms": retry_ms},
                status_code=429,
                headers={"Retry-After": str(max(1, retry_ms // 1000)), "X-RateLimit-Remaining": str(remaining)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
