"""Redis Streams producer used by the API on the async write path."""

from __future__ import annotations

import json
import uuid

import redis.asyncio as redis

from app.config import settings


class IndexQueue:
    def __init__(self, client: redis.Redis) -> None:
        self.r = client

    @classmethod
    def build(cls) -> "IndexQueue":
        return cls(redis.from_url(settings.redis_url, decode_responses=True))

    async def close(self) -> None:
        await self.r.aclose()

    async def enqueue(self, *, tenant_id: str, doc_id: str | None, body: dict) -> str:
        doc_id = doc_id or uuid.uuid4().hex
        payload = {"tenant_id": tenant_id, "doc_id": doc_id, "body": json.dumps(body)}
        await self.r.xadd(settings.stream_key, payload, maxlen=100_000, approximate=True)
        return doc_id

    async def ensure_group(self) -> None:
        try:
            await self.r.xgroup_create(settings.stream_key, settings.stream_group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
