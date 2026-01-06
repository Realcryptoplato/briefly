"""Source discovery search endpoints.

Search for X accounts, YouTube channels, and podcasts to add as sources.
Separate from semantic search (search.py) which searches ingested content.
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

router = APIRouter()


# ============================================================================
# Response Models
# ============================================================================

class XAccountResult(BaseModel):
    """X account search result."""
    username: str
    name: str
    bio: str
    approximate_followers: str
    verified: bool


class XSearchResponse(BaseModel):
    """Response from X account search."""
    query: str
    count: int
    results: list[XAccountResult]


class YouTubeChannelResult(BaseModel):
    """YouTube channel search result."""
    channel_id: str
    name: str
    description: str
    subscribers: str
    thumbnail: str | None
    handle: str | None


class YouTubeSearchResponse(BaseModel):
    """Response from YouTube channel search."""
    query: str
    count: int
    results: list[YouTubeChannelResult]


class PodcastResult(BaseModel):
    """Podcast search result."""
    name: str
    author: str
    feed_url: str
    artwork: str
    description: str
    episode_count: int | None = None
    genres: list[str] | None = None


class PodcastSearchResponse(BaseModel):
    """Response from podcast search."""
    query: str
    count: int
    results: list[PodcastResult]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/x", response_model=XSearchResponse)
async def search_x_accounts(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=10, ge=1, le=25, description="Max results"),
) -> XSearchResponse:
    """
    Search X for accounts matching query.

    Uses Grok's x_search to discover relevant X accounts.

    Examples:
    - q=AI researchers
    - q=crypto influencers
    - q=tech news
    """
    from briefly.adapters.grok import get_grok_adapter

    adapter = get_grok_adapter()
    results = await adapter.search_accounts(q, limit=limit)

    # Convert to response model
    accounts = []
    for r in results:
        try:
            accounts.append(XAccountResult(
                username=r.get("username", ""),
                name=r.get("name", r.get("username", "")),
                bio=r.get("bio", "")[:200],
                approximate_followers=r.get("approximate_followers", "Unknown"),
                verified=r.get("verified", False),
            ))
        except Exception:
            # Skip malformed results
            continue

    return XSearchResponse(
        query=q,
        count=len(accounts),
        results=accounts,
    )


@router.get("/youtube", response_model=YouTubeSearchResponse)
async def search_youtube_channels(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=10, ge=1, le=25, description="Max results"),
) -> YouTubeSearchResponse:
    """
    Search YouTube for channels matching query.

    Uses YouTube Data API v3.

    Examples:
    - q=tech reviews
    - q=AI tutorials
    - q=coding education
    """
    from briefly.adapters.youtube import YouTubeAdapter

    adapter = YouTubeAdapter()
    results = await adapter.search_channels(q, limit=limit)

    # Convert to response model
    channels = []
    for r in results:
        channels.append(YouTubeChannelResult(
            channel_id=r.get("channel_id", ""),
            name=r.get("name", ""),
            description=r.get("description", "")[:200],
            subscribers=r.get("subscribers", "0"),
            thumbnail=r.get("thumbnail"),
            handle=r.get("handle"),
        ))

    return YouTubeSearchResponse(
        query=q,
        count=len(channels),
        results=channels,
    )


@router.get("/podcasts", response_model=PodcastSearchResponse)
async def search_podcasts(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=10, ge=1, le=25, description="Max results"),
) -> PodcastSearchResponse:
    """
    Search podcasts via iTunes Search API (free, no auth).

    Examples:
    - q=AI podcasts
    - q=tech news
    - q=business interviews
    """
    from briefly.adapters.podcast_search import search_podcasts as itunes_search

    results = await itunes_search(q, limit=limit)

    # Convert to response model
    podcasts = []
    for r in results:
        podcasts.append(PodcastResult(
            name=r.get("name", ""),
            author=r.get("author", ""),
            feed_url=r.get("feed_url", ""),
            artwork=r.get("artwork", ""),
            description=r.get("description", "")[:200],
            episode_count=r.get("episode_count"),
            genres=r.get("genres"),
        ))

    return PodcastSearchResponse(
        query=q,
        count=len(podcasts),
        results=podcasts,
    )
