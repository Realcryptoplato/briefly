"""Transcript storage and processing service."""

import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from briefly.core.config import get_settings

logger = logging.getLogger(__name__)

# File-based storage for transcripts and summaries
CACHE_DIR = Path(__file__).parent.parent.parent.parent / ".cache" / "transcripts"


class TranscriptStore:
    """
    Stores and retrieves video transcripts and their summaries.

    File structure:
    .cache/transcripts/
        {video_id}.json  # Full transcript + metadata
        {video_id}.summary.json  # Processed summary
    """

    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _transcript_path(self, video_id: str) -> Path:
        return CACHE_DIR / f"{video_id}.json"

    def _summary_path(self, video_id: str) -> Path:
        return CACHE_DIR / f"{video_id}.summary.json"

    def get_transcript(self, video_id: str) -> dict | None:
        """Get stored transcript for a video."""
        path = self._transcript_path(video_id)
        if path.exists():
            return json.loads(path.read_text())
        return None

    def save_transcript(
        self,
        video_id: str,
        transcript: str,
        video_title: str,
        channel_name: str,
        duration_seconds: int | None = None,
    ) -> None:
        """Save a full transcript."""
        data = {
            "video_id": video_id,
            "video_title": video_title,
            "channel_name": channel_name,
            "transcript": transcript,
            "char_count": len(transcript),
            "word_count": len(transcript.split()),
            "duration_seconds": duration_seconds,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        self._transcript_path(video_id).write_text(json.dumps(data, indent=2))
        logger.info(f"Saved transcript for {video_id}: {len(transcript)} chars")

    def get_summary(self, video_id: str) -> dict | None:
        """Get processed summary for a video."""
        path = self._summary_path(video_id)
        if path.exists():
            return json.loads(path.read_text())
        return None

    def save_summary(
        self,
        video_id: str,
        summary: str,
        key_points: list[str],
        topics: list[str],
        model_used: str,
    ) -> None:
        """Save a processed summary."""
        data = {
            "video_id": video_id,
            "summary": summary,
            "key_points": key_points,
            "topics": topics,
            "model_used": model_used,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        self._summary_path(video_id).write_text(json.dumps(data, indent=2))
        logger.info(f"Saved summary for {video_id}")

    def has_summary(self, video_id: str) -> bool:
        """Check if we have a processed summary."""
        return self._summary_path(video_id).exists()

    def list_pending(self) -> list[str]:
        """List video IDs that have transcripts but no summaries."""
        pending = []
        for path in CACHE_DIR.glob("*.json"):
            if path.name.endswith(".summary.json"):
                continue
            video_id = path.stem
            if not self.has_summary(video_id):
                pending.append(video_id)
        return pending


class TranscriptProcessor:
    """
    Processes transcripts using a cheaper model for summarization.

    This runs as a background task to pre-process transcripts,
    so the main briefing generation can use cached summaries.
    """

    def __init__(self):
        settings = get_settings()
        self._client = AsyncOpenAI(
            api_key=settings.xai_api_key,
            base_url=settings.xai_base_url,
        )
        self._model = settings.xai_model_cheap
        self._store = TranscriptStore()

    async def summarize_transcript(
        self,
        video_id: str,
        transcript: str,
        video_title: str,
        channel_name: str,
    ) -> dict:
        """
        Summarize a full transcript using the cheaper model.

        For very long transcripts, we chunk and summarize iteratively.
        """
        # Check if already summarized
        existing = self._store.get_summary(video_id)
        if existing:
            logger.debug(f"Using cached summary for {video_id}")
            return existing

        logger.info(f"Summarizing transcript for {video_id} ({len(transcript)} chars)")

        # For very long transcripts, chunk them
        max_chunk_chars = 30000  # ~7500 tokens

        if len(transcript) > max_chunk_chars:
            summary = await self._summarize_long_transcript(
                transcript, video_title, channel_name, max_chunk_chars
            )
        else:
            summary = await self._summarize_short_transcript(
                transcript, video_title, channel_name
            )

        # Save the summary
        self._store.save_summary(
            video_id=video_id,
            summary=summary["summary"],
            key_points=summary["key_points"],
            topics=summary["topics"],
            model_used=self._model,
        )

        return summary

    async def _summarize_short_transcript(
        self,
        transcript: str,
        video_title: str,
        channel_name: str,
    ) -> dict:
        """Summarize a transcript that fits in one context."""
        prompt = f"""Analyze this video transcript and provide:
1. A concise summary (2-3 paragraphs) of the main content
2. 5-7 key points or takeaways (as bullet points)
3. Main topics/themes discussed (3-5 topics)

Video: "{video_title}" by {channel_name}

Transcript:
{transcript}

Respond in JSON format:
{{
  "summary": "...",
  "key_points": ["point 1", "point 2", ...],
  "topics": ["topic 1", "topic 2", ...]
}}"""

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        try:
            return json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return {
                "summary": response.choices[0].message.content,
                "key_points": [],
                "topics": [],
            }

    async def _summarize_long_transcript(
        self,
        transcript: str,
        video_title: str,
        channel_name: str,
        chunk_size: int,
    ) -> dict:
        """
        Summarize a long transcript by chunking.

        Strategy:
        1. Split into chunks
        2. Summarize each chunk
        3. Combine chunk summaries into final summary
        """
        # Split into chunks (try to break at sentence boundaries)
        chunks = []
        current_pos = 0
        while current_pos < len(transcript):
            end_pos = min(current_pos + chunk_size, len(transcript))

            # Try to break at a sentence boundary
            if end_pos < len(transcript):
                # Look for period, question mark, or exclamation in last 500 chars
                search_start = max(end_pos - 500, current_pos)
                last_period = transcript.rfind('. ', search_start, end_pos)
                if last_period > search_start:
                    end_pos = last_period + 1

            chunks.append(transcript[current_pos:end_pos])
            current_pos = end_pos

        logger.info(f"Split transcript into {len(chunks)} chunks")

        # Summarize each chunk
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            prompt = f"""Summarize this section ({i+1}/{len(chunks)}) of a video transcript.
Focus on key information, arguments, and insights.

Video: "{video_title}" by {channel_name}

Section:
{chunk}

Provide a concise summary (1-2 paragraphs) of this section:"""

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            chunk_summaries.append(response.choices[0].message.content)

        # Combine chunk summaries into final summary
        combined = "\n\n".join(f"[Part {i+1}]: {s}" for i, s in enumerate(chunk_summaries))

        final_prompt = f"""Based on these section summaries of a video, provide:
1. A unified summary (2-3 paragraphs) covering the entire video
2. 5-7 key points or takeaways
3. Main topics/themes (3-5)

Video: "{video_title}" by {channel_name}

Section summaries:
{combined}

Respond in JSON format:
{{
  "summary": "...",
  "key_points": ["point 1", "point 2", ...],
  "topics": ["topic 1", "topic 2", ...]
}}"""

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": final_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
        )

        try:
            return json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            return {
                "summary": response.choices[0].message.content,
                "key_points": [],
                "topics": [],
            }

    async def process_pending(self, limit: int = 10) -> int:
        """
        Process pending transcripts that don't have summaries yet.

        Returns number of transcripts processed.
        """
        pending = self._store.list_pending()[:limit]
        processed = 0

        for video_id in pending:
            transcript_data = self._store.get_transcript(video_id)
            if not transcript_data:
                continue

            try:
                await self.summarize_transcript(
                    video_id=video_id,
                    transcript=transcript_data["transcript"],
                    video_title=transcript_data.get("video_title", "Unknown"),
                    channel_name=transcript_data.get("channel_name", "Unknown"),
                )
                processed += 1
            except Exception as e:
                logger.error(f"Error processing {video_id}: {e}")

        return processed


# Singleton instances
_store: TranscriptStore | None = None
_processor: TranscriptProcessor | None = None


def get_transcript_store() -> TranscriptStore:
    global _store
    if _store is None:
        _store = TranscriptStore()
    return _store


def get_transcript_processor() -> TranscriptProcessor:
    global _processor
    if _processor is None:
        _processor = TranscriptProcessor()
    return _processor
