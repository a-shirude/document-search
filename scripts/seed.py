"""Seed synthetic documents across multiple tenants.

Usage:
    python -m scripts.seed --tenants tenant-a,tenant-b,tenant-c --per-tenant 1000

The script talks to the API directly so it exercises the same async indexing
path a real client would use.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import string

import httpx

from app.tenancy.middleware import expected_token

WORDS = (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa "
    "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega "
    "search index ranking shard cache redis elastic distributed query "
    "latency throughput tenant token bucket bulk routing fuzzy highlight"
).split()


def _sentence(n: int) -> str:
    return " ".join(random.choice(WORDS) for _ in range(n))


def _doc(i: int) -> dict:
    title = f"Document {i}: {_sentence(4)}"
    body = _sentence(80)
    tags = random.sample(["news", "finance", "tech", "ops", "legal", "hr"], k=2)
    return {"title": title, "body": body, "tags": tags}


async def seed_tenant(client: httpx.AsyncClient, base: str, tenant: str, n: int) -> None:
    headers = {"X-Tenant-Id": tenant, "Authorization": f"Bearer {expected_token(tenant)}"}
    sem = asyncio.Semaphore(50)

    async def one(i: int) -> None:
        async with sem:
            payload = _doc(i)
            r = await client.post(f"{base}/documents", json=payload, headers=headers)
            r.raise_for_status()

    await asyncio.gather(*(one(i) for i in range(n)))
    print(f"seeded {n} docs for tenant={tenant}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--tenants", default="tenant-a,tenant-b,tenant-c")
    ap.add_argument("--per-tenant", type=int, default=500)
    args = ap.parse_args()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for tenant in args.tenants.split(","):
            tenant = tenant.strip()
            if tenant:
                await seed_tenant(client, args.base, tenant, args.per_tenant)


if __name__ == "__main__":
    asyncio.run(main())
