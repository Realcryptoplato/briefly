"""Main curation service that orchestrates the briefing pipeline."""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from briefly.adapters.x import XAdapter
from briefly.adapters.youtube import YouTubeAdapter
from briefly.adapters.podcast import PodcastAdapter
from briefly.adapters.base import ContentItem
from briefly.services.summarization import SummarizationService
from briefly.services.vectorstore import VectorStore
from briefly.services.transcripts import get_transcript_store, get_transcript_processor
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)

# Type for progress callback: (step_name, current, total, elapsed_seconds, media_status)
# media_status is a dict with 'x', 'youtube', 'podcasts' keys, each with 'status' and optional 'count'
ProgressCallback = Callable[[str, int, int, float, dict | None], None]


class CurationService:
    """
    Main curation pipeline.

    Orchestrates:
    1. Fetching content from all platforms
    2. Deduping and ranking
    3. Summarization
    4. (Future) Storing briefings
    """

    def __init__(self) -> None:
        self._x_adapter = XAdapter()
        self._youtube_adapter = YouTubeAdapter()
        self._podcast_adapter = PodcastAdapter()
        self._summarizer = SummarizationService()
        self._vectorstore = VectorStore()

    async def create_briefing(
        self,
        x_sources: list[str] | None = None,
        youtube_sources: list[str] | None = None,
        podcast_sources: list[str] | None = None,
        hours_back: int = 24,
        transcribe_locally: bool = True,  # Use local Whisper for podcasts without transcripts
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        """
        Create a briefing from specified sources.

        Args:
            x_sources: List of X usernames to curate from
            youtube_sources: List of YouTube channel IDs to curate from
            podcast_sources: List of podcast UUIDs to curate from
            hours_back: How many hours back to look for content
            transcribe_locally: Use local Whisper for podcasts without Taddy transcripts

        Returns:
            Dict with 'summary', 'items', 'recommendations', 'stats'
        """
        now = datetime.now(timezone.utc)
        start_time_range = now - timedelta(hours=hours_back)
        pipeline_start = time.time()

        # Track per-media status
        media_status = {
            "x": {"status": "pending", "count": 0, "sources": len(x_sources or [])},
            "youtube": {"status": "pending", "count": 0, "sources": len(youtube_sources or [])},
            "podcasts": {"status": "pending", "count": 0, "sources": len(podcast_sources or [])},
        }

        def emit_progress(step: str, current: int = 0, total: int = 0) -> None:
            """Helper to emit progress if callback provided."""
            if progress_callback:
                elapsed = time.time() - pipeline_start
                progress_callback(step, current, total, elapsed, media_status)

        all_items: list[ContentItem] = []
        stats = {
            "sources": {
                "x": len(x_sources or []),
                "youtube": len(youtube_sources or []),
                "podcasts": len(podcast_sources or []),
            },
            "items_fetched": {"x": 0, "youtube": 0, "podcasts": 0},
            "time_range_hours": hours_back,
            "steps": [],  # Track each step's status
        }

        # Fetch from all sources IN PARALLEL
        emit_progress("Fetching media", 0, 3)
        logger.info("Fetching from all sources in parallel...")

        async def fetch_x():
            """Fetch X posts with timeout."""
            if not x_sources:
                media_status["x"]["status"] = "skipped"
                emit_progress("Fetching media", 0, 3)
                return []
            try:
                media_status["x"]["status"] = "fetching"
                emit_progress("Fetching media", 0, 3)
                result = await asyncio.wait_for(
                    self._x_adapter.fetch_content(
                        identifiers=x_sources,
                        start_time=start_time_range,
                        end_time=now,
                    ),
                    timeout=60.0,  # 1 minute timeout
                )
                media_status["x"]["status"] = "done"
                media_status["x"]["count"] = len(result)
                emit_progress("Fetching media", 1, 3)
                return result
            except asyncio.TimeoutError:
                logger.warning("X fetch timed out after 60s")
                media_status["x"]["status"] = "timeout"
                media_status["x"]["error"] = "Timed out - try fewer X sources"
                emit_progress("Fetching media", 1, 3)
                stats["steps"].append({"name": "fetch_x", "status": "timeout"})
                return []
            except Exception as e:
                error_msg = str(e)
                logger.error(f"X fetch failed: {error_msg}")
                media_status["x"]["status"] = "failed"
                # Provide helpful error message
                if "429" in error_msg or "TooManyRequests" in error_msg or "rate" in error_msg.lower():
                    media_status["x"]["error"] = "Rate limited - wait 15 min"
                else:
                    media_status["x"]["error"] = error_msg[:50]
                emit_progress("Fetching media", 1, 3)
                stats["steps"].append({"name": "fetch_x", "status": "failed", "error": error_msg})
                return []

        async def fetch_youtube():
            """Fetch YouTube videos."""
            if not youtube_sources:
                media_status["youtube"]["status"] = "skipped"
                emit_progress("Fetching media", 0, 3)
                return []
            try:
                media_status["youtube"]["status"] = "fetching"
                emit_progress("Fetching media", 0, 3)
                result = await asyncio.wait_for(
                    self._youtube_adapter.fetch_content(
                        identifiers=youtube_sources,
                        start_time=start_time_range,
                        end_time=now,
                    ),
                    timeout=120.0,  # 2 minute timeout
                )
                media_status["youtube"]["status"] = "done"
                media_status["youtube"]["count"] = len(result)
                emit_progress("Fetching media", 2, 3)
                return result
            except asyncio.TimeoutError:
                logger.warning("YouTube fetch timed out after 120s")
                media_status["youtube"]["status"] = "timeout"
                media_status["youtube"]["error"] = "Timed out fetching videos"
                emit_progress("Fetching media", 2, 3)
                stats["steps"].append({"name": "fetch_youtube", "status": "timeout"})
                return []
            except Exception as e:
                error_msg = str(e)
                logger.error(f"YouTube fetch failed: {error_msg}")
                media_status["youtube"]["status"] = "failed"
                media_status["youtube"]["error"] = error_msg[:50]
                emit_progress("Fetching media", 2, 3)
                stats["steps"].append({"name": "fetch_youtube", "status": "failed", "error": error_msg})
                return []

        async def fetch_podcasts():
            """Fetch podcast episodes (may include local transcription)."""
            if not podcast_sources:
                media_status["podcasts"]["status"] = "skipped"
                emit_progress("Fetching media", 0, 3)
                return []

            def podcast_progress_callback(
                podcast_name: str,
                episode_name: str,
                stage: str,
                current: int,
                total: int,
            ) -> None:
                """Update media status with detailed podcast progress."""
                media_status["podcasts"]["status"] = "fetching"
                media_status["podcasts"]["stage"] = stage
                media_status["podcasts"]["current_podcast"] = podcast_name
                media_status["podcasts"]["current_episode"] = episode_name
                media_status["podcasts"]["episode_current"] = current
                media_status["podcasts"]["episode_total"] = total
                emit_progress("Fetching media", 0, 3)

            try:
                media_status["podcasts"]["status"] = "fetching"
                emit_progress("Fetching media", 0, 3)
                result = await self._podcast_adapter.fetch_content(
                    identifiers=podcast_sources,
                    start_time=start_time_range,
                    end_time=now,
                    limit_per_podcast=5,
                    transcribe_locally=transcribe_locally,
                    progress_callback=podcast_progress_callback,
                )
                media_status["podcasts"]["status"] = "done"
                media_status["podcasts"]["count"] = len(result)
                # Clear stage info when done
                media_status["podcasts"]["stage"] = None
                media_status["podcasts"]["current_podcast"] = None
                media_status["podcasts"]["current_episode"] = None
                emit_progress("Fetching media", 3, 3)
                return result
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Podcast fetch failed: {error_msg}")
                media_status["podcasts"]["status"] = "failed"
                media_status["podcasts"]["error"] = error_msg[:50]
                emit_progress("Fetching media", 3, 3)
                stats["steps"].append({"name": "fetch_podcasts", "status": "failed", "error": error_msg})
                return []

        # Run all fetches in parallel
        x_items, yt_items, podcast_items = await asyncio.gather(
            fetch_x(),
            fetch_youtube(),
            fetch_podcasts(),
        )

        # Process X results
        if x_items:
            all_items.extend(x_items)
            stats["items_fetched"]["x"] = len(x_items)
            stats["steps"].append({
                "name": "fetch_x",
                "status": "completed",
                "count": len(x_items),
                "sources": len(x_sources or []),
            })
            logger.info(f"Fetched {len(x_items)} X items")

        # Process YouTube results
        if yt_items:
            all_items.extend(yt_items)
            stats["items_fetched"]["youtube"] = len(yt_items)
            transcripts_count = sum(1 for item in yt_items if item.metrics.get("has_transcript"))
            stats["transcripts_fetched"] = transcripts_count
            stats["steps"].append({
                "name": "fetch_youtube",
                "status": "completed",
                "count": len(yt_items),
                "transcripts": transcripts_count,
                "sources": len(youtube_sources or []),
            })
            logger.info(f"Fetched {len(yt_items)} YouTube items ({transcripts_count} with transcripts)")

        # Process Podcast results
        if podcast_items:
            all_items.extend(podcast_items)
            stats["items_fetched"]["podcasts"] = len(podcast_items)
            taddy_transcripts = sum(
                1 for item in podcast_items
                if item.metrics.get("transcript_status") == "available"
            )
            local_transcripts = sum(
                1 for item in podcast_items
                if item.metrics.get("transcript_status") == "audio_only"
                and len(item.content) > 500
            )
            stats["podcast_transcripts"] = {
                "taddy": taddy_transcripts,
                "local": local_transcripts,
                "description_only": len(podcast_items) - taddy_transcripts - local_transcripts,
            }
            stats["steps"].append({
                "name": "fetch_podcasts",
                "status": "completed",
                "count": len(podcast_items),
                "transcripts_taddy": taddy_transcripts,
                "transcripts_local": local_transcripts,
                "sources": len(podcast_sources or []),
            })
            logger.info(
                f"Fetched {len(podcast_items)} podcast episodes "
                f"({taddy_transcripts} Taddy, {local_transcripts} local transcripts)"
            )

        emit_progress(
            f"Fetched: {len(x_items)} X, {len(yt_items)} YT, {len(podcast_items)} podcasts",
            3, 3
        )

        if not all_items:
            return {
                "summary": "No content found from your sources in the specified time range.",
                "items": [],
                "recommendations": [],
                "stats": stats,
            }

        # Store content in vector store for future semantic search
        emit_progress("Archiving to vector store", 0, len(all_items))
        logger.info("Storing content in vector store...")
        stored_count = 0
        for idx, item in enumerate(all_items):
            try:
                content_id = await self._vectorstore.store_content(
                    platform=item.platform,
                    platform_id=item.platform_id,
                    source_id=item.source_identifier,
                    source_name=item.source_name,
                    content=item.content,
                    url=item.url,
                    metrics=item.metrics,
                    published_at=item.posted_at,
                )
                if content_id:
                    stored_count += 1
                emit_progress("Archiving to vector store", idx + 1, len(all_items))
            except Exception as e:
                logger.warning(f"Failed to store content in vector store: {e}")
        stats["items_stored_vectorstore"] = stored_count
        stats["steps"].append({
            "name": "archive_vectorstore",
            "status": "completed",
            "stored": stored_count,
            "total": len(all_items),
        })
        logger.info(f"Stored {stored_count} items in vector store")

        # Process any pending transcripts (summarize for future retrieval)
        transcript_store = get_transcript_store()
        pending_transcripts = transcript_store.list_pending()
        if pending_transcripts:
            emit_progress("Summarizing transcripts", 0, len(pending_transcripts))
            logger.info(f"Processing {len(pending_transcripts)} pending transcripts...")
            try:
                processor = get_transcript_processor()
                processed = await processor.process_pending(limit=20)  # Process up to 20
                stats["transcripts_summarized"] = processed
                stats["steps"].append({
                    "name": "summarize_transcripts",
                    "status": "completed",
                    "processed": processed,
                    "pending": len(pending_transcripts),
                })
                emit_progress("Summarized transcripts", processed, processed)
                logger.info(f"Summarized {processed} transcripts")
            except Exception as e:
                logger.warning(f"Failed to process transcripts: {e}")
                stats["steps"].append({
                    "name": "summarize_transcripts",
                    "status": "failed",
                    "error": str(e),
                })

        # Sort by score (already done in adapter, but ensure consistency)
        all_items.sort(key=lambda x: x.compute_score(), reverse=True)

        # Generate summary
        emit_progress("Generating AI summary", 0, 1)
        logger.info("Generating AI summary...")
        summary = await self._summarizer.summarize_content(all_items)
        stats["steps"].append({
            "name": "generate_summary",
            "status": "completed",
        })
        emit_progress("Generated AI summary", 1, 1)

        # Generate recommendations
        emit_progress("Generating recommendations", 0, 1)
        logger.info("Generating recommendations...")
        recommendations = await self._summarizer.generate_recommendations(
            items=all_items,
            current_sources=x_sources or [],
        )
        stats["steps"].append({
            "name": "generate_recommendations",
            "status": "completed",
        })
        emit_progress("Completed", 1, 1)

        # Record total pipeline time
        total_time = time.time() - pipeline_start
        stats["pipeline_duration_seconds"] = round(total_time, 2)

        return {
            "summary": summary,
            "items": [self._item_to_dict(item) for item in all_items[:20]],
            "recommendations": recommendations,
            "stats": stats,
        }

    def _item_to_dict(self, item: ContentItem) -> dict:
        """Convert ContentItem to serializable dict."""
        return {
            "platform": item.platform,
            "platform_id": item.platform_id,
            "source": item.source_identifier,
            "source_name": item.source_name,
            "content": item.content,
            "url": item.url,
            "metrics": item.metrics,
            "score": item.compute_score(),
            "posted_at": item.posted_at.isoformat(),
        }
