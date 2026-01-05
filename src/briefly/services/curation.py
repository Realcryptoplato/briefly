"""Main curation service that orchestrates the briefing pipeline."""

import logging
import re
from datetime import datetime, timedelta, timezone

from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter
from briefly.adapters.base import ContentItem
from briefly.services.summarization import SummarizationService
from briefly.services.vectorstore import VectorStore
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)


def extract_tags(content: str, title: str | None = None) -> list[str]:
    """
    Extract relevant tags from content using keyword extraction.

    Looks for:
    - Hashtags (#keyword)
    - $TICKER symbols
    - Common topic patterns
    """
    tags = set()
    text = f"{title or ''} {content}".lower()

    # Extract hashtags
    hashtags = re.findall(r'#(\w+)', text)
    tags.update(h.lower() for h in hashtags[:5])

    # Extract $TICKER symbols (crypto/stocks)
    tickers = re.findall(r'\$([A-Z]{2,5})', content)
    tags.update(t.upper() for t in tickers[:3])

    # Common topic keywords to look for
    topic_keywords = {
        'bitcoin': ['bitcoin', 'btc', 'satoshi'],
        'ethereum': ['ethereum', 'eth', 'vitalik'],
        'crypto': ['crypto', 'blockchain', 'web3', 'defi', 'nft'],
        'ai': ['artificial intelligence', 'machine learning', 'llm', 'gpt', 'claude', 'openai', 'anthropic'],
        'tech': ['technology', 'software', 'programming', 'coding'],
        'politics': ['politics', 'election', 'congress', 'senate', 'president', 'trump', 'biden'],
        'geopolitics': ['russia', 'china', 'ukraine', 'taiwan', 'nato', 'war'],
        'finance': ['stocks', 'market', 'trading', 'investment', 'fed', 'interest rate'],
        'science': ['science', 'research', 'study', 'discovery'],
        'health': ['health', 'medicine', 'covid', 'vaccine', 'fda'],
    }

    for tag, keywords in topic_keywords.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    return list(tags)[:8]  # Limit to 8 tags


def compute_time_bucket(posted_at: datetime) -> str:
    """Determine which time bucket an item belongs to."""
    now = datetime.now(timezone.utc)
    hours_ago = (now - posted_at).total_seconds() / 3600

    if hours_ago <= 6:
        return "breaking"
    elif hours_ago <= 24:
        return "today"
    elif hours_ago <= 48:
        return "yesterday"
    else:
        return "older"


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

        # Convert items to dicts with rich UI fields
        items_as_dicts = [self._item_to_dict(item) for item in all_items[:20]]

        # Create structured sections
        sections = self._create_structured_sections(items_as_dicts)

        # Collect all unique tags for the briefing
        all_tags = set()
        for item in items_as_dicts:
            all_tags.update(item.get("tags") or [])

        return {
            "summary": summary,
            "items": items_as_dicts,
            "sections": sections,
            "recommendations": recommendations,
            "stats": stats,
            "tags": list(all_tags)[:15],  # Top 15 tags for the briefing
        }

    def _item_to_dict(self, item: ContentItem) -> dict:
        """Convert ContentItem to serializable dict."""
        # Extract or use existing tags
        tags = item.tags or extract_tags(item.content, item.title)

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
            # Rich UI fields
            "thumbnail_url": item.thumbnail_url,
            "title": item.title,
            "tags": tags,
            "time_bucket": compute_time_bucket(item.posted_at),
            "drill_down_query": " ".join(tags[:3]) if tags else item.title or item.content[:50],
        }

    def _create_structured_sections(self, items: list[dict]) -> list[dict]:
        """
        Organize items into structured sections for rich UI display.

        Returns sections: breaking, top_stories, by_category
        """
        sections = []

        # Breaking news (last 6 hours)
        breaking_items = [i for i in items if i.get("time_bucket") == "breaking"]
        if breaking_items:
            sections.append({
                "title": "Breaking",
                "type": "breaking",
                "icon": "📰",
                "items": breaking_items[:5],
            })

        # Top stories by engagement (excluding breaking)
        non_breaking = [i for i in items if i.get("time_bucket") != "breaking"]
        top_stories = sorted(non_breaking, key=lambda x: x.get("score", 0), reverse=True)[:8]
        if top_stories:
            sections.append({
                "title": "Top Stories",
                "type": "top_stories",
                "icon": "📊",
                "items": top_stories,
            })

        # By category - group by most common tags
        tag_groups: dict[str, list[dict]] = {}
        for item in items:
            for tag in (item.get("tags") or [])[:2]:  # Use top 2 tags
                if tag not in tag_groups:
                    tag_groups[tag] = []
                if item not in tag_groups[tag]:
                    tag_groups[tag].append(item)

        # Get categories with at least 2 items
        category_sections = []
        for tag, tag_items in sorted(tag_groups.items(), key=lambda x: -len(x[1])):
            if len(tag_items) >= 2 and len(category_sections) < 5:
                category_sections.append({
                    "title": tag.replace("_", " ").title(),
                    "type": "category",
                    "tag": tag,
                    "items": tag_items[:5],
                })

        if category_sections:
            sections.append({
                "title": "By Category",
                "type": "categories",
                "icon": "🏷️",
                "categories": category_sections,
            })

        return sections
