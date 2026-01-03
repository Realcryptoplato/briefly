"""Platform adapters for fetching content."""

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter

__all__ = ["BaseAdapter", "ContentItem", "XAdapter", "YouTubeAdapter"]
