"""Podcast adapter using Taddy API."""

import logging
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)

# Progress callback type: (podcast_name, episode_name, stage, current, total)
# stage: "fetching_episodes", "downloading", "transcribing", "processing"
PodcastProgressCallback = Callable[[str, str, str, int, int], None]

TADDY_API_URL = "https://api.taddy.org"


class PodcastAdapter(BaseAdapter):
    """
    Podcast adapter using Taddy API.

    Fetches podcast episodes and transcripts from Taddy's GraphQL API.
    """

    platform_name = "podcast"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.taddy_api_key
        self._user_id = settings.taddy_user_id

        if not self._api_key or not self._user_id:
            logger.warning("Taddy API credentials not configured")

    def _get_headers(self) -> dict:
        """Get headers for Taddy API requests."""
        return {
            "Content-Type": "application/json",
            "X-USER-ID": self._user_id or "",
            "X-API-KEY": self._api_key or "",
        }

    async def search_podcasts(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search for podcasts by name or topic.

        Returns list of podcast metadata.
        """
        if not self._api_key:
            logger.error("Taddy API key not configured")
            return []

        # Use the correct Taddy GraphQL schema
        graphql_query = """
        query {
            search(term: "%s", filterForTypes: PODCASTSERIES, limitPerPage: %d) {
                searchId
                podcastSeries {
                    uuid
                    name
                    description
                    imageUrl
                    rssUrl
                    itunesId
                    authorName
                    genres
                }
            }
        }
        """ % (query.replace('"', '\\"'), limit)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    TADDY_API_URL,
                    headers=self._get_headers(),
                    json={"query": graphql_query},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                if "errors" in data:
                    logger.error(f"Taddy API error: {data['errors']}")
                    return []

                podcasts = data.get("data", {}).get("search", {}).get("podcastSeries", []) or []
                return [
                    {
                        "id": p["uuid"],
                        "name": p["name"],
                        "description": p.get("description", ""),
                        "image_url": p.get("imageUrl"),
                        "rss_url": p.get("rssUrl"),
                        "itunes_id": p.get("itunesId"),
                        "author": p.get("authorName"),
                        "genres": p.get("genres", []),
                    }
                    for p in podcasts
                ]
            except httpx.HTTPError as e:
                logger.error(f"Taddy API request failed: {e}")
                return []

    async def lookup_podcast(self, identifier: str) -> dict[str, Any] | None:
        """
        Look up a podcast by UUID, iTunes ID, or name.

        Returns podcast metadata if found.
        """
        if not self._api_key:
            logger.error("Taddy API key not configured")
            return None

        # Try to determine if it's a UUID, iTunes ID, or name
        # UUIDs are 36 chars with dashes
        if len(identifier) == 36 and "-" in identifier:
            return await self._lookup_by_uuid(identifier)
        elif identifier.isdigit():
            return await self._lookup_by_itunes_id(identifier)
        else:
            # Search by name and return first result
            results = await self.search_podcasts(identifier, limit=1)
            return results[0] if results else None

    async def _lookup_by_uuid(self, uuid: str) -> dict[str, Any] | None:
        """Look up podcast by Taddy UUID."""
        graphql_query = """
        query {
            getPodcastSeries(uuid: "%s") {
                uuid
                name
                description
                imageUrl
                rssUrl
                itunesId
                authorName
                genres
                totalEpisodesCount
            }
        }
        """ % uuid

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    TADDY_API_URL,
                    headers=self._get_headers(),
                    json={"query": graphql_query},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                if "errors" in data:
                    logger.error(f"Taddy API error: {data['errors']}")
                    return None

                p = data.get("data", {}).get("getPodcastSeries")
                if not p:
                    return None

                return {
                    "id": p["uuid"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "image_url": p.get("imageUrl"),
                    "rss_url": p.get("rssUrl"),
                    "itunes_id": p.get("itunesId"),
                    "author": p.get("authorName"),
                    "genres": p.get("genres", []),
                    "episode_count": p.get("totalEpisodesCount", 0),
                }
            except httpx.HTTPError as e:
                logger.error(f"Taddy API request failed: {e}")
                return None

    async def _lookup_by_itunes_id(self, itunes_id: str) -> dict[str, Any] | None:
        """Look up podcast by iTunes ID."""
        graphql_query = """
        query {
            getPodcastSeries(itunesId: %s) {
                uuid
                name
                description
                imageUrl
                rssUrl
                itunesId
                authorName
                genres
                totalEpisodesCount
            }
        }
        """ % itunes_id

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    TADDY_API_URL,
                    headers=self._get_headers(),
                    json={"query": graphql_query},
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

                if "errors" in data:
                    logger.error(f"Taddy API error: {data['errors']}")
                    return None

                p = data.get("data", {}).get("getPodcastSeries")
                if not p:
                    return None

                return {
                    "id": p["uuid"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "image_url": p.get("imageUrl"),
                    "rss_url": p.get("rssUrl"),
                    "itunes_id": p.get("itunesId"),
                    "author": p.get("authorName"),
                    "genres": p.get("genres", []),
                    "episode_count": p.get("totalEpisodesCount", 0),
                }
            except httpx.HTTPError as e:
                logger.error(f"Taddy API request failed: {e}")
                return None

    async def get_episodes(
        self,
        podcast_uuid: str,
        limit: int = 5,
        include_transcript: bool = True,
    ) -> list[dict]:
        """
        Get recent episodes from a podcast.

        Args:
            podcast_uuid: Taddy podcast UUID
            limit: Max episodes to fetch (default 5 for briefings)
            include_transcript: Whether to request transcript from Taddy

        Returns:
            List of episodes with transcript_status:
            - "available": Taddy has transcript ready
            - "audio_only": Has audio URL, can transcribe locally
            - "unavailable": No audio, cannot transcribe
        """
        if not self._api_key:
            return []

        # Try with transcript first if Pro plan, fall back to without
        episodes_data = await self._fetch_episodes(podcast_uuid, include_transcript=True)

        # If we got an error about Pro/Business requirement, try without transcript
        if episodes_data is None:
            logger.info("Taddy transcript requires Pro plan, fetching without transcripts")
            episodes_data = await self._fetch_episodes(podcast_uuid, include_transcript=False)

        if not episodes_data:
            return []

        # Slice to respect limit
        episodes = episodes_data[:limit]

        result = []
        for ep in episodes:
            transcript_text = ep.get("transcript")
            audio_url = ep.get("audioUrl")

            # Determine transcript status
            if transcript_text and len(transcript_text) > 50:
                transcript_status = "available"
            elif audio_url:
                transcript_status = "audio_only"
            else:
                transcript_status = "unavailable"

            result.append({
                "id": ep["uuid"],
                "name": ep["name"],
                "description": ep.get("description", ""),
                "published_at": ep.get("datePublished"),
                "duration": ep.get("duration"),
                "audio_url": audio_url,
                "image_url": ep.get("imageUrl"),
                # Transcript info
                "transcript": transcript_text if transcript_status == "available" else None,
                "transcript_status": transcript_status,
                # Derived flags for easy checking
                "has_transcript": transcript_status == "available",
                "can_transcribe_locally": transcript_status == "audio_only",
            })

        return result

    async def _fetch_episodes(
        self,
        podcast_uuid: str,
        include_transcript: bool = True,
    ) -> list[dict] | None:
        """
        Internal method to fetch episodes from Taddy.

        Returns None if there's a Pro/Business requirement error (so caller can retry without transcript).
        Returns empty list for other errors.
        """
        # Build query with or without transcript field
        transcript_field = "transcript" if include_transcript else ""
        graphql_query = """
        query {
            getPodcastSeries(uuid: "%s") {
                uuid
                name
                episodes {
                    uuid
                    name
                    description
                    datePublished
                    duration
                    audioUrl
                    imageUrl
                    %s
                }
            }
        }
        """ % (podcast_uuid, transcript_field)

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    TADDY_API_URL,
                    headers=self._get_headers(),
                    json={"query": graphql_query},
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

                if "errors" in data:
                    errors = data["errors"]
                    # Check if it's a Pro/Business requirement error
                    if any("Pro or Business" in str(e.get("message", "")) for e in errors):
                        return None  # Signal to retry without transcript
                    logger.error(f"Taddy API error: {errors}")
                    return []

                podcast_data = data.get("data", {}).get("getPodcastSeries", {})
                return podcast_data.get("episodes", []) or []

            except httpx.HTTPError as e:
                logger.error(f"Taddy API request failed: {e}")
                return []

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
        limit_per_podcast: int = 5,
        transcribe_locally: bool = False,
        progress_callback: PodcastProgressCallback | None = None,
    ) -> list[ContentItem]:
        """
        Fetch podcast episodes from specified podcasts.

        Args:
            identifiers: List of podcast UUIDs
            start_time: Only include episodes after this time
            end_time: Only include episodes before this time
            limit_per_podcast: Max episodes per podcast (default 5)
            transcribe_locally: Whether to use local Whisper for audio_only episodes
            progress_callback: Optional callback for progress updates
        """
        all_items = []
        stats = {"available": 0, "audio_only": 0, "unavailable": 0, "transcribed_locally": 0}
        total_podcasts = len(identifiers)

        def emit_progress(podcast_name: str, episode_name: str, stage: str, current: int, total: int) -> None:
            """Helper to emit progress if callback provided."""
            if progress_callback:
                progress_callback(podcast_name, episode_name, stage, current, total)

        for podcast_idx, podcast_id in enumerate(identifiers):
            # Look up podcast info first
            podcast = await self.lookup_podcast(podcast_id)
            if not podcast:
                logger.warning(f"Podcast not found: {podcast_id}")
                continue

            podcast_name = podcast.get("name", podcast_id)[:40]
            emit_progress(podcast_name, "", "fetching_episodes", podcast_idx + 1, total_podcasts)

            # Get episodes with transcripts
            episodes = await self.get_episodes(
                podcast_uuid=podcast["id"],
                limit=limit_per_podcast,
            )

            total_episodes = len(episodes)
            for ep_idx, ep in enumerate(episodes):
                # Parse published date (Taddy returns Unix timestamp)
                published_at = None
                if ep.get("published_at"):
                    try:
                        # Taddy returns Unix timestamp as integer
                        if isinstance(ep["published_at"], int):
                            published_at = datetime.fromtimestamp(ep["published_at"], tz=timezone.utc)
                        else:
                            published_at = datetime.fromisoformat(
                                str(ep["published_at"]).replace("Z", "+00:00")
                            )
                    except (ValueError, OSError):
                        pass

                # Skip if outside time range
                if published_at:
                    if published_at < start_time or published_at > end_time:
                        continue

                transcript_status = ep.get("transcript_status", "unavailable")
                stats[transcript_status] = stats.get(transcript_status, 0) + 1

                # Determine content based on transcript status
                content = None
                episode_name = ep.get("name", "Episode")[:50]

                if transcript_status == "available":
                    emit_progress(podcast_name, episode_name, "processing", ep_idx + 1, total_episodes)
                    content = ep.get("transcript")
                elif transcript_status == "audio_only" and transcribe_locally:
                    # Local transcription requested
                    try:
                        from briefly.services.transcription import transcribe_podcast_episode
                        emit_progress(podcast_name, episode_name, "transcribing", ep_idx + 1, total_episodes)
                        logger.info(f"Transcribing locally: {ep['name'][:50]}...")
                        content = await transcribe_podcast_episode(ep["audio_url"])
                        stats["transcribed_locally"] += 1
                    except Exception as e:
                        logger.warning(f"Local transcription failed: {e}")
                        content = ep.get("description", "")
                else:
                    # Fall back to description
                    emit_progress(podcast_name, episode_name, "processing", ep_idx + 1, total_episodes)
                    content = ep.get("description", "")

                if content:
                    all_items.append(
                        ContentItem(
                            platform="podcast",
                            platform_id=ep["id"],
                            source_identifier=podcast["id"],
                            source_name=podcast["name"],
                            content=content,
                            url=ep.get("audio_url"),
                            metrics={
                                "duration": ep.get("duration"),
                                "transcript_status": transcript_status,
                                "has_transcript": transcript_status == "available",
                                "can_transcribe_locally": ep.get("can_transcribe_locally", False),
                            },
                            posted_at=published_at or datetime.now(timezone.utc),
                        )
                    )

        logger.info(
            f"Fetched {len(all_items)} podcast episodes. "
            f"Transcripts: {stats['available']} available, {stats['audio_only']} audio-only, "
            f"{stats['transcribed_locally']} transcribed locally"
        )
        return all_items

    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """Alias for lookup_podcast to match base adapter interface."""
        return await self.lookup_podcast(identifier)
