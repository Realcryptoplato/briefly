"""X (Twitter) adapter with fallback strategies."""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any

import tweepy
from tweepy.asynchronous import AsyncClient

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.core.config import get_settings
from briefly.core.cache import get_user_cache

logger = logging.getLogger(__name__)


class XAdapter(BaseAdapter):
    """
    X (Twitter) adapter.

    Strategies (in order of preference):
    1. Temporary list (requires write permissions)
    2. Direct user timeline fetching (read-only, higher rate limit usage)
    """

    platform_name = "x"

    # Rate limit constants
    MAX_LIST_MEMBERS = 300
    ADD_MEMBER_DELAY = 0.1
    MAX_USERS_DIRECT = 15  # Limit for direct timeline fetching

    def __init__(self) -> None:
        settings = get_settings()

        # Client for bot operations (list management) - OAuth 1.0a
        # wait_on_rate_limit=False to fail fast instead of waiting 15 min
        self._bot_client = tweepy.Client(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_key_secret,
            access_token=settings.x_access_token,
            access_token_secret=settings.x_access_token_secret,
            wait_on_rate_limit=False,
        )

        # Async client for read operations - Bearer token
        self._async_client = AsyncClient(
            bearer_token=settings.x_bearer_token,
            wait_on_rate_limit=False,
        )

        # Track rate limit status
        self._rate_limited = False
        self._rate_limit_reset: datetime | None = None

        # Check if we have write permissions
        self._has_write_permissions: bool | None = None

    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """Look up X user by username."""
        try:
            username = identifier.lstrip("@")
            response = await self._async_client.get_user(
                username=username,
                user_fields=["id", "name", "username", "description", "public_metrics"],
            )

            if response.data:
                user = response.data
                return {
                    "id": user.id,
                    "username": user.username,
                    "name": user.name,
                    "description": getattr(user, "description", None),
                    "metrics": getattr(user, "public_metrics", {}),
                }
            return None
        except tweepy.errors.NotFound:
            logger.warning(f"X user not found: {identifier}")
            return None
        except tweepy.errors.TweepyException as e:
            logger.error(f"Error looking up X user {identifier}: {e}")
            return None

    async def lookup_users_batch(
        self, identifiers: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Look up multiple users by username (batched, with caching)."""
        cache = get_user_cache()

        # Check cache first
        cached_users, uncached_usernames = cache.get_many(identifiers)

        if not uncached_usernames:
            logger.info("All users found in cache!")
            return cached_users

        # Fetch uncached users from API
        results = dict(cached_users)
        new_users = {}

        for i in range(0, len(uncached_usernames), 100):
            batch = [u.lstrip("@") for u in uncached_usernames[i : i + 100]]
            try:
                response = await self._async_client.get_users(
                    usernames=batch,
                    user_fields=["id", "name", "username", "description"],
                )

                if response.data:
                    for user in response.data:
                        user_data = {
                            "id": user.id,
                            "username": user.username,
                            "name": user.name,
                        }
                        results[user.username.lower()] = user_data
                        new_users[user.username.lower()] = user_data
            except tweepy.errors.TweepyException as e:
                logger.error(f"Error in batch user lookup: {e}")

        # Cache new users
        if new_users:
            cache.set_many(new_users)

        return results

    async def _check_write_permissions(self) -> bool:
        """Check if we have write permissions by attempting to create a list."""
        if self._has_write_permissions is not None:
            return self._has_write_permissions

        try:
            def create_and_delete():
                resp = self._bot_client.create_list(
                    name=f"briefly_test_{uuid.uuid4().hex[:4]}",
                    private=True,
                )
                list_id = resp.data["id"]
                self._bot_client.delete_list(id=list_id)
                return True

            loop = asyncio.get_event_loop()
            self._has_write_permissions = await loop.run_in_executor(None, create_and_delete)
            logger.info("Write permissions confirmed")
        except tweepy.errors.Forbidden:
            logger.warning("No write permissions - using direct timeline fetching")
            self._has_write_permissions = False
        except Exception as e:
            logger.warning(f"Permission check failed: {e}")
            self._has_write_permissions = False

        return self._has_write_permissions

    async def _fetch_user_timeline(
        self,
        user_id: str,
        username: str,
        name: str | None,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch tweets from a single user's timeline."""
        items = []

        # Skip if already rate limited
        if self._rate_limited:
            return items

        try:
            response = await self._async_client.get_users_tweets(
                id=user_id,
                max_results=20,
                start_time=start_time,
                end_time=end_time,
                tweet_fields=["created_at", "public_metrics"],
                exclude=["retweets", "replies"],
            )

            if response.data:
                for tweet in response.data:
                    metrics = tweet.public_metrics or {}
                    items.append(
                        ContentItem(
                            platform="x",
                            platform_id=str(tweet.id),
                            source_identifier=username,
                            source_name=name,
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
        except tweepy.errors.TooManyRequests as e:
            self._rate_limited = True
            logger.warning(f"X API rate limit hit - skipping remaining users. Error: {e}")
        except tweepy.errors.TweepyException as e:
            logger.warning(f"Failed to fetch timeline for {username}: {e}")

        return items

    async def _fetch_via_direct_timelines(
        self,
        user_lookup: dict[str, dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch content by polling individual user timelines."""
        all_items = []
        users = list(user_lookup.values())[:self.MAX_USERS_DIRECT]
        fetched_count = 0

        logger.info(f"Fetching timelines for {len(users)} users (direct method)...")

        for i, user in enumerate(users):
            # Check if rate limited before each fetch
            if self._rate_limited:
                skipped = len(users) - i
                logger.warning(f"Rate limited - skipping {skipped} remaining users")
                break

            items = await self._fetch_user_timeline(
                user_id=str(user["id"]),
                username=user["username"],
                name=user.get("name"),
                start_time=start_time,
                end_time=end_time,
            )
            all_items.extend(items)
            fetched_count += 1
            logger.info(f"  [{i+1}/{len(users)}] @{user['username']}: {len(items)} tweets")

            # Small delay between requests to be respectful
            if i < len(users) - 1 and not self._rate_limited:
                await asyncio.sleep(0.5)

        if self._rate_limited:
            logger.warning(f"X fetch completed with rate limit: got {len(all_items)} tweets from {fetched_count}/{len(users)} users")
        else:
            logger.info(f"X fetch completed: {len(all_items)} tweets from {fetched_count} users")

        return all_items

    def _create_temp_list(self) -> int:
        """Create a temporary private list. Returns list ID."""
        list_name = f"briefly_temp_{uuid.uuid4().hex[:8]}"
        response = self._bot_client.create_list(
            name=list_name,
            private=True,
            description="Temporary list for Briefly 3000 curation",
        )
        return response.data["id"]

    def _delete_list(self, list_id: int) -> None:
        """Delete a list."""
        try:
            self._bot_client.delete_list(id=list_id)
        except tweepy.errors.TweepyException as e:
            logger.error(f"Failed to delete list {list_id}: {e}")

    def _add_list_members(self, list_id: int, user_ids: list[str]) -> int:
        """Add members to a list. Returns count of successful adds."""
        added = 0
        for user_id in user_ids[:self.MAX_LIST_MEMBERS]:
            try:
                self._bot_client.add_list_member(id=list_id, user_id=user_id)
                added += 1
                time.sleep(self.ADD_MEMBER_DELAY)
            except tweepy.errors.TweepyException as e:
                logger.warning(f"Failed to add user {user_id} to list: {e}")
        return added

    async def _fetch_list_tweets(
        self,
        list_id: int,
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch tweets from a list timeline."""
        items = []

        try:
            def fetch_sync():
                return self._bot_client.get_list_tweets(
                    id=list_id,
                    max_results=100,
                    start_time=start_time.isoformat(),
                    end_time=end_time.isoformat(),
                    tweet_fields=["created_at", "public_metrics", "author_id"],
                    expansions=["author_id"],
                    user_fields=["username", "name"],
                )

            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, fetch_sync)

            if not response.data:
                return items

            authors = {}
            if response.includes and "users" in response.includes:
                for user in response.includes["users"]:
                    authors[user.id] = {
                        "username": user.username,
                        "name": user.name,
                    }

            for tweet in response.data:
                author = authors.get(tweet.author_id, {})
                metrics = tweet.public_metrics or {}

                items.append(
                    ContentItem(
                        platform="x",
                        platform_id=str(tweet.id),
                        source_identifier=author.get("username", "unknown"),
                        source_name=author.get("name"),
                        content=tweet.text,
                        url=f"https://x.com/{author.get('username', 'i')}/status/{tweet.id}",
                        metrics={
                            "like_count": metrics.get("like_count", 0),
                            "retweet_count": metrics.get("retweet_count", 0),
                            "reply_count": metrics.get("reply_count", 0),
                            "impression_count": metrics.get("impression_count", 0),
                        },
                        posted_at=tweet.created_at,
                    )
                )

        except tweepy.errors.TweepyException as e:
            logger.error(f"Error fetching list tweets: {e}")

        return items

    async def _fetch_via_temp_list(
        self,
        user_lookup: dict[str, dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch content using temporary list strategy."""
        user_ids = [str(u["id"]) for u in user_lookup.values()]

        logger.info("Creating temporary list...")
        loop = asyncio.get_event_loop()
        list_id = await loop.run_in_executor(None, self._create_temp_list)
        logger.info(f"Created list {list_id}")

        try:
            logger.info(f"Adding {len(user_ids)} members to list...")
            await loop.run_in_executor(None, self._add_list_members, list_id, user_ids)

            logger.info("Fetching list timeline...")
            return await self._fetch_list_tweets(list_id, start_time, end_time)

        finally:
            logger.info(f"Deleting temporary list {list_id}...")
            await loop.run_in_executor(None, self._delete_list, list_id)

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """
        Fetch tweets from specified X accounts.

        Automatically chooses the best strategy based on permissions.
        Rate limits are handled gracefully - returns partial results if hit.
        """
        if not identifiers:
            return []

        # Reset rate limit state for fresh request
        self._rate_limited = False
        self._rate_limit_reset = None

        # Step 1: Look up user IDs
        logger.info(f"Looking up {len(identifiers)} X users...")
        user_lookup = await self.lookup_users_batch(identifiers)

        if not user_lookup:
            logger.warning("No valid X users found")
            return []

        logger.info(f"Found {len(user_lookup)} valid users")

        # Step 2: Choose fetching strategy
        has_write = await self._check_write_permissions()

        if has_write and len(user_lookup) > self.MAX_USERS_DIRECT:
            # Use list strategy for many users
            items = await self._fetch_via_temp_list(user_lookup, start_time, end_time)
        else:
            # Use direct timeline fetching
            items = await self._fetch_via_direct_timelines(user_lookup, start_time, end_time)

        # Deduplicate by tweet ID
        seen_ids = set()
        unique_items = []
        for item in items:
            if item.platform_id not in seen_ids:
                seen_ids.add(item.platform_id)
                unique_items.append(item)

        # Sort by engagement score
        unique_items.sort(key=lambda x: x.compute_score(), reverse=True)

        logger.info(f"Fetched {len(unique_items)} unique tweets")
        return unique_items
