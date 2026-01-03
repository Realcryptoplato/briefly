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
