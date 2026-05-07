"""ES index definition. Centralised so the indexer worker and the API agree."""

INDEX_SETTINGS = {
    "settings": {
        "number_of_shards": 6,
        "number_of_replicas": 1,
        "analysis": {
            "analyzer": {
                "default": {"type": "english"},
            }
        },
    },
    "mappings": {
        "properties": {
            "tenant_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "english",
                "fields": {"raw": {"type": "keyword", "ignore_above": 256}},
            },
            "body": {"type": "text", "analyzer": "english"},
            "tags": {"type": "keyword"},
            "acl": {"type": "keyword"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
        }
    },
}
