"""Simple file-based cache for X user IDs and other slow-changing data."""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Cache file location (in project root)
CACHE_DIR = Path(__file__).parent.parent.parent.parent / ".cache"
USER_CACHE_FILE = CACHE_DIR / "x_users.json"


def _ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(exist_ok=True)


def _load_cache(cache_file: Path) -> dict:
    """Load cache from file."""
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load cache: {e}")
    return {}


def _save_cache(cache_file: Path, data: dict):
    """Save cache to file."""
    _ensure_cache_dir()
    try:
        cache_file.write_text(json.dumps(data, indent=2))
    except IOError as e:
        logger.warning(f"Failed to save cache: {e}")


class XUserCache:
    """
    Cache for X user ID lookups.

    Username → ID mappings never change, so we cache them permanently.
    This dramatically reduces API calls on the Free tier.
    """

    def __init__(self):
        self._cache = _load_cache(USER_CACHE_FILE)

    def get(self, username: str) -> dict[str, Any] | None:
        """Get cached user data by username."""
        key = username.lower().lstrip("@")
        entry = self._cache.get(key)
        if entry:
            logger.debug(f"Cache hit for @{username}")
            return entry.get("data")
        return None

    def get_many(self, usernames: list[str]) -> tuple[dict[str, dict], list[str]]:
        """
        Get cached users and return list of uncached usernames.

        Returns:
            (cached_users, uncached_usernames)
        """
        cached = {}
        uncached = []

        for username in usernames:
            key = username.lower().lstrip("@")
            entry = self._cache.get(key)
            if entry and entry.get("data"):
                cached[key] = entry["data"]
            else:
                uncached.append(username)

        logger.info(f"User cache: {len(cached)} hits, {len(uncached)} misses")
        return cached, uncached

    def set(self, username: str, data: dict[str, Any]):
        """Cache user data."""
        key = username.lower().lstrip("@")
        self._cache[key] = {
            "data": data,
            "cached_at": datetime.now().isoformat(),
        }
        self._save()

    def set_many(self, users: dict[str, dict[str, Any]]):
        """Cache multiple users at once."""
        for username, data in users.items():
            key = username.lower().lstrip("@")
            self._cache[key] = {
                "data": data,
                "cached_at": datetime.now().isoformat(),
            }
        self._save()
        logger.info(f"Cached {len(users)} users")

    def _save(self):
        """Persist cache to disk."""
        _save_cache(USER_CACHE_FILE, self._cache)

    def clear(self):
        """Clear all cached data."""
        self._cache = {}
        if USER_CACHE_FILE.exists():
            USER_CACHE_FILE.unlink()


# Singleton instance
_user_cache: XUserCache | None = None


def get_user_cache() -> XUserCache:
    """Get the user cache singleton."""
    global _user_cache
    if _user_cache is None:
        _user_cache = XUserCache()
    return _user_cache


# --- Content Summary Cache ---
# Caches processed podcast episodes and YouTube video summaries

CONTENT_CACHE_FILE = CACHE_DIR / "content_summaries.json"


class ContentSummaryCache:
    """
    Cache for processed content summaries (podcasts, videos).

    Avoids re-processing expensive content like long podcasts.
    Content is cached by URL with a configurable TTL.
    """

    def __init__(self, ttl_hours: int = 168):  # 1 week default
        self._cache = _load_cache(CONTENT_CACHE_FILE)
        self._ttl = timedelta(hours=ttl_hours)

    def get(self, url: str) -> dict[str, Any] | None:
        """Get cached summary by content URL."""
        entry = self._cache.get(url)
        if entry:
            # Check if expired
            cached_at = datetime.fromisoformat(entry.get("cached_at", "2000-01-01"))
            if datetime.now() - cached_at < self._ttl:
                logger.debug(f"Content cache hit for {url[:50]}...")
                return entry.get("data")
            else:
                logger.debug(f"Content cache expired for {url[:50]}...")
        return None

    def set(self, url: str, data: dict[str, Any], content_type: str = "podcast"):
        """Cache a content summary."""
        self._cache[url] = {
            "data": data,
            "content_type": content_type,
            "cached_at": datetime.now().isoformat(),
        }
        self._save()
        logger.info(f"Cached {content_type} summary for {url[:50]}...")

    def get_recent_urls(self, content_type: str | None = None, hours: int = 24) -> list[str]:
        """Get URLs of recently cached content."""
        cutoff = datetime.now() - timedelta(hours=hours)
        urls = []
        for url, entry in self._cache.items():
            if content_type and entry.get("content_type") != content_type:
                continue
            cached_at = datetime.fromisoformat(entry.get("cached_at", "2000-01-01"))
            if cached_at > cutoff:
                urls.append(url)
        return urls

    def _save(self):
        """Persist cache to disk."""
        _save_cache(CONTENT_CACHE_FILE, self._cache)

    def clear(self, older_than_hours: int | None = None):
        """Clear cache, optionally only entries older than specified hours."""
        if older_than_hours:
            cutoff = datetime.now() - timedelta(hours=older_than_hours)
            self._cache = {
                url: entry for url, entry in self._cache.items()
                if datetime.fromisoformat(entry.get("cached_at", "2000-01-01")) > cutoff
            }
        else:
            self._cache = {}
        self._save()

    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        podcast_count = sum(1 for e in self._cache.values() if e.get("content_type") == "podcast")
        video_count = sum(1 for e in self._cache.values() if e.get("content_type") == "video")
        return {
            "total": len(self._cache),
            "podcasts": podcast_count,
            "videos": video_count,
        }


# Singleton
_content_cache: ContentSummaryCache | None = None


def get_content_cache() -> ContentSummaryCache:
    """Get the content summary cache singleton."""
    global _content_cache
    if _content_cache is None:
        _content_cache = ContentSummaryCache()
    return _content_cache
