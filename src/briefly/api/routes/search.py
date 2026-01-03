"""Semantic search endpoints."""

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from briefly.services.vectorstore import VectorStore

router = APIRouter()


class SearchRequest(BaseModel):
    """Request body for semantic search."""

    query: str
    limit: int = 10
    platform: str | None = None
    source_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class SearchResult(BaseModel):
    """A single search result."""

    id: str
    platform: str
    platform_id: str
    source_id: str
    source_name: str | None
    title: str | None
    url: str | None
    published_at: str | None
    chunk_content: str
    similarity: float


class SearchResponse(BaseModel):
    """Response from semantic search."""

    query: str
    count: int
    results: list[SearchResult]


@router.post("", response_model=SearchResponse)
async def search_content(
    request: SearchRequest,
) -> SearchResponse:
    """
    Semantic search across all ingested content.

    Examples:
    - "What did anyone say about Bitcoin?"
    - "AI regulation discussions"
    - "Ethereum staking opinions"
    """
    store = VectorStore()
    results = await store.search(
        query=request.query,
        limit=min(request.limit, 50),  # Cap at 50
        platform=request.platform,
        source_id=request.source_id,
        since=request.since,
        until=request.until,
    )

    return SearchResponse(
        query=request.query,
        count=len(results),
        results=[SearchResult(**r) for r in results],
    )


@router.get("/query")
async def search_content_get(
    query: str,
    limit: int = Query(default=10, le=50),
    platform: str | None = None,
    source_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> SearchResponse:
    """
    Semantic search (GET variant for simple queries).

    Same as POST /api/search but uses query parameters.
    """
    store = VectorStore()
    results = await store.search(
        query=query,
        limit=limit,
        platform=platform,
        source_id=source_id,
        since=since,
        until=until,
    )

    return SearchResponse(
        query=query,
        count=len(results),
        results=[SearchResult(**r) for r in results],
    )


class VectorStats(BaseModel):
    """Statistics about the vector store."""

    content_items: dict[str, int]
    total_content_items: int
    total_chunks: int
    chunks_with_embeddings: int


@router.get("/stats", response_model=VectorStats)
async def vector_stats() -> VectorStats:
    """Get statistics about stored content and embeddings."""
    store = VectorStore()
    stats = await store.get_stats()
    return VectorStats(**stats)
