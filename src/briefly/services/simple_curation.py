"""Simplified curation service using Grok and Gemini directly.

This is a streamlined approach that bypasses X API rate limits entirely:
- Uses Grok to summarize X accounts (no X API needed)
- Uses Gemini to summarize YouTube videos (no transcription needed)
- Uses Gemini to summarize podcast audio (no transcription needed)
- Generates briefings directly from LLM summaries
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from openai import OpenAI

from briefly.adapters.grok import get_grok_adapter
from briefly.adapters.gemini import get_gemini_adapter
from briefly.adapters.youtube import YouTubeAdapter
from briefly.core.config import get_settings
from briefly.core.cache import get_content_cache

logger = logging.getLogger(__name__)


class SimpleCurationService:
    """
    Simplified curation using LLM-native content access.

    Benefits over the complex approach:
    - No X API rate limits (Grok accesses X directly)
    - No YouTube transcription needed (Gemini processes videos)
    - Faster briefing generation
    - Lower cost (single calls vs multiple API calls)
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._grok = get_grok_adapter()
        self._gemini = get_gemini_adapter()
        self._youtube = YouTubeAdapter()  # Still need for channel -> video discovery
        self._content_cache = get_content_cache()

        # Use xAI for final briefing generation
        self._llm_client = OpenAI(
            api_key=self._settings.xai_api_key,
            base_url=self._settings.xai_base_url,
        )

    async def create_briefing(
        self,
        x_sources: list[str] | None = None,
        youtube_sources: list[str] | None = None,
        podcast_sources: list[dict] | None = None,
        hours_back: int = 24,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a briefing using LLM-native content access.

        Args:
            x_sources: List of X usernames
            youtube_sources: List of YouTube channel IDs
            podcast_sources: List of podcast dicts with feed_url, name
            hours_back: Hours to look back
            focus: Optional focus area (e.g., "AI", "crypto", "tech")

        Returns:
            Complete briefing dict
        """
        sections = []
        stats = {
            "x_sources": len(x_sources or []),
            "youtube_sources": len(youtube_sources or []),
            "podcast_sources": len(podcast_sources or []),
            "hours_back": hours_back,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Get X summaries via Grok
        x_summary = None
        if x_sources:
            logger.info(f"Summarizing {len(x_sources)} X accounts via Grok...")
            results = await self._grok.summarize_accounts_batch(
                usernames=x_sources,
                hours=hours_back,
                focus=focus,
            )
            if results and "error" not in results[0]:
                x_summary = results[0].get("combined_summary")
                sections.append({
                    "title": "X/Twitter Activity",
                    "platform": "x",
                    "content": x_summary,
                    "accounts": x_sources,
                })
                stats["x_summary_generated"] = True
            else:
                stats["x_error"] = results[0].get("error") if results else "Unknown error"

        # 2. Get YouTube summaries via Gemini
        yt_summaries = []
        if youtube_sources:
            logger.info(f"Fetching recent videos from {len(youtube_sources)} YouTube channels...")

            # First, get recent video IDs from channels
            now = datetime.now(timezone.utc)
            start_time = now - timedelta(hours=hours_back)

            try:
                videos = await self._youtube.fetch_content(
                    identifiers=youtube_sources,
                    start_time=start_time,
                    end_time=now,
                )
                stats["youtube_videos_found"] = len(videos)

                # Summarize top 5 videos via Gemini (with caching)
                for video in videos[:5]:
                    if video.url:
                        # Check cache first
                        cached = self._content_cache.get(video.url)
                        if cached:
                            logger.info(f"Using cached summary for video: {video.title or video.url}")
                            yt_summaries.append(cached)
                            stats["youtube_cache_hits"] = stats.get("youtube_cache_hits", 0) + 1
                        else:
                            logger.info(f"Summarizing video: {video.title or video.url}")
                            result = await self._gemini.summarize_video(
                                video_url=video.url,
                                focus=focus,
                                include_timestamps=False,
                            )
                            if "error" not in result:
                                video_summary = {
                                    "title": video.title,
                                    "channel": video.source_name,
                                    "url": video.url,
                                    "summary": result.get("summary"),
                                }
                                yt_summaries.append(video_summary)
                                # Cache the summary
                                self._content_cache.set(video.url, video_summary, "video")

                if yt_summaries:
                    sections.append({
                        "title": "YouTube Highlights",
                        "platform": "youtube",
                        "videos": yt_summaries,
                    })
                    stats["youtube_summaries_generated"] = len(yt_summaries)

            except Exception as e:
                logger.error(f"YouTube fetch failed: {e}")
                stats["youtube_error"] = str(e)

        # 3. Get Podcast summaries via Gemini
        podcast_summaries = []
        if podcast_sources:
            logger.info(f"Processing {len(podcast_sources)} podcasts via Gemini...")

            for podcast in podcast_sources[:5]:  # Limit to 5 podcasts
                feed_url = podcast.get("feed_url")
                name = podcast.get("name", "Unknown Podcast")

                if not feed_url:
                    continue

                try:
                    # Get latest episode from RSS feed
                    episode_url = await self._get_latest_episode_url(feed_url)

                    if episode_url:
                        # Check cache first - podcasts are expensive to process!
                        cached = self._content_cache.get(episode_url)
                        if cached:
                            logger.info(f"Using cached summary for podcast: {name}")
                            podcast_summaries.append(cached)
                            stats["podcast_cache_hits"] = stats.get("podcast_cache_hits", 0) + 1
                        else:
                            logger.info(f"Summarizing podcast: {name} (this may take a while...)")
                            result = await self._gemini.summarize_audio_url(
                                audio_url=episode_url,
                                title=name,
                                focus=focus,
                            )
                            if "error" not in result:
                                podcast_summary = {
                                    "title": result.get("title", name),
                                    "podcast_name": name,
                                    "summary": result.get("summary"),
                                    "episode_url": episode_url,
                                }
                                podcast_summaries.append(podcast_summary)
                                # Cache the summary - podcasts are expensive!
                                self._content_cache.set(episode_url, podcast_summary, "podcast")
                except Exception as e:
                    logger.error(f"Podcast {name} processing failed: {e}")

            if podcast_summaries:
                sections.append({
                    "title": "Podcast Highlights",
                    "platform": "podcast",
                    "episodes": podcast_summaries,
                })
                stats["podcast_summaries_generated"] = len(podcast_summaries)

        # 4. Generate final briefing summary
        if not sections:
            return {
                "summary": "No content found from your sources in the specified time range.",
                "sections": [],
                "stats": stats,
            }

        logger.info("Generating final briefing summary...")
        briefing_summary = await self._generate_briefing_summary(sections, focus)

        return {
            "summary": briefing_summary,
            "sections": sections,
            "stats": stats,
            "focus": focus,
        }

    async def _generate_briefing_summary(
        self,
        sections: list[dict],
        focus: str | None = None,
    ) -> str:
        """Generate a cohesive briefing summary from all sections."""
        # Build context from sections
        context_parts = []

        for section in sections:
            if section["platform"] == "x":
                context_parts.append(f"## X/Twitter\n{section['content']}")
            elif section["platform"] == "youtube":
                yt_text = "## YouTube\n"
                for vid in section.get("videos", []):
                    yt_text += f"\n### {vid['title']} ({vid['channel']})\n{vid['summary']}\n"
                context_parts.append(yt_text)
            elif section["platform"] == "podcast":
                pod_text = "## Podcasts\n"
                for ep in section.get("episodes", []):
                    pod_text += f"\n### {ep.get('title', ep['podcast_name'])} ({ep['podcast_name']})\n{ep['summary']}\n"
                context_parts.append(pod_text)

        context = "\n\n".join(context_parts)
        focus_clause = f" Focus especially on {focus}." if focus else ""

        prompt = f"""You are a media curator creating a daily briefing. Based on the following summaries from various sources, create a cohesive briefing that:

1. Highlights the most important stories/themes
2. Notes any connections between topics
3. Identifies what's trending or breaking
4. Provides actionable insights

{focus_clause}

Source summaries:
{context}

Create a brief (2-3 paragraphs) executive summary followed by 3-5 key takeaways."""

        try:
            response = self._llm_client.chat.completions.create(
                model=self._settings.xai_model,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Briefing summary generation failed: {e}")
            return f"Summary generation failed: {e}\n\nRaw content available in sections."

    async def _get_latest_episode_url(self, feed_url: str) -> str | None:
        """
        Get the latest episode audio URL from a podcast RSS feed.

        Args:
            feed_url: RSS feed URL

        Returns:
            Audio URL of the most recent episode, or None if not found
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(feed_url, follow_redirects=True)
                response.raise_for_status()

                # Parse RSS XML
                root = ET.fromstring(response.text)

                # Find the first item (most recent episode)
                # RSS uses different namespaces, try common patterns
                namespaces = {
                    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
                    "media": "http://search.yahoo.com/mrss/",
                }

                # Try to find enclosure in first item
                for item in root.iter("item"):
                    # Look for enclosure element (standard RSS for audio)
                    enclosure = item.find("enclosure")
                    if enclosure is not None:
                        url = enclosure.get("url")
                        if url:
                            return url

                    # Fallback: look for media:content
                    media_content = item.find("media:content", namespaces)
                    if media_content is not None:
                        url = media_content.get("url")
                        if url:
                            return url

                    # Only check first item
                    break

                logger.warning(f"No audio enclosure found in feed: {feed_url}")
                return None

        except Exception as e:
            logger.error(f"Failed to parse podcast feed {feed_url}: {e}")
            return None

    async def quick_briefing(
        self,
        x_accounts: list[str],
        hours: int = 24,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate a quick X-only briefing.

        This is the fastest path - just asks Grok to summarize accounts.
        """
        logger.info(f"Quick briefing for {len(x_accounts)} X accounts...")

        result = await self._grok.summarize_accounts_batch(
            usernames=x_accounts,
            hours=hours,
            focus=focus,
        )

        if result and "error" not in result[0]:
            return {
                "summary": result[0].get("combined_summary"),
                "accounts": x_accounts,
                "hours": hours,
                "focus": focus,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return {
                "summary": f"Failed to generate briefing: {result[0].get('error') if result else 'Unknown error'}",
                "error": True,
            }


# Singleton
_simple_curation: SimpleCurationService | None = None


def get_simple_curation() -> SimpleCurationService:
    """Get the simple curation service singleton."""
    global _simple_curation
    if _simple_curation is None:
        _simple_curation = SimpleCurationService()
    return _simple_curation
