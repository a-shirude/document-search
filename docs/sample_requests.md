# Sample API requests

The dev bearer token is `HMAC(tenant_id, AUTH_SECRET)[:32]`. Print one with:

```bash
python -m scripts.token tenant-a
```

Below, replace `$TOKEN` with the printed value.

## Index a document (async, returns 202)

```bash
curl -sS -X POST http://localhost:8000/documents \
  -H "X-Tenant-Id: tenant-a" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Distributed systems primer","body":"BM25 sharding routing consistency","tags":["tech"]}'
```

## Index synchronously (returns 201, immediately searchable after refresh)

```bash
curl -sS -X POST 'http://localhost:8000/documents?sync=true' \
  -H "X-Tenant-Id: tenant-a" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":"hello-1","title":"Hello tenant","body":"sync path","tags":["demo"]}'
```

## Search

```bash
curl -sS 'http://localhost:8000/search?q=sharding&fuzzy=true' \
  -H "X-Tenant-Id: tenant-a" -H "Authorization: Bearer $TOKEN"
```

## Get a document

```bash
curl -sS http://localhost:8000/documents/hello-1 \
  -H "X-Tenant-Id: tenant-a" -H "Authorization: Bearer $TOKEN"
```

## Delete a document

```bash
curl -sS -X DELETE http://localhost:8000/documents/hello-1 \
  -H "X-Tenant-Id: tenant-a" -H "Authorization: Bearer $TOKEN"
```

## Health / readiness

```bash
curl -sS http://localhost:8000/healthz
curl -sS http://localhost:8000/readyz
```
