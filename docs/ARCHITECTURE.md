# Architecture — Distributed Document Search Service (DSS)

## 1. Goals & non-functional targets

| Target | Value | How the design meets it |
|---|---|---|
| Corpus size | 10M+ docs across many tenants | Sharded Elasticsearch (ES); shared index with `_routing` keyed on `tenant_id` so any one tenant's docs land on a single primary shard. |
| Search p95 | < 500 ms | Two-tier cache (Redis search-result cache → ES); single-shard search per tenant via routing; BM25 only — no rescoring on the prototype. |
| Throughput | 1k+ concurrent QPS | Stateless API tier scaled horizontally behind a load balancer; cache hit ratio target ≥ 70% on hot tenants; ES read replicas. |
| Tenant isolation | Hard data + noisy-neighbour | Mandatory `tenant_id` filter injected by middleware (defence-in-depth: query builder *and* doc-level security); per-tenant Redis-backed token bucket. |
| Horizontal scale | Linear with shards | Stateless API; ES rebalance on shard add; partitioned Redis cluster path documented in `PRODUCTION.md`. |

## 2. High-level architecture

```
                          ┌─────────────────────┐
                          │   Clients / SDKs    │
                          └──────────┬──────────┘
                                     │ HTTPS
                          ┌──────────▼──────────┐
                          │  LB / API Gateway   │   (nginx in compose; ALB/Envoy in prod)
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
     ┌────────▼────────┐    ┌────────▼────────┐    ┌────────▼────────┐
     │  FastAPI pod 1  │    │  FastAPI pod 2  │ …  │  FastAPI pod N  │   stateless
     └───┬────┬────┬───┘    └───┬────┬────┬───┘    └───┬────┬────┬───┘
         │    │    │            │    │    │            │    │    │
   ┌─────▼┐ ┌─▼──┐ ┌▼────────┐ (rate-limit + cache + tenant middleware on every pod)
   │Redis │ │ES  │ │Redis    │
   │cache │ │R/W │ │Streams  │◄── async index queue
   └──────┘ └────┘ └────┬────┘
                        │
                ┌───────▼────────┐
                │ Indexer worker │  (consumer group; bulk-writes to ES)
                └───────┬────────┘
                        │
                  ┌─────▼─────┐
                  │   ES      │   shared index `documents`
                  │  cluster  │   shards routed by tenant_id
                  └───────────┘
```

## 3. Data flow

### Indexing (write path)
```
POST /documents
   │  (X-Tenant-Id header validated)
   ▼
[middleware: auth → tenant → rate-limit]
   │
   ▼
publish to Redis Stream  `idx:stream`     ── 202 Accepted ──► client
   │                                          (id returned immediately)
   ▼
indexer worker (consumer group `idx-cg`)
   │
   ▼
ES bulk index with `_routing = tenant_id`
   │
   ▼
invalidate cache keys: `search:{tenant}:*`  (sampled — see §6)
```

The 202-then-async path keeps p99 write latency low and decouples ingest spikes from ES. Synchronous index is also exposed (`POST /documents?sync=true`) for tests and small batches.

### Search (read path)
```
GET /search?q=...&tenant=...
   │
   ▼
[middleware: auth → tenant → rate-limit]
   │
   ▼
cache GET `search:{tenant}:{sha1(q+filters+page)}`
   │ hit                                  │ miss
   ▼                                      ▼
return cached page                ES query (with tenant_id filter + routing)
                                          │
                                          ▼
                                  cache SET (TTL 60s, jittered)
                                          │
                                          ▼
                                       respond
```

## 4. Storage strategy

**Search engine — Elasticsearch.** Purpose-built inverted index, BM25 ranking, and first-class support for highlighting / fuzzy / faceting (the bonus features). PostgreSQL FTS was considered and rejected: it works to ~1–10M rows but degrades on multi-field analyzers, doesn't shard naturally, and gives weaker relevance tuning controls.

**Index design.** *Single shared index* `documents`, sharded N ways, with `_routing = tenant_id`. This means:

- Each tenant's docs co-locate on one primary shard → search becomes a **single-shard query** instead of scatter-gather. This is the single biggest p95 lever.
- Operationally we manage one mapping, not thousands of indices. Index-per-tenant breaks at >~1k tenants because each shard carries fixed memory overhead in ES.
- Trade-off: a hot "whale" tenant can saturate one shard. Mitigation in production: a hybrid model — whales graduate to a dedicated index; the long tail stays in the shared index. This is documented in `PRODUCTION.md` but not built in the prototype.

**Mapping** (excerpt — full version in `app/search/mapping.py`):

```json
{
  "settings": {"number_of_shards": 6, "number_of_replicas": 1},
  "mappings": {
    "properties": {
      "tenant_id":  {"type": "keyword"},
      "title":      {"type": "text", "analyzer": "english"},
      "body":       {"type": "text", "analyzer": "english"},
      "tags":       {"type": "keyword"},
      "created_at": {"type": "date"},
      "acl":        {"type": "keyword"}
    }
  }
}
```

