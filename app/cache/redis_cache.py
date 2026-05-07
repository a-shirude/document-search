"""Redis-backed cache for search results and documents.

Uses jittered TTLs to avoid synchronised expiry stampedes. Versioned
invalidation (cacheVer:{tenant}) lets us drop all of a tenant's search
results in O(1) without SCAN.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from typing import Any

import redis.asyncio as redis

from app.config import settings

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, client: redis.Redis) -> None:
        self.r = client

    @classmethod
    def build(cls) -> "Cache":
        return cls(redis.from_url(settings.redis_url, decode_responses=True))

    async def close(self) -> None:
        await self.r.aclose()

    async def ping(self) -> bool:
        try:
            return await self.r.ping()
        except Exception:
            return False

    # ----- search cache -----

    @staticmethod
    def _hash(*parts: Any) -> str:
        h = hashlib.sha1()
        for p in parts:
            h.update(str(p).encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()[:16]

    async def _ver(self, tenant_id: str) -> str:
        v = await self.r.get(f"cacheVer:{tenant_id}")
        return v or "0"

    async def search_key(self, tenant_id: str, query: str, page: int, size: int, tags_csv: str) -> str:
        ver = await self._ver(tenant_id)
        return f"search:{tenant_id}:{ver}:{self._hash(query, page, size, tags_csv)}"

    async def get_search(self, key: str) -> dict[str, Any] | None:
        raw = await self.r.get(key)
        return json.loads(raw) if raw else None

    async def set_search(self, key: str, payload: dict[str, Any]) -> None:
        ttl = settings.search_cache_ttl
        jitter = ttl * settings.search_cache_jitter_pct // 100
        await self.r.set(key, json.dumps(payload), ex=ttl + random.randint(0, max(jitter, 1)))

    async def bump_tenant_version(self, tenant_id: str) -> None:
        """O(1) invalidation: every old search:{tenant}:{oldVer}:* key is now unreachable."""
        await self.r.incr(f"cacheVer:{tenant_id}")

    # ----- document cache -----

    async def get_doc(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        raw = await self.r.get(f"doc:{tenant_id}:{doc_id}")
        return json.loads(raw) if raw else None

    async def set_doc(self, tenant_id: str, doc_id: str, payload: dict[str, Any]) -> None:
        await self.r.set(f"doc:{tenant_id}:{doc_id}", json.dumps(payload), ex=settings.doc_cache_ttl)

    async def del_doc(self, tenant_id: str, doc_id: str) -> None:
        await self.r.delete(f"doc:{tenant_id}:{doc_id}")
