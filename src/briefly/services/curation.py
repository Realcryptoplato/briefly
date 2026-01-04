"""Main curation service that orchestrates the briefing pipeline."""

import logging
from datetime import datetime, timedelta, timezone

from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter
from briefly.adapters.base import ContentItem
from briefly.services.summarization import SummarizationService
from briefly.services.vectorstore import VectorStore
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)


class CurationService:
    """
    Main curation pipeline.

    Orchestrates:
    1. Fetching content from all platforms
    2. Deduping and ranking
    3. Summarization
    4. (Future) Storing briefings
    """

    def __init__(self) -> None:
        self._x_adapter = XAdapter()
        self._youtube_adapter = YouTubeAdapter()
        self._summarizer = SummarizationService()
        self._vectorstore = VectorStore()

    async def create_briefing(
        self,
        x_sources: list[str] | None = None,
        youtube_sources: list[str] | None = None,
        hours_back: int = 24,
    ) -> dict:
        """
        Create a briefing from specified sources.

        Args:
            x_sources: List of X usernames to curate from
            hours_back: How many hours back to look for content

        Returns:
            Dict with 'summary', 'items', 'recommendations', 'stats'
        """
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=hours_back)

        all_items: list[ContentItem] = []
        stats = {
            "sources": {
                "x": len(x_sources or []),
                "youtube": len(youtube_sources or []),
            },
            "items_fetched": {"x": 0, "youtube": 0},
            "time_range_hours": hours_back,
        }

        # Fetch from X
        if x_sources:
            logger.info(f"Fetching from {len(x_sources)} X sources...")
            x_items = await self._x_adapter.fetch_content(
                identifiers=x_sources,
                start_time=start_time,
                end_time=now,
            )
            all_items.extend(x_items)
            stats["items_fetched"]["x"] = len(x_items)
            logger.info(f"Fetched {len(x_items)} X items")

        # Fetch from YouTube
        if youtube_sources:
            logger.info(f"Fetching from {len(youtube_sources)} YouTube sources...")
            yt_items = await self._youtube_adapter.fetch_content(
                identifiers=youtube_sources,
                start_time=start_time,
                end_time=now,
            )
            all_items.extend(yt_items)
            stats["items_fetched"]["youtube"] = len(yt_items)
            # Count videos with transcripts
            transcripts_count = sum(1 for item in yt_items if item.metrics.get("has_transcript"))
            stats["transcripts_fetched"] = transcripts_count
            logger.info(f"Fetched {len(yt_items)} YouTube items ({transcripts_count} with transcripts)")

        if not all_items:
            return {
                "summary": "No content found from your sources in the specified time range.",
                "items": [],
                "recommendations": [],
                "stats": stats,
            }

        # Store content in vector store for future semantic search
        logger.info("Storing content in vector store...")
        stored_count = 0
        for item in all_items:
            try:
                content_id = await self._vectorstore.store_content(
                    platform=item.platform,
                    platform_id=item.platform_id,
                    source_id=item.source_identifier,
                    source_name=item.source_name,
                    content=item.content,
                    url=item.url,
                    metrics=item.metrics,
                    published_at=item.posted_at,
                )
                if content_id:
                    stored_count += 1
            except Exception as e:
                logger.warning(f"Failed to store content in vector store: {e}")
        stats["items_stored_vectorstore"] = stored_count
        logger.info(f"Stored {stored_count} items in vector store")

        # Sort by score (already done in adapter, but ensure consistency)
        all_items.sort(key=lambda x: x.compute_score(), reverse=True)

        # Generate summary
        logger.info("Generating AI summary...")
        summary = await self._summarizer.summarize_content(all_items)

        # Generate recommendations
        logger.info("Generating recommendations...")
        recommendations = await self._summarizer.generate_recommendations(
            items=all_items,
            current_sources=x_sources or [],
        )

        return {
            "summary": summary,
            "items": [self._item_to_dict(item) for item in all_items[:20]],
            "recommendations": recommendations,
            "stats": stats,
        }

    def _item_to_dict(self, item: ContentItem) -> dict:
        """Convert ContentItem to serializable dict."""
        return {
            "platform": item.platform,
            "platform_id": item.platform_id,
            "source": item.source_identifier,
            "source_name": item.source_name,
            "content": item.content,
            "url": item.url,
            "metrics": item.metrics,
            "score": item.compute_score(),
            "posted_at": item.posted_at.isoformat(),
        }
