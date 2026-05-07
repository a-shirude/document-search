from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentIn(BaseModel):
    id: str | None = Field(default=None, description="Optional client-supplied id")
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=0, max_length=200_000)
    tags: list[str] = Field(default_factory=list)
    acl: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class IndexAccepted(BaseModel):
    id: str
    status: str


class SearchHit(BaseModel):
    id: str
    score: float | None
    title: str | None
    snippet: str | None
    tags: list[str] = []
    highlight: dict[str, Any] = {}


class SearchResponse(BaseModel):
    total: int
    page: int
    size: int
    took_ms: int | None = None
    cache: str | None = None
    hits: list[SearchHit]
