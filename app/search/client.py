"""Thin Elasticsearch wrapper.

Two design properties this module enforces by construction:
1. tenant_id is a *required* argument on every read/write — handlers cannot
   accidentally issue a cross-tenant query because the wrapper builds the
   filter, not the caller.
2. _routing is set to tenant_id on every operation, turning multi-shard
   scatter-gather into a single-shard query.
"""

from __future__ import annotations

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError

from app.config import settings
from app.search.mapping import INDEX_SETTINGS

log = logging.getLogger(__name__)


class SearchClient:
    def __init__(self, es: AsyncElasticsearch, index: str) -> None:
        self._es = es
        self._index = index

    @classmethod
    def build(cls) -> "SearchClient":
        es = AsyncElasticsearch(settings.es_url, request_timeout=5, retry_on_timeout=True, max_retries=2)
        return cls(es, settings.index_name)

    async def close(self) -> None:
        await self._es.close()

    async def ensure_index(self) -> None:
        if not await self._es.indices.exists(index=self._index):
            await self._es.indices.create(index=self._index, body=INDEX_SETTINGS)
            log.info("created index", extra={"index": self._index})

    async def ping(self) -> bool:
        try:
            return await self._es.ping()
        except Exception:
            return False

    async def index_doc(self, *, tenant_id: str, doc_id: str, body: dict[str, Any]) -> None:
        # `refresh="wait_for"` makes this call return only once the doc is visible
        # to search. This is what the sync write path wants — read-your-writes —
        # at the cost of a small (≤ refresh_interval) added latency.
        body = {**body, "tenant_id": tenant_id}
        await self._es.index(
            index=self._index,
            id=doc_id,
            routing=tenant_id,
            document=body,
            refresh="wait_for",
        )

    async def bulk_index(self, *, tenant_id: str, items: list[tuple[str, dict[str, Any]]]) -> tuple[int, list[str]]:
        """Bulk index. Returns (succeeded, list_of_failed_ids)."""
        if not items:
            return 0, []
        ops: list[dict[str, Any]] = []
        for doc_id, body in items:
            ops.append({"index": {"_index": self._index, "_id": doc_id, "routing": tenant_id}})
            ops.append({**body, "tenant_id": tenant_id})
        resp = await self._es.bulk(operations=ops, refresh=False)
        failed: list[str] = []
        for entry in resp.get("items", []):
            action = next(iter(entry.values()))
            if action.get("error"):
                failed.append(action.get("_id", "?"))
        return len(items) - len(failed), failed

    async def get_doc(self, *, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        try:
            resp = await self._es.get(index=self._index, id=doc_id, routing=tenant_id)
        except NotFoundError:
            return None
        src = resp["_source"]
        if src.get("tenant_id") != tenant_id:
            # Defence in depth: routing collision could in theory return a doc
            # belonging to another tenant on the same shard. Re-check and refuse.
            return None
        return {"id": resp["_id"], **src}

    async def delete_doc(self, *, tenant_id: str, doc_id: str) -> bool:
        try:
            await self._es.delete(index=self._index, id=doc_id, routing=tenant_id)
            return True
        except NotFoundError:
            return False

    async def search(
        self,
        *,
        tenant_id: str,
        query: str,
        page: int = 1,
        size: int = 10,
        tags: list[str] | None = None,
        fuzzy: bool = False,
        highlight: bool = True,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        if query:
            must.append(
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^2", "body"],
                        "fuzziness": "AUTO" if fuzzy else "0",
                    }
                }
            )
        else:
            must.append({"match_all": {}})

        filters: list[dict[str, Any]] = [{"term": {"tenant_id": tenant_id}}]
        if tags:
            filters.append({"terms": {"tags": tags}})

        body: dict[str, Any] = {
            "from": max(0, (page - 1) * size),
            "size": min(max(size, 1), 100),
            "query": {"bool": {"must": must, "filter": filters}},
            "_source": ["title", "body", "tags", "created_at", "tenant_id"],
        }
        if highlight:
            body["highlight"] = {
                "fields": {"title": {}, "body": {"fragment_size": 120, "number_of_fragments": 1}},
                "pre_tags": ["<em>"],
                "post_tags": ["</em>"],
            }

        resp = await self._es.search(index=self._index, body=body, routing=tenant_id)
        hits = []
        for h in resp["hits"]["hits"]:
            src = h["_source"]
            snippet = (src.get("body") or "")[:160]
            hits.append(
                {
                    "id": h["_id"],
                    "score": h["_score"],
                    "title": src.get("title"),
                    "snippet": snippet,
                    "tags": src.get("tags", []),
                    "highlight": h.get("highlight", {}),
                }
            )
        total = resp["hits"]["total"]["value"]
        return {"total": total, "page": page, "size": size, "hits": hits, "took_ms": resp.get("took")}
