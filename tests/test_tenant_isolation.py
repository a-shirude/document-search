"""The single most important behavioural test: a tenant cannot see another
tenant's documents under any read path (search, get, delete).

Runs against a live API at http://localhost:8000 — start the stack with
`docker compose up` first.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from app.tenancy.middleware import expected_token

BASE = os.environ.get("DSS_BASE", "http://localhost:8000")


def hdr(tenant: str) -> dict[str, str]:
    return {"X-Tenant-Id": tenant, "Authorization": f"Bearer {expected_token(tenant)}"}


@pytest.mark.asyncio
async def test_search_does_not_cross_tenant_boundary():
    a, b = f"t-a-{uuid.uuid4().hex[:6]}", f"t-b-{uuid.uuid4().hex[:6]}"
    secret_marker = f"marker-{uuid.uuid4().hex}"

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as c:
        r = await c.post("/documents", params={"sync": "true"}, headers=hdr(a),
                         json={"title": secret_marker, "body": secret_marker, "tags": ["secret"]})
        assert r.status_code == 201
        doc_id = r.json()["id"]

        r = await c.get("/search", params={"q": secret_marker}, headers=hdr(a))
        assert r.status_code == 200
        assert r.json()["total"] == 1, "tenant A must see its own document"

        r = await c.get("/search", params={"q": secret_marker}, headers=hdr(b))
        assert r.status_code == 200
        assert r.json()["total"] == 0, "tenant B must NOT see tenant A's document"

        r = await c.get(f"/documents/{doc_id}", headers=hdr(b))
        assert r.status_code == 404, "tenant B must NOT be able to fetch tenant A's document by id"

        r = await c.delete(f"/documents/{doc_id}", headers=hdr(b))
        assert r.status_code == 404, "tenant B must NOT be able to delete tenant A's document"


@pytest.mark.asyncio
async def test_missing_tenant_or_token_is_rejected():
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as c:
        r = await c.get("/search", params={"q": "x"})
        assert r.status_code == 400  # missing tenant header
        r = await c.get("/search", params={"q": "x"}, headers={"X-Tenant-Id": "t"})
        assert r.status_code == 401  # missing bearer
        r = await c.get("/search", params={"q": "x"},
                        headers={"X-Tenant-Id": "t", "Authorization": "Bearer wrong"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_health_endpoints_are_public():
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as c:
        assert (await c.get("/healthz")).status_code == 200
        # /readyz can be 200 or 503; either way it's reachable without auth.
        assert (await c.get("/readyz")).status_code in (200, 503)
