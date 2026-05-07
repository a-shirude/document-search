"""Crude search benchmark.

Reports throughput and p50/p95/p99 latency for /search at a fixed concurrency.
This is not a serious load tester (no warmup, no histogram bucketing); it
exists to produce the bonus "performance benchmarks" number on the prototype.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx

from app.tenancy.middleware import expected_token

QUERIES = ["alpha", "search", "elastic", "shard", "tenant", "fuzzy", "ranking", "cache redis", "throughput"]


async def worker(client: httpx.AsyncClient, base: str, tenant: str, n: int, results: list[float]) -> None:
    headers = {"X-Tenant-Id": tenant, "Authorization": f"Bearer {expected_token(tenant)}"}
    for _ in range(n):
        q = random.choice(QUERIES)
        t0 = time.perf_counter()
        r = await client.get(f"{base}/search", params={"q": q}, headers=headers)
        results.append((time.perf_counter() - t0) * 1000)
        if r.status_code >= 500:
            print("server error:", r.status_code, r.text)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--tenant", default="tenant-a")
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--per-worker", type=int, default=200)
    args = ap.parse_args()

    results: list[float] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        t0 = time.perf_counter()
        await asyncio.gather(*(worker(client, args.base, args.tenant, args.per_worker, results) for _ in range(args.concurrency)))
        elapsed = time.perf_counter() - t0

    total = len(results)
    p = sorted(results)
    def pct(x: float) -> float: return p[int(min(len(p) - 1, x * len(p)))]
    print(f"requests:     {total}")
    print(f"elapsed:      {elapsed:.2f}s")
    print(f"throughput:   {total / elapsed:.1f} req/s")
    print(f"latency p50:  {statistics.median(p):.1f} ms")
    print(f"latency p95:  {pct(0.95):.1f} ms")
    print(f"latency p99:  {pct(0.99):.1f} ms")


if __name__ == "__main__":
    asyncio.run(main())
