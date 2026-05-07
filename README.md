# Distributed Document Search Service (DSS)

A prototype of a multi-tenant document search service designed to index 10M+ documents with sub-second p95 search latency.

> **Read these in order:**
> 1. `docs/ARCHITECTURE.md` — design, trade-offs, data flow
> 2. `docs/PRODUCTION.md` — what would change for production
> 3. `docs/EXPERIENCE.md` — relevant prior work
> 4. `docs/BENCHMARKS.md` — measured numbers from the prototype
> 5. `docs/AI_USAGE.md` — how AI was used on this submission

## Stack

- **API** — FastAPI (Python 3.11), async end-to-end
- **Search** — Elasticsearch 8 (single shared index, `_routing = tenant_id`)
- **Cache & rate limit** — Redis 7 (versioned-key cache + Lua token bucket)
- **Async indexing** — Redis Streams consumer group (Kafka in production)
- **Containerisation** — docker-compose

## Layout

```
app/
  api/         FastAPI routes, schemas, lifespan wiring
  search/      ES client wrapper + index mapping
  cache/       Redis cache (versioned per-tenant invalidation)
  ratelimit/   Per-tenant token bucket (atomic Lua)
  tenancy/     Auth + tenant middleware
  indexer/     Stream producer + consumer worker
docs/          Architecture, production, experience, AI usage, sample requests
scripts/       seed.py, bench.py, token.py
tests/         Tenant isolation tests
```

## Run it

```bash
docker compose up --build
# Wait until elasticsearch is "yellow" (the API container waits for it).

# Print a dev bearer token for tenant-a
docker compose exec api python -m scripts.token tenant-a
# (or run locally: pip install -r requirements.txt && python -m scripts.token tenant-a)

export TOKEN=<paste-token>

# Index a doc synchronously (immediately searchable after refresh)
curl -sS -X POST 'http://localhost:8000/documents?sync=true' \
  -H "X-Tenant-Id: tenant-a" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"hello-1","title":"Hello tenant","body":"sync path","tags":["demo"]}'

# Refresh ES so the doc is visible (default refresh interval is 1s anyway)
sleep 2

# Search
curl -sS 'http://localhost:8000/search?q=hello' \
  -H "X-Tenant-Id: tenant-a" -H "Authorization: Bearer $TOKEN"
```

More examples in `docs/sample_requests.md` and `postman_collection.json`.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET    | `/healthz`  | Liveness — public |
| GET    | `/readyz`   | Deep readiness (ES + Redis) — public |
| POST   | `/documents`           | Index. `?sync=true` for inline; default returns 202 |
| GET    | `/documents/{id}`      | Cached on read (`doc:{tenant}:{id}`, TTL 5 min) |
| DELETE | `/documents/{id}`      | Invalidates doc + bumps tenant cache version |
| GET    | `/search`              | Params: `q`, `page`, `size`, `tags`, `fuzzy`, `highlight` |

All non-public endpoints require:
- `X-Tenant-Id: <tenant>`
- `Authorization: Bearer <HMAC(tenant_id, AUTH_SECRET)[:32]>`

## Seed + benchmark

```bash
python -m scripts.seed --tenants tenant-a,tenant-b,tenant-c --per-tenant 1000
python -m scripts.bench --tenant tenant-a --concurrency 50 --per-worker 200
```

The bench script reports throughput and p50/p95/p99 latency.

## Tests

The flagship test is tenant isolation: tenant B cannot see / fetch / delete tenant A's documents through any path.

```bash
docker compose up -d
pip install -r requirements.txt
pytest -q
```

## Bonus features built

- Highlighting (`/search?highlight=true` — default on)
- Fuzzy matching (`/search?fuzzy=true`)
- Tag faceting filter (`/search?tags=tech&tags=ops`)
- Async indexing with consumer-group + DLQ
- Versioned cache invalidation (O(1), no `SCAN`)
- Atomic per-tenant token bucket via Lua
- Per-tenant cache scoping at every layer

## Known limitations (deliberate, in the spirit of "prototype, not production")

- Bearer token is HMAC-of-tenant — replace with OIDC/JWT (see `docs/PRODUCTION.md` §3).
- Single-node ES & Redis in compose — multi-node sizing is in `docs/PRODUCTION.md` §1.
- No distributed tracing wired (OpenTelemetry stubs would be the natural addition).
- No singleflight / XFetch for cache stampede (designed-in but not built).
