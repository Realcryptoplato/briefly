"""YouTube adapter using Data API v3."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.core.config import get_settings
from briefly.core.cache import get_user_cache

logger = logging.getLogger(__name__)


def fetch_transcript(video_id: str, max_chars: int | None = None) -> str | None:
    """
    Fetch transcript for a YouTube video.

    Uses youtube-transcript-api which doesn't require an API key.
    Works with auto-generated captions too.

    Args:
        video_id: YouTube video ID
        max_chars: Max characters to return (None = no limit, full transcript)

    Returns:
        Transcript text or None if unavailable
    """
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)

        # Prefer English transcripts
        transcript = None
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
        except NoTranscriptFound:
            # Fall back to auto-generated
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
            except NoTranscriptFound:
                # Try any available transcript
                for t in transcript_list:
                    transcript = t
                    break

        if not transcript:
            return None

        # Fetch the actual transcript entries
        entries = transcript.fetch()

        # Combine all text entries (entries are objects with .text attribute)
        full_text = ' '.join(entry.text for entry in entries)

        # Optionally truncate
        if max_chars and len(full_text) > max_chars:
            full_text = full_text[:max_chars] + '...'

        return full_text

    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        logger.debug(f"No transcript available for {video_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error fetching transcript for {video_id}: {e}")
        return None


class YouTubeAdapter(BaseAdapter):
    """
    YouTube adapter using Data API v3.

    Much more generous than X:
    - 10,000 units/day free quota
    - Channel lookup: 1 unit
    - Video list: 1 unit per request
    - No OAuth needed for public data

    Key feature: Can fetch ANY channel's public subscriptions
    with just an API key (no OAuth required).
    """

    platform_name = "youtube"

    def __init__(self) -> None:
        settings = get_settings()
        api_key = getattr(settings, 'youtube_api_key', None)

        if not api_key:
            logger.warning("No YOUTUBE_API_KEY configured")
            self._youtube = None
        else:
            self._youtube = build('youtube', 'v3', developerKey=api_key, cache_discovery=False)

    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """
        Look up a YouTube channel by handle or ID.

        Accepts:
        - @handle (e.g., @mkbhd)
        - Channel ID (e.g., UCBcRF18a7Qf58cCRy5xuWwQ)
        - Custom URL name
        """
        if not self._youtube:
            logger.error("YouTube API not configured")
            return None

        # Check cache first
        cache = get_user_cache()
        cache_key = f"yt:{identifier.lower().lstrip('@')}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for YouTube {identifier}")
            return cached

        try:
            # Try as handle first (@username)
            if identifier.startswith('@'):
                handle = identifier[1:]
                response = self._youtube.channels().list(
                    part='snippet,contentDetails,statistics',
                    forHandle=handle
                ).execute()
            elif identifier.startswith('UC'):
                # Looks like a channel ID
                response = self._youtube.channels().list(
                    part='snippet,contentDetails,statistics',
                    id=identifier
                ).execute()
            else:
                # Try as custom URL or search
                response = self._youtube.search().list(
                    part='snippet',
                    q=identifier,
                    type='channel',
                    maxResults=1
                ).execute()

                if response.get('items'):
                    channel_id = response['items'][0]['snippet']['channelId']
                    response = self._youtube.channels().list(
                        part='snippet,contentDetails,statistics',
                        id=channel_id
                    ).execute()

            if response.get('items'):
                channel = response['items'][0]
                result = {
                    "id": channel['id'],
                    "name": channel['snippet']['title'],
                    "handle": channel['snippet'].get('customUrl', ''),
                    "description": channel['snippet'].get('description', '')[:200],
                    "subscriber_count": int(channel['statistics'].get('subscriberCount', 0)),
                    "video_count": int(channel['statistics'].get('videoCount', 0)),
                    "uploads_playlist": channel['contentDetails']['relatedPlaylists']['uploads'],
                }

                # Cache it
                cache.set(cache_key, result)
                return result

            return None

        except HttpError as e:
            logger.error(f"YouTube API error looking up {identifier}: {e}")
            return None

    async def get_subscriptions(self, channel_identifier: str, max_results: int = 50) -> list[dict]:
        """
        Fetch a channel's public subscriptions.

        This is the killer feature - we can get ANY channel's subscriptions
        with just an API key (no OAuth required).

        Args:
            channel_identifier: Channel handle (@mkbhd) or ID
            max_results: Max subscriptions to return (default 50)

        Returns:
            List of subscribed channel info dicts
        """
        if not self._youtube:
            logger.error("YouTube API not configured")
            return []

        # First, get the channel ID
        channel = await self.lookup_user(channel_identifier)
        if not channel:
            logger.error(f"Channel not found: {channel_identifier}")
            return []

        channel_id = channel['id']
        subscriptions = []

        try:
            page_token = None
            while len(subscriptions) < max_results:
                request = self._youtube.subscriptions().list(
                    part='snippet',
                    channelId=channel_id,
                    maxResults=min(50, max_results - len(subscriptions)),
                    pageToken=page_token,
                )
                response = request.execute()

                for item in response.get('items', []):
                    snippet = item['snippet']
                    subscriptions.append({
                        'channel_id': snippet['resourceId']['channelId'],
                        'title': snippet['title'],
                        'description': snippet.get('description', '')[:200],
                        'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url'),
                    })

                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            logger.info(f"Found {len(subscriptions)} subscriptions for {channel['name']}")
            return subscriptions

        except HttpError as e:
            # 403 means subscriptions are private
            if e.resp.status == 403:
                logger.warning(f"Subscriptions are private for {channel_identifier}")
                return []
            logger.error(f"YouTube API error fetching subscriptions: {e}")
            return []

    async def import_subscriptions(self, channel_identifier: str) -> list[str]:
        """
        Import a user's subscriptions as sources.

        Returns list of channel handles/IDs that were imported.
        """
        subs = await self.get_subscriptions(channel_identifier)

        # Return channel IDs (we'll cache the full info when we fetch content)
        imported = []
        cache = get_user_cache()

        for sub in subs:
            channel_id = sub['channel_id']
            cache_key = f"yt:{channel_id}"

            # Pre-cache what we know
            cache.set(cache_key, {
                'id': channel_id,
                'name': sub['title'],
                'description': sub['description'],
            })

            imported.append(channel_id)

        return imported

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch recent videos from specified channels."""
        if not self._youtube:
            logger.error("YouTube API not configured")
            return []

        if not identifiers:
            return []

        all_items = []

        for identifier in identifiers:
            channel = await self.lookup_user(identifier)
            if not channel:
                logger.warning(f"YouTube channel not found: {identifier}")
                continue

            try:
                # Get recent videos from uploads playlist
                playlist_id = channel['uploads_playlist']

                response = self._youtube.playlistItems().list(
                    part='snippet,contentDetails',
                    playlistId=playlist_id,
                    maxResults=10,  # Last 10 videos
                ).execute()

                for item in response.get('items', []):
                    snippet = item['snippet']
                    video_id = snippet['resourceId']['videoId']
                    published_at = datetime.fromisoformat(
                        snippet['publishedAt'].replace('Z', '+00:00')
                    )

                    # Filter by time range
                    if published_at < start_time or published_at > end_time:
                        continue

                    # Get video stats (costs 1 unit)
                    video_resp = self._youtube.videos().list(
                        part='statistics',
                        id=video_id
                    ).execute()

                    stats = {}
                    if video_resp.get('items'):
                        stats = video_resp['items'][0].get('statistics', {})

                    # Check for cached transcript summary first
                    # Lazy import to avoid circular dependency
                    from briefly.services.transcripts import get_transcript_store
                    store = get_transcript_store()
                    cached_summary = store.get_summary(video_id)
                    transcript_content = ""
                    has_transcript = False
                    transcript_chars = 0

                    if cached_summary:
                        # Use the pre-processed summary
                        transcript_content = f"\n\n[AI Summary]: {cached_summary['summary']}"
                        if cached_summary.get('key_points'):
                            transcript_content += "\n\nKey points:\n" + "\n".join(
                                f"• {p}" for p in cached_summary['key_points'][:5]
                            )
                        has_transcript = True
                        logger.debug(f"Using cached summary for {video_id}")
                    else:
                        # Fetch full transcript and store it
                        transcript = fetch_transcript(video_id)  # No limit - full transcript
                        if transcript:
                            transcript_chars = len(transcript)
                            has_transcript = True
                            # Store the full transcript for background processing
                            store.save_transcript(
                                video_id=video_id,
                                transcript=transcript,
                                video_title=snippet['title'],
                                channel_name=channel['name'],
                            )
                            # For now, include a preview in the content
                            # Background job will create proper summary later
                            transcript_content = f"\n\n[Transcript ({transcript_chars:,} chars)]: {transcript[:2000]}..."
                            logger.info(f"Stored full transcript for {video_id}: {transcript_chars:,} chars")

                    # Build content with title, description, and transcript/summary
                    content = f"{snippet['title']}\n\n{snippet.get('description', '')[:300]}{transcript_content}"

                    all_items.append(
                        ContentItem(
                            platform="youtube",
                            platform_id=video_id,
                            source_identifier=channel.get('handle', channel['id']),
                            source_name=channel['name'],
                            content=content,
                            url=f"https://youtube.com/watch?v={video_id}",
                            metrics={
                                "view_count": int(stats.get('viewCount', 0)),
                                "like_count": int(stats.get('likeCount', 0)),
                                "comment_count": int(stats.get('commentCount', 0)),
                                "has_transcript": has_transcript,
                                "transcript_chars": transcript_chars,
                                "has_summary": cached_summary is not None,
                            },
                            posted_at=published_at,
                        )
                    )

                logger.info(f"Fetched {len(all_items)} videos from {channel['name']}")

            except HttpError as e:
                logger.error(f"YouTube API error fetching videos: {e}")

        # Sort by engagement
        all_items.sort(key=lambda x: x.compute_score(), reverse=True)
        return all_items
