from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.api.schemas import DocumentIn, IndexAccepted, SearchResponse
from app.cache.redis_cache import Cache
from app.config import settings
from app.indexer.stream import IndexQueue
from app.logging_setup import configure_logging
from app.ratelimit.middleware import RateLimitMiddleware
from app.ratelimit.token_bucket import TokenBucket
from app.search.client import SearchClient
from app.tenancy.middleware import TenantMiddleware

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.search = SearchClient.build()
    app.state.cache = Cache.build()
    app.state.queue = IndexQueue.build()
    app.state.bucket = TokenBucket(
        app.state.cache.r, capacity=settings.rate_limit_burst, rate=settings.rate_limit_rps
    )
    # Best-effort: create the index + stream group on startup.
    try:
        await app.state.search.ensure_index()
    except Exception as e:
        log.warning("ensure_index failed at startup", extra={"err": str(e)})
    try:
        await app.state.queue.ensure_group()
    except Exception as e:
        log.warning("ensure_group failed at startup", extra={"err": str(e)})

    yield
    await app.state.search.close()
    await app.state.queue.close()
    await app.state.cache.close()


app = FastAPI(title="Distributed Document Search", version="0.1.0", lifespan=lifespan)
# Middleware order matters: in Starlette, the last-added middleware wraps
# the others, so it runs FIRST on the request path. We want Tenant first
# (to set state.tenant_id) and RateLimit second — so add RateLimit first,
# Tenant second.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantMiddleware)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse({"error": "internal_error"}, status_code=500)


# ---------- health ----------


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    es_ok = await request.app.state.search.ping()
    redis_ok = await request.app.state.cache.ping()
    ok = es_ok and redis_ok
    body = {"status": "ok" if ok else "degraded", "elasticsearch": es_ok, "redis": redis_ok}
    return JSONResponse(body, status_code=200 if ok else 503)


# ---------- documents ----------


@app.post("/documents", response_model=IndexAccepted, status_code=202)
async def index_document(
    payload: DocumentIn,
    request: Request,
    sync: bool = Query(False, description="If true, index synchronously (returns 201)."),
):
    tenant_id: str = request.state.tenant_id
    doc_id = payload.id or uuid.uuid4().hex
    body = {
        "title": payload.title,
        "body": payload.body,
        "tags": payload.tags,
        "acl": payload.acl,
        "created_at": (payload.created_at or datetime.now(timezone.utc)).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if sync:
        await request.app.state.search.index_doc(tenant_id=tenant_id, doc_id=doc_id, body=body)
        await request.app.state.cache.bump_tenant_version(tenant_id)
        await request.app.state.cache.del_doc(tenant_id, doc_id)
        return JSONResponse({"id": doc_id, "status": "indexed"}, status_code=201)

    await request.app.state.queue.enqueue(tenant_id=tenant_id, doc_id=doc_id, body=body)
    return {"id": doc_id, "status": "queued"}


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request):
    tenant_id: str = request.state.tenant_id
    cached = await request.app.state.cache.get_doc(tenant_id, doc_id)
    if cached:
        cached["_cache"] = "hit"
        return cached
    doc = await request.app.state.search.get_doc(tenant_id=tenant_id, doc_id=doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="not found")
    await request.app.state.cache.set_doc(tenant_id, doc_id, doc)
    doc["_cache"] = "miss"
    return doc


@app.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, request: Request):
    tenant_id: str = request.state.tenant_id
    deleted = await request.app.state.search.delete_doc(tenant_id=tenant_id, doc_id=doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="not found")
    await request.app.state.cache.del_doc(tenant_id, doc_id)
    await request.app.state.cache.bump_tenant_version(tenant_id)
    return None


# ---------- search ----------


@app.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: str = Query("", description="Query string"),
    page: int = Query(1, ge=1, le=1000),
    size: int = Query(10, ge=1, le=100),
    tags: list[str] | None = Query(None),
    fuzzy: bool = Query(False),
    highlight: bool = Query(True),
    # `tenant` is also accepted as a query param per the brief; X-Tenant-Id wins.
    tenant: str | None = Query(None),
):
    tenant_id: str = request.state.tenant_id
    if tenant and tenant != tenant_id:
        raise HTTPException(status_code=400, detail="tenant query param does not match X-Tenant-Id")

    tags_csv = ",".join(sorted(tags)) if tags else ""
    cache_key = await request.app.state.cache.search_key(tenant_id, q, page, size, tags_csv + f"|f={fuzzy}|h={highlight}")
    cached = await request.app.state.cache.get_search(cache_key)
    if cached:
        cached["cache"] = "hit"
        return cached

    result = await request.app.state.search.search(
        tenant_id=tenant_id, query=q, page=page, size=size, tags=tags, fuzzy=fuzzy, highlight=highlight
    )
    result["cache"] = "miss"
    await request.app.state.cache.set_search(cache_key, result)
    return result
