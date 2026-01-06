"""Gemini adapter for YouTube and podcast content.

Uses Gemini's ability to directly process YouTube URLs and audio files
for summarization, bypassing the need for transcription services.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import google.generativeai as genai

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)


class GeminiAdapter(BaseAdapter):
    """
    Gemini-powered content adapter for YouTube and podcasts.

    Key capabilities:
    - Summarize YouTube videos directly by URL (no transcription needed)
    - Process audio files up to 9.5 hours for podcast summarization
    - Extract key points, timestamps, and themes
    """

    platform_name = "youtube"

    def __init__(self) -> None:
        self._settings = get_settings()
        if self._settings.gemini_api_key:
            genai.configure(api_key=self._settings.gemini_api_key)
        self._model = genai.GenerativeModel(self._settings.gemini_model)

    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """
        Look up a YouTube channel.

        Note: Gemini can't list channel videos, so we still need the YouTube API
        for channel discovery. This method is a pass-through.
        """
        # For now, just return basic info - actual lookup requires YouTube API
        channel = identifier.lstrip("@")
        return {
            "id": channel,
            "name": channel,
            "platform": "youtube",
        }

    async def summarize_video(
        self,
        video_url: str,
        focus: str | None = None,
        include_timestamps: bool = True,
    ) -> dict[str, Any]:
        """
        Summarize a YouTube video directly by URL.

        Gemini can process YouTube videos without needing transcription.

        Args:
            video_url: Full YouTube URL or video ID
            focus: Optional focus area for the summary
            include_timestamps: Whether to include key timestamps

        Returns:
            Dict with summary, key_points, and metadata
        """
        # Normalize URL format
        if not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        focus_clause = f" Focus on {focus}." if focus else ""
        timestamp_clause = " Include timestamps for key moments." if include_timestamps else ""

        prompt = f"""Summarize this YouTube video.{focus_clause}{timestamp_clause}

Provide:
1. Brief overview (2-3 sentences)
2. Key points and takeaways (bullet points)
3. Notable quotes or moments
4. Who would find this valuable

Video: {video_url}"""

        try:
            response = self._model.generate_content(prompt)

            return {
                "video_url": video_url,
                "summary": response.text,
                "focus": focus,
                "model": self._settings.gemini_model,
                "fetched_at": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Gemini video summarize failed for {video_url}: {e}")
            return {
                "video_url": video_url,
                "summary": f"Failed to summarize video: {e}",
                "error": str(e),
            }

    async def summarize_videos_batch(
        self,
        video_urls: list[str],
        focus: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Summarize multiple YouTube videos.

        Note: Currently processes sequentially. Could be parallelized.
        """
        results = []
        for url in video_urls:
            result = await self.summarize_video(url, focus=focus)
            results.append(result)
        return results

    async def summarize_audio(
        self,
        audio_path: str | Path,
        title: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """
        Summarize an audio file (podcast episode).

        Gemini can process audio up to 9.5 hours directly.

        Args:
            audio_path: Path to audio file (mp3, wav, etc.)
            title: Optional title for context
            focus: Optional focus area

        Returns:
            Dict with summary and key points
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            return {
                "audio_path": str(audio_path),
                "summary": f"Audio file not found: {audio_path}",
                "error": "File not found",
            }

        title_clause = f' titled "{title}"' if title else ""
        focus_clause = f" Focus on {focus}." if focus else ""

        prompt = f"""Summarize this podcast episode{title_clause}.{focus_clause}

Provide:
1. Episode overview (2-3 sentences)
2. Main topics discussed with timestamps
3. Key insights and takeaways
4. Notable quotes from speakers
5. Action items or recommendations mentioned"""

        try:
            # Upload audio file to Gemini
            audio_file = genai.upload_file(str(audio_path))

            # Generate summary
            response = self._model.generate_content([prompt, audio_file])

            # Clean up uploaded file
            try:
                audio_file.delete()
            except Exception:
                pass  # Non-critical

            return {
                "audio_path": str(audio_path),
                "title": title,
                "summary": response.text,
                "focus": focus,
                "model": self._settings.gemini_model,
                "fetched_at": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Gemini audio summarize failed for {audio_path}: {e}")
            return {
                "audio_path": str(audio_path),
                "title": title,
                "summary": f"Failed to summarize audio: {e}",
                "error": str(e),
            }

    async def summarize_audio_url(
        self,
        audio_url: str,
        title: str | None = None,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """
        Summarize audio from a URL (podcast feed).

        Note: Gemini may support direct URL processing for some formats.
        For large files, may need to download first.

        Args:
            audio_url: URL to audio file
            title: Optional title for context
            focus: Optional focus area

        Returns:
            Dict with summary and key points
        """
        title_clause = f' titled "{title}"' if title else ""
        focus_clause = f" Focus on {focus}." if focus else ""

        prompt = f"""Summarize this podcast episode{title_clause}.{focus_clause}

Provide:
1. Episode overview (2-3 sentences)
2. Main topics discussed
3. Key insights and takeaways
4. Notable quotes from speakers
5. Action items or recommendations mentioned

Audio: {audio_url}"""

        try:
            response = self._model.generate_content(prompt)

            return {
                "audio_url": audio_url,
                "title": title,
                "summary": response.text,
                "focus": focus,
                "model": self._settings.gemini_model,
                "fetched_at": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Gemini audio URL summarize failed for {audio_url}: {e}")
            return {
                "audio_url": audio_url,
                "title": title,
                "summary": f"Failed to summarize audio: {e}",
                "error": str(e),
            }

    async def extract_topics(
        self,
        content_url: str,
        num_topics: int = 5,
    ) -> dict[str, Any]:
        """
        Extract main topics from video or audio content.

        Useful for categorization and filtering.
        """
        prompt = f"""Extract the {num_topics} main topics discussed in this content.

For each topic, provide:
1. Topic name (2-4 words)
2. Brief description
3. Approximate timestamp range if applicable

Content: {content_url}"""

        try:
            response = self._model.generate_content(prompt)
            return {
                "content_url": content_url,
                "topics": response.text,
                "num_topics": num_topics,
                "model": self._settings.gemini_model,
            }
        except Exception as e:
            logger.error(f"Gemini topic extraction failed: {e}")
            return {
                "content_url": content_url,
                "topics": f"Failed to extract topics: {e}",
                "error": str(e),
            }

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """
        Fetch summarized content from YouTube videos.

        Note: identifiers should be video URLs or IDs, not channel names.
        For channel-based fetching, use the YouTubeAdapter to get video IDs first.
        """
        if not identifiers:
            return []

        items = []
        for video_url in identifiers:
            result = await self.summarize_video(video_url)
            if "error" not in result:
                items.append(
                    ContentItem(
                        platform="youtube",
                        platform_id=video_url.split("v=")[-1] if "v=" in video_url else video_url,
                        source_identifier=video_url,
                        source_name="Gemini Summary",
                        content=result.get("summary", ""),
                        url=result.get("video_url"),
                        metrics={},
                        posted_at=datetime.now(),
                    )
                )

        return items


# Singleton instance
_gemini_adapter: GeminiAdapter | None = None


def get_gemini_adapter() -> GeminiAdapter:
    """Get the Gemini adapter singleton."""
    global _gemini_adapter
    if _gemini_adapter is None:
        _gemini_adapter = GeminiAdapter()
    return _gemini_adapter