**Document source of truth.** ES stores the source. In production we'd add Postgres (or S3 + a metadata table) as the system of record so we can rebuild the index from cold on a schema change — called out in `PRODUCTION.md`.

## 5. API contract (key endpoints)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/documents` | Index a document (default async, `?sync=true` for inline). 202 / 201. |
| `GET`  | `/documents/{id}` | Retrieve document. Cache `doc:{tenant}:{id}`. |
| `DELETE` | `/documents/{id}` | Remove document. Invalidates doc + search caches. |
| `GET`  | `/search` | Query params: `q`, `page`, `size`, `tags`, `highlight`, `fuzzy`. |
| `GET`  | `/healthz` | Liveness. |
| `GET`  | `/readyz` | Deep readiness — pings ES + Redis. |

All requests **must** carry `X-Tenant-Id` and `Authorization: Bearer <token>`. Examples: `docs/sample_requests.md` and the Postman collection at the repo root.

```jsonc
// POST /documents response
{ "id": "9f3...", "status": "queued" }

// GET /search response
{
  "took_ms": 12,
  "cache": "miss",
  "total": 1342,
  "page": 1, "size": 10,
  "hits": [
    { "id": "9f3...", "score": 7.81,
      "title": "...", "snippet": "...",
      "highlight": ["...<em>query</em>..."] }
  ]
}
```

## 6. Caching strategy (multi-layer)

| Layer | Key | TTL | Invalidation |
|---|---|---|---|
| L1 — search results | `search:{tenant}:{sha1(q+filters+page+size)}` | 60 s + 10% jitter | Sampled invalidation on writes (`del search:{tenant}:*` is too coarse on hot tenants — production uses a per-tenant version counter; see below). |
| L2 — document by id | `doc:{tenant}:{id}` | 5 min | Explicit `DEL` on PUT/DELETE. |
| L3 — ES request cache | (built-in) | per-segment | Auto-invalidated on segment merge. |

**Cache stampede.** Documented as a production concern. Approach: probabilistic early expiration (XFetch) or singleflight per cache key. Not built in the prototype — flagged in `PRODUCTION.md`.

**Versioned invalidation (production).** Maintain `cacheVer:{tenant}` integer in Redis; bake it into the search cache key. On any write to that tenant, `INCR cacheVer:{tenant}` — every old key becomes unreachable, no scan required, O(1) invalidation.

## 7. Consistency model

- **Reads from cache are eventually consistent** (≤ 60 s + indexer lag). Acceptable for search; this is industry-standard.
- **`GET /documents/{id}` is read-your-writes** when called with `?sync=true` on the write, or after the indexer ack. The async path is at-least-once delivery via Redis Streams consumer groups; ES upserts are idempotent on `_id`.
- **Writes are not transactional across documents.** A bulk index can partially succeed; the worker logs failed items and retries with exponential backoff. After max retries, items move to `idx:dlq` (dead letter stream) for manual replay.

## 8. Multi-tenancy & isolation

1. **Identity** — every request must carry `X-Tenant-Id` and a bearer token. Middleware rejects mismatched pairs.
2. **Query enforcement** — the search query builder *always* injects `term: {tenant_id: <ctx>}`. The handler cannot bypass this because the ES client wrapper takes `tenant_id` as a required argument and constructs the filter itself.
3. **Routing** — `_routing = tenant_id` on every read and write. Cross-tenant scatter-gather is impossible by construction.
4. **Noisy-neighbour** — per-tenant token bucket in Redis (Lua, atomic). Prototype defaults: 50 RPS sustained, 100 burst.
5. **Resource quotas (production)** — per-tenant doc count + storage caps; ES ILM for retention. Documented but not built.

## 9. Message queue

**Redis Streams** for the prototype: consumer groups give at-least-once delivery, replay, and dead-letter without operating Kafka. The indexer consumes `idx:stream` via `XREADGROUP`, ACKs on success, and pushes to `idx:dlq` on terminal failure.

**Production — Kafka.** Same conceptual shape, replaces Streams when retention, throughput, or multi-consumer fan-out grow. Migration path is mechanical because the worker contract is "consume → bulk-index → ack."

## 10. Trade-offs called out

- **Single shared index vs index-per-tenant.** Chose shared + routing for shard-count scalability; accepted "whale tenant on one shard" risk (mitigated via hybrid model in production).
- **Async-by-default writes.** Returns 202 quickly at the cost of read-your-writes; provided a `?sync=true` escape hatch for tests and admins.
- **FastAPI vs Go/Java.** Picked Python for prototype velocity. In production the same architecture would run on Go for tail-latency reasons; nothing in the design is Python-specific.
- **Redis Streams vs Kafka.** Streams is "good enough" up to mid-tens-of-thousands of events/sec on a single node and avoids a Kafka cluster in the prototype.
- **Bearer token vs full OAuth2/JWT.** Stub HMAC verification for the prototype; OIDC/JWT validation flow described in `PRODUCTION.md`.
