from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    es_url: str = "http://localhost:9200"
    redis_url: str = "redis://localhost:6379/0"
    index_name: str = "documents"

    search_cache_ttl: int = 60
    doc_cache_ttl: int = 300
    search_cache_jitter_pct: int = 10

    rate_limit_rps: int = 50
    rate_limit_burst: int = 100

    stream_key: str = "idx:stream"
    stream_group: str = "idx-cg"
    stream_consumer: str = "indexer-1"
    dlq_key: str = "idx:dlq"

    log_level: str = "INFO"

    # Demo-only HMAC secret for the bearer token stub.
    auth_secret: str = "dev-secret-change-me"


settings = Settings()
