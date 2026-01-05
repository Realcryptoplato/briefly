"""X List management service for efficient timeline fetching.

Uses a persistent private list to fetch all sources' tweets in a single API call,
dramatically reducing API usage compared to individual timeline fetching.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import tweepy
from tweepy.asynchronous import AsyncClient

from briefly.adapters.base import ContentItem
from briefly.core.config import get_settings
from briefly.core.cache import get_user_cache

logger = logging.getLogger(__name__)

# State file for list sync status
STATE_DIR = Path(__file__).parent.parent.parent.parent / ".cache"
LIST_STATE_FILE = STATE_DIR / "x_list_state.json"


def _load_list_state() -> dict:
    """Load list state from file."""
    if LIST_STATE_FILE.exists():
        try:
            return json.loads(LIST_STATE_FILE.read_text())
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load list state: {e}")
    return {}


def _save_list_state(state: dict) -> None:
    """Save list state to file."""
    STATE_DIR.mkdir(exist_ok=True)
    try:
        LIST_STATE_FILE.write_text(json.dumps(state, indent=2))
    except IOError as e:
        logger.warning(f"Failed to save list state: {e}")


class RateLimitTracker:
    """Track rate limit usage for list member operations."""

    def __init__(self, window_minutes: int = 15, max_operations: int = 300):
        self.window_minutes = window_minutes
        self.max_operations = max_operations
        self._operations: list[datetime] = []

    def _clean_old_operations(self) -> None:
        """Remove operations outside the current window."""
        cutoff = datetime.now() - timedelta(minutes=self.window_minutes)
        self._operations = [t for t in self._operations if t > cutoff]

    def can_operate(self, count: int = 1) -> bool:
        """Check if we can perform count operations."""
        self._clean_old_operations()
        return len(self._operations) + count <= self.max_operations

    def record_operation(self, count: int = 1) -> None:
        """Record that operations were performed."""
        now = datetime.now()
        self._operations.extend([now] * count)

    def available_operations(self) -> int:
        """Return number of operations available in current window."""
        self._clean_old_operations()
        return self.max_operations - len(self._operations)


class XListManager:
    """
    Manages a persistent private X list for efficient timeline fetching.

    Instead of fetching individual user timelines (N API calls),
    fetches a single list timeline (1 API call) containing all sources.
    """

    LIST_NAME = "briefly_sources"
    LIST_DESCRIPTION = "Briefly 3000 curated sources"

    def __init__(self) -> None:
        settings = get_settings()

        # OAuth 1.0a client for list management (write operations)
        self._bot_client = tweepy.Client(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_key_secret,
            access_token=settings.x_access_token,
            access_token_secret=settings.x_access_token_secret,
            wait_on_rate_limit=False,
        )

        # Async client for read operations (Bearer token)
        self._async_client = AsyncClient(
            bearer_token=settings.x_bearer_token,
            wait_on_rate_limit=False,
        )

        # Rate limit tracking
        self._add_rate_tracker = RateLimitTracker(window_minutes=15, max_operations=300)
        self._remove_rate_tracker = RateLimitTracker(window_minutes=15, max_operations=300)

        # Load persisted state
        self._state = _load_list_state()
        self._list_id: str | None = self._state.get("list_id")

    def _save_state(self) -> None:
        """Persist current state."""
        self._state["list_id"] = self._list_id
        self._state["last_updated"] = datetime.now().isoformat()
        _save_list_state(self._state)

    async def get_list_id(self) -> str | None:
        """Get the list ID, checking if it still exists."""
        if self._list_id:
            # Verify list still exists
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self._bot_client.get_list(id=self._list_id)
                )
                if response.data:
                    return self._list_id
            except tweepy.errors.NotFound:
                logger.warning(f"List {self._list_id} no longer exists")
                self._list_id = None
                self._save_state()
            except tweepy.errors.TweepyException as e:
                logger.warning(f"Error checking list: {e}")

        return self._list_id

    async def ensure_list_exists(self) -> str:
        """Create the list if it doesn't exist, return list ID."""
        # Check if we already have a valid list
        if await self.get_list_id():
            return self._list_id

        # Look for existing list by name
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._bot_client.get_owned_lists(max_results=100)
            )

            if response.data:
                for lst in response.data:
                    if lst.name == self.LIST_NAME:
                        self._list_id = str(lst.id)
                        logger.info(f"Found existing list: {self._list_id}")
                        self._save_state()
                        return self._list_id
        except tweepy.errors.TweepyException as e:
            logger.warning(f"Error fetching owned lists: {e}")

        # Create new private list
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self._bot_client.create_list(
                    name=self.LIST_NAME,
                    description=self.LIST_DESCRIPTION,
                    private=True,
                )
            )
            self._list_id = str(response.data["id"])
            logger.info(f"Created new list: {self._list_id}")
            self._save_state()
            return self._list_id
        except tweepy.errors.TweepyException as e:
            logger.error(f"Failed to create list: {e}")
            raise

    async def get_list_members(self) -> list[dict[str, Any]]:
        """Get current list members."""
        if not self._list_id:
            return []

        members = []
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._bot_client.get_list_members(
                    id=self._list_id,
                    max_results=100,
                    user_fields=["id", "username", "name"],
                )
            )

            if response.data:
                for user in response.data:
                    members.append({
                        "id": str(user.id),
                        "username": user.username,
                        "name": user.name,
                    })
        except tweepy.errors.TweepyException as e:
            logger.error(f"Error fetching list members: {e}")

        return members

    async def add_member(self, user_id: str) -> bool:
        """
        Add a user to the list.

        Returns True on success, False if rate limited or failed.
        """
        if not self._list_id:
            await self.ensure_list_exists()

        if not self._add_rate_tracker.can_operate():
            logger.warning("Rate limit reached for list member additions")
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._bot_client.add_list_member(
                    id=self._list_id,
                    user_id=user_id,
                )
            )
            self._add_rate_tracker.record_operation()
            logger.debug(f"Added user {user_id} to list")
            return True
        except tweepy.errors.TooManyRequests:
            logger.warning("Rate limit hit while adding member")
            return False
        except tweepy.errors.TweepyException as e:
            logger.warning(f"Failed to add user {user_id}: {e}")
            return False

    async def remove_member(self, user_id: str) -> bool:
        """
        Remove a user from the list.

        Returns True on success, False if rate limited or failed.
        """
        if not self._list_id:
            return False

        if not self._remove_rate_tracker.can_operate():
            logger.warning("Rate limit reached for list member removals")
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._bot_client.remove_list_member(
                    id=self._list_id,
                    user_id=user_id,
                )
            )
            self._remove_rate_tracker.record_operation()
            logger.debug(f"Removed user {user_id} from list")
            return True
        except tweepy.errors.TooManyRequests:
            logger.warning("Rate limit hit while removing member")
            return False
        except tweepy.errors.TweepyException as e:
            logger.warning(f"Failed to remove user {user_id}: {e}")
            return False

    async def sync_sources(self, source_usernames: list[str]) -> dict[str, Any]:
        """
        Sync local sources with list membership.

        Adds missing members and removes stale ones.

        Returns sync result with added/removed/failed counts.
        """
        await self.ensure_list_exists()

        # Get user IDs for source usernames
        cache = get_user_cache()
        target_users: dict[str, str] = {}  # username -> user_id

        # Check cache first
        for username in source_usernames:
            key = username.lower().lstrip("@")
            cached = cache.get(key)
            if cached and cached.get("id"):
                target_users[key] = str(cached["id"])

        # Fetch uncached users
        uncached = [u for u in source_usernames if u.lower().lstrip("@") not in target_users]
        if uncached:
            try:
                for i in range(0, len(uncached), 100):
                    batch = [u.lstrip("@") for u in uncached[i:i + 100]]
                    response = await self._async_client.get_users(
                        usernames=batch,
                        user_fields=["id", "name", "username"],
                    )
                    if response.data:
                        for user in response.data:
                            key = user.username.lower()
                            target_users[key] = str(user.id)
                            cache.set(key, {
                                "id": str(user.id),
                                "username": user.username,
                                "name": user.name,
                            })
            except tweepy.errors.TweepyException as e:
                logger.error(f"Error fetching users: {e}")

        # Get current list members
        current_members = await self.get_list_members()
        current_by_username = {m["username"].lower(): m["id"] for m in current_members}

        # Determine changes needed
        target_usernames = set(target_users.keys())
        current_usernames = set(current_by_username.keys())

        to_add = target_usernames - current_usernames
        to_remove = current_usernames - target_usernames

        result = {
            "added": [],
            "removed": [],
            "failed": [],
            "already_synced": list(target_usernames & current_usernames),
        }

        # Add new members
        for username in to_add:
            user_id = target_users.get(username)
            if user_id:
                if await self.add_member(user_id):
                    result["added"].append(username)
                else:
                    result["failed"].append(username)

        # Remove stale members
        for username in to_remove:
            user_id = current_by_username.get(username)
            if user_id:
                if await self.remove_member(user_id):
                    result["removed"].append(username)

        # Update state
        self._state["last_sync"] = datetime.now().isoformat()
        self._state["member_count"] = len(target_usernames) - len(result["failed"])
        self._state["pending_adds"] = result["failed"]
        self._save_state()

        logger.info(
            f"List sync complete: {len(result['added'])} added, "
            f"{len(result['removed'])} removed, {len(result['failed'])} failed"
        )

        return result

    async def get_list_timeline(
        self,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 100,
    ) -> list[ContentItem]:
        """
        Fetch all tweets from list members in time range.

        This is the key efficiency gain: 1 API call for ALL sources.
        """
        if not self._list_id:
            await self.ensure_list_exists()

        items = []

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._bot_client.get_list_tweets(
                    id=self._list_id,
                    max_results=max_results,
                    start_time=start_time.isoformat(),
                    end_time=end_time.isoformat(),
                    tweet_fields=["created_at", "public_metrics", "author_id"],
                    expansions=["author_id"],
                    user_fields=["username", "name"],
                )
            )

            if not response.data:
                logger.info("No tweets found in list timeline")
                return items

            # Build author lookup
            authors = {}
            if response.includes and "users" in response.includes:
                for user in response.includes["users"]:
                    authors[user.id] = {
                        "username": user.username,
                        "name": user.name,
                    }

            # Convert tweets to ContentItems
            for tweet in response.data:
                author = authors.get(tweet.author_id, {})
                metrics = tweet.public_metrics or {}
                username = author.get("username", "unknown")

                items.append(
                    ContentItem(
                        platform="x",
                        platform_id=str(tweet.id),
                        source_identifier=username,
                        source_name=author.get("name"),
                        content=tweet.text,
                        url=f"https://x.com/{username}/status/{tweet.id}",
                        metrics={
                            "like_count": metrics.get("like_count", 0),
                            "retweet_count": metrics.get("retweet_count", 0),
                            "reply_count": metrics.get("reply_count", 0),
                            "impression_count": metrics.get("impression_count", 0),
                        },
                        posted_at=tweet.created_at,
                    )
                )

            logger.info(f"Fetched {len(items)} tweets from list timeline")

        except tweepy.errors.TooManyRequests:
            logger.warning("Rate limit hit fetching list timeline")
        except tweepy.errors.TweepyException as e:
            logger.error(f"Error fetching list timeline: {e}")

        return items

    def get_status(self) -> dict[str, Any]:
        """Get current list status for API."""
        return {
            "list_id": self._list_id,
            "list_name": self.LIST_NAME,
            "member_count": self._state.get("member_count", 0),
            "last_sync": self._state.get("last_sync"),
            "pending_adds": self._state.get("pending_adds", []),
            "available_add_operations": self._add_rate_tracker.available_operations(),
            "available_remove_operations": self._remove_rate_tracker.available_operations(),
        }


# Singleton instance
_list_manager: XListManager | None = None


def get_list_manager() -> XListManager:
    """Get the list manager singleton."""
    global _list_manager
    if _list_manager is None:
        _list_manager = XListManager()
    return _list_manager
