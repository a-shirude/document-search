# Production Readiness Analysis

Scope: what would change between the prototype in this repo and a service we'd put behind a 99.95% SLO at 100× the prototype's load. Each section names *what* and *why*, not just a generic checklist.

---

## 1. Scalability — handling 100× growth

**Document tier (10M → 1B+ docs).**
- ES cluster sizing: a tested rule of thumb is ~30–50 GB / shard for text-heavy corpora. At 1B docs ~ 1 KB each → ~ 1 TB → 30 primary shards × 1 replica across ~10–15 data nodes. The current mapping of 6 shards is sized for the prototype only.
- **Hybrid tenancy.** Promote whales (>5% of corpus or >5% of QPS) onto dedicated indices. Mid-tier tenants share an index per pricing/region cohort. The long tail stays on the shared index. Routing key stays `tenant_id` everywhere — only the index name changes — so the API contract is unaffected.
- **ILM (Index Lifecycle Management).** Time-based rollover for log-shaped tenants; force-merge to 1 segment on cold indices to slash query memory.
- **Hot/warm/cold tiers.** Cold tier on cheap nodes (or `searchable_snapshots` against S3) for archived data.

**API tier (1k → 100k QPS).**
- Stateless pods scale horizontally on QPS / CPU. p95-budget-aware HPA, not just CPU.
- **Locality.** Pin region pods to region-local ES & Redis. A cross-AZ Redis call alone burns the 500 ms budget on a slow day.
- **Connection pooling.** ES + Redis clients reuse a per-pod pool sized to expected concurrency — not per-request connections.

**Cache tier.**
- Redis Cluster (sharded by tenant\_id) once a single primary's memory or CPU saturates. Replicas per shard for read fan-out and HA.
- Optional client-side L0 (in-process LRU, 1–5 s TTL) for the hottest queries to absorb thundering-herd cache misses.

**Indexing tier.**
- Replace Redis Streams with **Kafka** once retention or fan-out grows. Same consumer-group semantics, much higher throughput and durability.
- Indexer workers scale linearly with consumer-group partitions; back-pressure is managed at Kafka, not the API.

---

## 2. Resilience

- **Circuit breakers** around ES and Redis (open after N consecutive 5xx/timeouts in a sliding window). When ES is open, search returns last-good cache or 503. When Redis is open, search bypasses the cache and goes straight to ES — slower, still correct.
- **Retries.** Idempotent ops (PUT-by-id, DELETE-by-id) get exponential-backoff retries with jitter and a hard deadline. Search retries are bounded to one attempt — better to fail fast than chain timeouts inside a 500 ms p95 budget.
- **Bulkheads.** Separate connection pools per dependency so a slow ES doesn't starve Redis calls.
- **Dead-letter queue.** Already implemented (`idx:dlq`) — a tool to replay it lives behind a feature flag.
- **Failover.** ES cross-cluster replication (CCR) for region failover; Redis Sentinel/Cluster with replica promotion; API tier active-active.
- **Graceful degradation.** Disable highlight/fuzzy first when ES is hot. Ranking-quality features should be the first thing thrown overboard, not availability.

---

## 3. Security

- **AuthN.** Replace the HMAC stub with **OIDC + JWT** validated against a JWKS endpoint. Rotate signing keys; cache JWKS with a short TTL.
- **AuthZ.** Per-tenant API keys + per-document ACLs. The `acl` field on the mapping is already wired — production adds the filter clause `terms: {acl: <user_groups>}` at the search layer.
- **Tenant isolation defence-in-depth.** (a) Mandatory `tenant_id` filter in the query builder; (b) `_routing` enforcement; (c) ES *document-level security* via per-tenant ES API keys with a baked-in role query — even an internal bug bypassing (a) cannot exfiltrate.
- **Encryption.** TLS everywhere (ES TLS + mTLS between API and ES); ES disk encryption at the volume level; KMS-managed keys.
- **Secrets.** No secrets in env vars in prod — pull from Vault/Secrets Manager at startup and rotate via SIGHUP or pod restart.
- **Input validation.** Pydantic at the edge plus an ES query-cost limit (`indices.query.bool.max_clause_count`) to stop pathological queries.
- **Rate limit + WAF.** Per-tenant limit (already built) + per-IP limit at the gateway to deflect credential-stuffing-style abuse.
- **Audit log.** Append-only structured log of every index/delete with tenant + actor + payload hash. Retain 90+ days for compliance.

