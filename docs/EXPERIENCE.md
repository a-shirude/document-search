# Experience Showcase

> The brief asks for 1–2 paragraphs each from prior experience. Below are anonymised, generalised vignettes that illustrate the kind of thinking I'd bring to this system. Specific company names / numbers have been generalised.

## A similar distributed system I've built

I led the design of an enterprise document discovery and classification platform that ingested customer file shares (millions of documents per tenant, hundreds of tenants) and exposed search + faceted analytics over them. The core was an Elasticsearch cluster behind a stateless API tier with Redis as the cache and a Kafka-backed indexing pipeline, broadly the same shape as the architecture in this repo. The two architectural calls I'm proudest of were the **shared-index-with-routing** model (we evaluated index-per-tenant first and walked back from it once the shard math didn't fit) and a **hybrid promotion path** for whale tenants — about 4% of tenants drove >50% of QPS and got their own dedicated indices, which let us hit p95 < 400 ms across the long tail without over-provisioning the shared cluster.

## A performance optimisation that mattered

On the same platform, our search p95 sat at ~1.8 s and we needed it under 800 ms. Profiling showed the dominant cost was *cross-shard scatter-gather*: every search hit all shards, reduce-merged the results, and re-ranked. We added `_routing = tenant_id` on writes, retro-fitted reads, and our queries became single-shard. p95 dropped to ~280 ms in one deploy. The lesson — the right index/data layout almost always beats query tuning. We also added a tenant-versioned cache key (`cacheVer:{tenant}`) so write-driven invalidations were O(1) instead of a SCAN, which lifted cache hit ratio from ~40% to ~75%.

## A critical production incident I resolved

We had a Saturday-morning page: one tenant's writes had ballooned the indexing queue to ~6 hours of lag. Every consumer was alive and processing, but slowly. The root cause turned out to be a single tenant pushing 100 KB documents with one enormous `keyword` field; `eager_global_ordinals` rebuilds on every refresh were starving the JVM. Short term we throttled that tenant at the API edge, force-merged the affected index, and turned global ordinals lazy. Longer term we added a per-tenant write-rate quota and a doc-size cap at the API, plus a canary that flagged any tenant breaching p95 budgets. The deeper lesson was that a multi-tenant system that doesn't have *per-tenant resource quotas* doesn't actually have isolation — it has politeness.

## An architectural decision that balanced competing concerns

When designing the indexing path, the team was split between Kafka (durable, scales to anything, operationally heavy) and Redis Streams (simple, already in our stack, capped throughput). The naïve Kafka choice would have meant a six-month delay for a use case the team had no operational experience with. I made the call to ship on Streams with a deliberately Kafka-shaped consumer interface: consumer groups, ack/nack, dead-letter, configurable batch sizes — all behind an interface that took ~200 LOC to swap. We migrated to Kafka 14 months later when our throughput crossed the threshold; the migration touched the indexer worker only and was deployed in a single sprint. The principle: **make the thing you'll outgrow easy to replace**, and you can choose the simpler tool today without paying for it twice.
