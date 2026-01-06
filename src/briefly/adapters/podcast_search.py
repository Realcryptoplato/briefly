"""Podcast search adapter using iTunes Search API.

The iTunes Search API is free and requires no authentication.
It provides comprehensive podcast metadata including feed URLs.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# iTunes Search API endpoint (free, no auth required)
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"


async def search_podcasts(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Search podcasts via iTunes Search API (free, no auth).

    Args:
        query: Search query (e.g., "AI podcasts", "tech news")
        limit: Maximum number of results (default 10, max 200)

    Returns:
        List of podcast dicts with:
        - name: Podcast name
        - author: Podcast author/creator
        - feed_url: RSS feed URL
        - artwork: Artwork URL (600x600)
        - description: Podcast description
        - episode_count: Number of episodes
        - genres: List of genre names
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                ITUNES_SEARCH_URL,
                params={
                    "term": query,
                    "media": "podcast",
                    "limit": min(limit, 200),  # iTunes caps at 200
                },
            )
            response.raise_for_status()
            data = response.json()

            podcasts = []
            for result in data.get("results", []):
                # Extract relevant fields
                podcast = {
                    "name": result.get("collectionName", ""),
                    "author": result.get("artistName", ""),
                    "feed_url": result.get("feedUrl", ""),
                    "artwork": result.get("artworkUrl600")
                        or result.get("artworkUrl100")
                        or result.get("artworkUrl60", ""),
                    "description": result.get("description", "")
                        or result.get("collectionName", ""),
                    "episode_count": result.get("trackCount", 0),
                    "genres": result.get("genres", []),
                    "collection_id": result.get("collectionId"),
                    "itunes_url": result.get("collectionViewUrl", ""),
                }

                # Skip podcasts without a feed URL (can't subscribe)
                if podcast["feed_url"]:
                    podcasts.append(podcast)

            logger.info(f"Found {len(podcasts)} podcasts for query '{query}'")
            return podcasts

    except httpx.HTTPStatusError as e:
        logger.error(f"iTunes API HTTP error: {e}")
        return []
    except httpx.RequestError as e:
        logger.error(f"iTunes API request error: {e}")
        return []
    except Exception as e:
        logger.error(f"Error searching podcasts: {e}")
        return []


async def get_podcast_by_id(collection_id: int) -> dict[str, Any] | None:
    """
    Get a specific podcast by its iTunes collection ID.

    Args:
        collection_id: iTunes collection ID

    Returns:
        Podcast dict or None if not found
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://itunes.apple.com/lookup",
                params={"id": collection_id},
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if results:
                result = results[0]
                return {
                    "name": result.get("collectionName", ""),
                    "author": result.get("artistName", ""),
                    "feed_url": result.get("feedUrl", ""),
                    "artwork": result.get("artworkUrl600", ""),
                    "description": result.get("description", ""),
                    "episode_count": result.get("trackCount", 0),
                    "genres": result.get("genres", []),
                    "collection_id": result.get("collectionId"),
                    "itunes_url": result.get("collectionViewUrl", ""),
                }
            return None

    except Exception as e:
        logger.error(f"Error looking up podcast {collection_id}: {e}")
        return None
