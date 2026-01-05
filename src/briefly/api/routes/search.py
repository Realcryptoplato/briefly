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


class ExploreRequest(BaseModel):
    """Request body for drill-down exploration."""

    query: str
    context_id: str | None = None  # Parent briefing/item ID for relevance
    breadcrumb: list[str] | None = None  # Current navigation path
    limit: int = 12


class ExploreResult(BaseModel):
    """A single exploration result with rich metadata."""

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
    thumbnail_url: str | None = None
    tags: list[str] | None = None


class ExploreResponse(BaseModel):
    """Response from drill-down exploration."""

    query: str
    context_id: str | None
    breadcrumb: list[str]
    results: list[ExploreResult]
    suggested_queries: list[str]


@router.post("/explore", response_model=ExploreResponse)
async def explore_content(request: ExploreRequest) -> ExploreResponse:
    """
    Drill-down exploration with vector search.

    Supports contextual navigation with breadcrumbs.

    Examples:
    - query: "venezuela oil impact", context: briefing_123
    - query: "crypto regulations", breadcrumb: ["Home", "Crypto"]
    """
    store = VectorStore()

    # Perform semantic search
    results = await store.search(
        query=request.query,
        limit=min(request.limit, 20),
    )

    # Build breadcrumb trail
    breadcrumb = request.breadcrumb or ["Home"]
    # Add current query to breadcrumb if not already there
    query_title = request.query.title()[:30]
    if query_title not in breadcrumb:
        breadcrumb.append(query_title)

    # Generate suggested queries based on results
    # Extract common terms from results for suggestions
    suggested = _extract_suggested_queries(results, request.query)

    # Enrich results with thumbnails
    enriched_results = []
    for r in results:
        thumbnail = None
        if r.get("platform") == "youtube" and r.get("platform_id"):
            thumbnail = f"https://img.youtube.com/vi/{r['platform_id']}/mqdefault.jpg"

        enriched_results.append(ExploreResult(
            id=r.get("id", ""),
            platform=r.get("platform", ""),
            platform_id=r.get("platform_id", ""),
            source_id=r.get("source_id", ""),
            source_name=r.get("source_name"),
            title=r.get("title"),
            url=r.get("url"),
            published_at=r.get("published_at"),
            chunk_content=r.get("chunk_content", ""),
            similarity=r.get("similarity", 0.0),
            thumbnail_url=thumbnail,
            tags=None,  # Could extract from content
        ))

    return ExploreResponse(
        query=request.query,
        context_id=request.context_id,
        breadcrumb=breadcrumb,
        results=enriched_results,
        suggested_queries=suggested,
    )


def _extract_suggested_queries(results: list[dict], current_query: str) -> list[str]:
    """Extract suggested follow-up queries from search results."""
    import re
    from collections import Counter

    # Common words to ignore
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall",
        "can", "to", "of", "in", "for", "on", "with", "at", "by",
        "from", "as", "into", "through", "during", "before", "after",
        "above", "below", "between", "under", "again", "further",
        "then", "once", "here", "there", "when", "where", "why",
        "how", "all", "each", "few", "more", "most", "other", "some",
        "such", "no", "nor", "not", "only", "own", "same", "so",
        "than", "too", "very", "just", "and", "but", "if", "or",
        "because", "until", "while", "this", "that", "these", "those",
        "i", "you", "he", "she", "it", "we", "they", "what", "which",
        "who", "whom", "its", "his", "her", "our", "your", "their",
    }

    # Extract words from result content
    word_counts: Counter = Counter()
    for r in results:
        content = r.get("chunk_content", "").lower()
        title = (r.get("title") or "").lower()
        text = f"{title} {content}"

        # Extract meaningful words (3+ chars, not numbers, not in stopwords)
        words = re.findall(r'\b[a-z]{3,15}\b', text)
        for word in words:
            if word not in stopwords and word not in current_query.lower():
                word_counts[word] += 1

    # Get top terms as suggestions
    current_words = set(current_query.lower().split())
    suggestions = []
    for word, count in word_counts.most_common(10):
        if word not in current_words and count >= 2:
            suggestions.append(word)
            if len(suggestions) >= 5:
                break

    return suggestions


@router.get("/explore")
async def explore_content_get(
    q: str,
    context_id: str | None = None,
    depth: int = Query(default=1, le=5),
    limit: int = Query(default=12, le=20),
) -> ExploreResponse:
    """
    Drill-down exploration (GET variant).

    URL pattern: /explore?q=venezuela+oil&context=briefing_123&depth=2
    """
    # Build breadcrumb from depth (placeholder)
    breadcrumb = ["Home"] + (["..."] * (depth - 1)) if depth > 1 else ["Home"]

    return await explore_content(ExploreRequest(
        query=q,
        context_id=context_id,
        breadcrumb=breadcrumb,
        limit=limit,
    ))
