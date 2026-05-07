# Prototype Benchmarks

Crude single-host numbers from the prototype to demonstrate the cache and search paths under load. Treat them as illustrative — single-node ES on Docker Desktop, sharing CPU with the API and the load generator.

## Setup

- 3 tenants × 1000 docs each (`scripts/seed.py`)
- API: 2 uvicorn workers, FastAPI, in-container
- ES: single node, 6 shards × 1 replica, default mapping (`app/search/mapping.py`)
- Redis: single node (cache + token bucket + stream)
- Concurrency: 50 simultaneous clients × 200 requests = 10,000 total requests
- Hardware: Docker Desktop on Windows, dev machine

## Results

### Cache-warm (`scripts/bench.py` — 9-query rotation)

| Metric        | Value     |
|---------------|-----------|
| Requests      | 10,000    |
| Elapsed       | 35.4 s    |
| Throughput    | **283 req/s** |
| Latency p50   | 156 ms    |
| Latency p95   | **302 ms** |
| Latency p99   | 469 ms    |

p95 well under the 500 ms target. The cache is doing real work here — most queries are repeats.

### Cache-cold (random 3-term queries, ~27k cardinality)

| Metric        | Value     |
|---------------|-----------|
| Requests      | 10,000    |
| Elapsed       | 69.0 s    |
| Throughput    | 145 req/s |
| Latency p50   | 321 ms    |
| Latency p95   | 596 ms    |
| Latency p99   | 796 ms    |

Roughly 2× the latency of the warm run, ~½ the throughput — exactly the shape you'd expect when bypassing the cache. p95 sits just over the 500 ms target on a single-node ES; production would have:
- a multi-node ES cluster (queries fan out within a single shard thanks to routing, but read replicas absorb concurrent load),
- API tier on more cores than the 2-worker Docker container,
- a real-world Zipfian query distribution where the top decile of queries dominates traffic, lifting cache hit ratio toward 70–80%.

## What this benchmark *doesn't* tell you

- Tail behaviour at 100k+ QPS — not stressed here.
- Index-write throughput — only read-path measured.
- p999 — sample size too small.
- GC / JVM warm-up effects on ES — first ~30 s of any run will be slower.

The number that matters from a system-design perspective is the **shape of the difference between warm and cold** — the cache is paying for itself, and the path through the rate limiter + tenant middleware adds <10 ms vs. a bare ES query (visible in the warm-cache p50 of 156 ms vs. ES `took_ms` of ~5–15 ms).