---

## 4. Observability

- **Metrics (Prometheus + Grafana).** RED at the API edge per tenant, USE on ES and Redis nodes. Critical SLIs: search p95, index lag (stream length), cache hit ratio, ES rejected-task count.
- **Tracing (OpenTelemetry).** Propagate trace context across API → ES / Redis. The single most useful trace span is "ES search," tagged with `tenant_id`, `shards_queried`, `cache_status`.
- **Logging.** Structured JSON (already wired); a tenant\_id field on every record. Sampled body logging only for slow queries, never the full search request.
- **Dashboards & SLOs.** SLO burn-rate alerts (multi-window, multi-burn-rate) on search-success and search-latency. No raw "5xx > N" alerts.
- **Synthetic probes.** A canary tenant in every region with index/search probes every 30 s, paging on consecutive failure.

---

## 5. Performance

- **Mapping discipline.** No `text` fields we won't search. `keyword` for filters. `index: false` on payload-only fields. `eager_global_ordinals: true` on tenant\_id (it's filtered on every query).
- **Query shape.** No leading wildcards. Cap `from + size`; switch to `search_after` for deep paging. Compute facets via `terms` aggregations only when requested.
- **Index settings.** `refresh_interval = 30s` on bulk-write indices (huge indexing throughput win); revert to 1 s for low-write tenants.
- **JVM.** Right-size ES heap (≤30 GB to keep compressed oops); use ZGC on JDK17+ if pause-time-sensitive.
- **Caching.** OS page cache is the real performance lever — leave 50% of node RAM unused for it. Don't co-locate ES with greedy neighbours.
- **Force-merge** cold indices to 1 segment.

---

## 6. Operations

- **Deployment.** GitOps; staged rollouts (canary 1% → 10% → 50% → 100%) gated on SLO burn.
- **Zero-downtime updates.** API tier is stateless → rolling deploys behind the LB. ES rolling restarts use shard allocation awareness; we set `cluster.routing.allocation.enable: primaries` before each node restart and re-enable after.
- **Schema migrations.** Index mapping changes can't be done in place. Process: create `documents-v2` with new mapping → dual-write via the indexer → reindex → cut search reads via an alias swap → delete `documents-v1`. The API talks to the alias, not the index, so no client-visible cutover.
- **Backup & recovery.** ES `snapshot` to S3 every hour for incremental, daily full. Quarterly restore drill — backups you've never restored aren't backups.
- **Capacity planning.** Per-tenant doc count and growth-rate dashboards; storage projections; auto-open Jira tickets at 70% / 85% disk.

---

## 7. SLA — getting to 99.95%

99.95% = ~21.6 minutes / month. Concrete asks:

- **No single point of failure.** ≥3 ES master-eligible nodes, ≥1 replica per shard, Redis Cluster with replicas, API in ≥3 AZs.
- **Multi-region active-active** for read traffic. Writes can be active-passive (CCR) — the failover RTO is the dominant factor.
- **Deploys are the #1 incident source.** Mandatory canary + automated rollback on burn-rate alert. No 100% rollouts in one step.
- **Fast detection.** SLO burn alerts (1h/5m windows) page within ~2 minutes of regression.
- **Runbooks.** Tier-1 oncall runs them; nothing requires waking an architect.
- **Game days.** Quarterly chaos exercise: kill an ES node, partition Redis, blow up an AZ. The first time you discover a failure mode should *not* be in production.

---

## Bonus: cost optimisation

- ES **searchable snapshots** to push cold tenants onto S3-backed nodes — orders of magnitude cheaper per GB.
- Spot/preemptible instances for indexer workers (idempotent, replayable) but never for ES data nodes.
- Cache more aggressively (larger TTL, larger pool) on read-heavy tenants — every cache hit is one fewer ES query and the cheapest CPU we have.
- Per-tenant data tiering aligned to the customer's pricing tier.
