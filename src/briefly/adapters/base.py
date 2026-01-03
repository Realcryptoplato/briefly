"""Base adapter interface for all platforms."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ContentItem:
    """A piece of content from any platform."""

    platform: str
    platform_id: str  # Tweet ID, video ID, etc.
    source_identifier: str  # Username, channel handle
    source_name: str | None
    content: str
    url: str | None
    metrics: dict[str, Any]  # likes, retweets, views, etc.
    posted_at: datetime

    def compute_score(self) -> float:
        """Compute engagement score for ranking."""
        # Default: sum of all numeric metrics
        score = 0.0
        for key, value in self.metrics.items():
            if isinstance(value, (int, float)):
                # Weight different metrics differently
                weight = 1.0
                if key in ("like_count", "likes"):
                    weight = 1.0
                elif key in ("retweet_count", "retweets", "shares"):
                    weight = 2.0
                elif key in ("reply_count", "comments"):
                    weight = 1.5
                elif key in ("view_count", "views", "impression_count"):
                    weight = 0.01  # Views are usually much higher
                score += value * weight
        return score


class BaseAdapter(ABC):
    """Abstract base class for platform adapters."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform name (e.g., 'x', 'youtube')."""
        pass

    @abstractmethod
    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """
        Look up a user/channel by identifier.

        Returns dict with at least 'id' and 'name' keys, or None if not found.
        """
        pass

    @abstractmethod
    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """
        Fetch content from specified sources within time range.

        Args:
            identifiers: List of source identifiers (usernames, channel IDs)
            start_time: Start of time range
            end_time: End of time range

        Returns:
            List of ContentItem objects, deduplicated and sorted by score
        """
        pass

    async def validate_identifier(self, identifier: str) -> bool:
        """Check if an identifier is valid on this platform."""
        user = await self.lookup_user(identifier)
        return user is not None
