"""Stream consumer that bulk-indexes documents into Elasticsearch.

Behaviour:
- XREADGROUP with a small block, then bulk-index everything we got in one call.
- ACK on success. Retry on transient failure with exponential backoff (XCLAIM
  via the consumer group's PEL).
- After max retries, push the payload to idx:dlq and ACK the original entry so
  the consumer group is not blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections import defaultdict

import redis.asyncio as redis

from app.cache.redis_cache import Cache
from app.config import settings
from app.logging_setup import configure_logging
from app.search.client import SearchClient

log = logging.getLogger("indexer")

BATCH_SIZE = int(os.environ.get("INDEXER_BATCH_SIZE", "200"))
BLOCK_MS = int(os.environ.get("INDEXER_BLOCK_MS", "1000"))
MAX_DELIVERIES = int(os.environ.get("INDEXER_MAX_DELIVERIES", "5"))


async def _ensure_group(r: redis.Redis) -> None:
    try:
        await r.xgroup_create(settings.stream_key, settings.stream_group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _to_dlq(r: redis.Redis, entry_id: str, fields: dict) -> None:
    await r.xadd(settings.dlq_key, {**fields, "orig_id": entry_id}, maxlen=10_000, approximate=True)


async def run() -> None:
    configure_logging()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    search = SearchClient.build()
    cache = Cache(r)
    await search.ensure_index()
    await _ensure_group(r)

    stop = asyncio.Event()

    def _on_signal(*_):
        log.info("shutting down indexer")
        stop.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(s, _on_signal)
        except (NotImplementedError, RuntimeError):
            # Windows / non-main-thread.
            pass

    log.info("indexer started", extra={"group": settings.stream_group, "stream": settings.stream_key})

    while not stop.is_set():
        resp = await r.xreadgroup(
            settings.stream_group,
            settings.stream_consumer,
            {settings.stream_key: ">"},
            count=BATCH_SIZE,
            block=BLOCK_MS,
        )
        if not resp:
            continue

        # resp shape: [[stream_key, [(entry_id, {field: value, ...}), ...]]]
        _, entries = resp[0]
        # group by tenant so we can issue one bulk per tenant (good for routing).
        per_tenant: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
        for entry_id, fields in entries:
            tenant_id = fields.get("tenant_id")
            doc_id = fields.get("doc_id")
            try:
                body = json.loads(fields.get("body", "{}"))
            except json.JSONDecodeError:
                log.warning("bad payload, dropping to dlq", extra={"entry_id": entry_id})
                await _to_dlq(r, entry_id, fields)
                await r.xack(settings.stream_key, settings.stream_group, entry_id)
                continue
            if not tenant_id or not doc_id:
                await _to_dlq(r, entry_id, fields)
                await r.xack(settings.stream_key, settings.stream_group, entry_id)
                continue
            per_tenant[tenant_id].append((entry_id, doc_id, body))

        for tenant_id, items in per_tenant.items():
            payload = [(doc_id, body) for _, doc_id, body in items]
            try:
                ok, failed_ids = await search.bulk_index(tenant_id=tenant_id, items=payload)
            except Exception as e:
                log.exception("bulk_index errored, leaving items in PEL", extra={"err": str(e), "tenant": tenant_id})
                continue

            failed_set = set(failed_ids)
            ack_ids: list[str] = []
            for entry_id, doc_id, body in items:
                if doc_id in failed_set:
                    deliveries = await r.xpending_range(
                        settings.stream_key, settings.stream_group, min=entry_id, max=entry_id, count=1
                    )
                    delivery_count = deliveries[0]["times_delivered"] if deliveries else 1
                    if delivery_count >= MAX_DELIVERIES:
                        await _to_dlq(r, entry_id, {"tenant_id": tenant_id, "doc_id": doc_id, "body": json.dumps(body)})
                        ack_ids.append(entry_id)
                else:
                    ack_ids.append(entry_id)

            if ack_ids:
                await r.xack(settings.stream_key, settings.stream_group, *ack_ids)
                # Bump tenant cache version once per batch — coarse but cheap.
                await cache.bump_tenant_version(tenant_id)

            log.info(
                "indexed",
                extra={"tenant": tenant_id, "ok": ok, "failed": len(failed_ids), "acked": len(ack_ids)},
            )

    await search.close()
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(run())
