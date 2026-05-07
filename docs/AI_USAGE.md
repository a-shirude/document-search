# AI Tool Usage

The brief explicitly encourages AI assistance, so this is a transparent account of how I used it.

## What AI did

- **Boilerplate acceleration.** Pydantic schemas, FastAPI middleware skeletons, `docker-compose.yml`, and the Postman JSON were largely AI-drafted. They are mechanical to write by hand and offer little signal in an assessment.
- **Reference recall.** Confirmed the exact `XREADGROUP` invocation and the Lua script idiom for an atomic Redis token bucket (I've written both before but reach for a reference each time).
- **Document polish.** Tightened wording on the architecture and production-readiness docs after I'd structured them.

## What AI didn't do

- **The architectural calls.** Shared-index-with-routing vs index-per-tenant, async-by-default writes with a sync escape hatch, Redis Streams as a Kafka-shaped placeholder, versioned cache invalidation — these are choices I made up front and the AI was asked to align with, not derive.
- **Tenant isolation as defence-in-depth.** The `SearchClient` API forces `tenant_id` to be a required argument so handlers can't accidentally issue a cross-tenant query. That's an API-design decision, not a generated one — and it's the test that `test_tenant_isolation.py` was written first to enforce.
- **Trade-off framing.** Each "Why this over X" line in `ARCHITECTURE.md` is mine; the assessment grades thinking, not a list of components, and that's where AI is least useful.

## Where AI was wrong and I overrode it

- An AI suggestion proposed `KEYS search:{tenant}:*` for cache invalidation. Replaced with the versioned-key pattern (`cacheVer:{tenant}` baked into every key) — `KEYS`/`SCAN` against a hot prefix on a real Redis is a cliff every senior engineer has fallen off once.
- A suggested mapping omitted `_routing`-friendly choices and defaulted to `dynamic: true`. Tightened the mapping to explicit `keyword`/`text` types and disabled dynamic mapping in spirit (production version would set `dynamic: strict`).
- An initial draft of the indexer had no DLQ path and unbounded retries. Replaced with bounded retries via the consumer-group PEL `times_delivered` count, with a DLQ after `MAX_DELIVERIES`.

## Time split

Roughly: 35% docs (architecture + production), 50% code, 15% AI prompting + reviewing AI output. AI saved ~30–40% on net wall-clock — most of the win was on boilerplate and recall, not on the parts the assessment is actually grading.
