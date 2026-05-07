"""Per-tenant token bucket implemented as an atomic Redis Lua script.

Production note: the script is loaded once via EVALSHA cache by the redis-py
client. Buckets live for `ttl` seconds of inactivity before being garbage
collected.
"""

from __future__ import annotations

import time

import redis.asyncio as redis

# KEYS[1] = bucket key
# ARGV[1] = capacity (burst)
# ARGV[2] = refill rate (tokens / sec)
# ARGV[3] = now (seconds, float)
# ARGV[4] = cost (tokens to consume; usually 1)
# returns {allowed (1/0), tokens_remaining, retry_after_ms}
LUA = """
local k = KEYS[1]
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', k, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = cap
  ts = now
end
local delta = math.max(0, now - ts)
tokens = math.min(cap, tokens + delta * rate)

local allowed = 0
local retry = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil(((cost - tokens) / rate) * 1000)
end

redis.call('HMSET', k, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', k, 3600)
return {allowed, tokens, retry}
"""


class TokenBucket:
    def __init__(self, client: redis.Redis, capacity: int, rate: int) -> None:
        self.r = client
        self.capacity = capacity
        self.rate = rate
        self._sha: str | None = None

    async def _ensure_loaded(self) -> str:
        if self._sha is None:
            self._sha = await self.r.script_load(LUA)
        return self._sha

    async def allow(self, tenant_id: str, cost: int = 1) -> tuple[bool, int, int]:
        sha = await self._ensure_loaded()
        now = time.time()
        res = await self.r.evalsha(
            sha, 1, f"rl:{tenant_id}", self.capacity, self.rate, now, cost
        )
        allowed, tokens, retry_ms = int(res[0]), int(float(res[1])), int(res[2])
        return allowed == 1, tokens, retry_ms
