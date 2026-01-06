"""LLM-native content endpoints using Grok and Gemini."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from briefly.adapters.grok import get_grok_adapter
from briefly.adapters.gemini import get_gemini_adapter

router = APIRouter()


# --- Grok X Endpoints ---

class GrokSummarizeRequest(BaseModel):
    username: str
    hours: int = 24
    focus: str | None = None


class GrokBatchRequest(BaseModel):
    usernames: list[str]
    hours: int = 24
    focus: str | None = None


class GrokTopicRequest(BaseModel):
    topic: str
    hours: int = 24
    accounts: list[str] | None = None


@router.post("/grok/summarize")
async def grok_summarize_account(req: GrokSummarizeRequest) -> dict:
    """
    Summarize an X account's recent activity using Grok.

    No X API needed - Grok searches X directly.
    """
    adapter = get_grok_adapter()
    result = await adapter.summarize_account(
        username=req.username,
        hours=req.hours,
        focus=req.focus,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    return result


@router.post("/grok/summarize-batch")
async def grok_summarize_batch(req: GrokBatchRequest) -> dict:
    """
    Summarize multiple X accounts in one request.

    More efficient than individual calls.
    """
    if not req.usernames:
        raise HTTPException(400, "At least one username required")

    adapter = get_grok_adapter()
    results = await adapter.summarize_accounts_batch(
        usernames=req.usernames,
        hours=req.hours,
        focus=req.focus,
    )

    if results and "error" in results[0]:
        raise HTTPException(500, results[0]["error"])

    return {"results": results}


@router.post("/grok/search-topic")
async def grok_search_topic(req: GrokTopicRequest) -> dict:
    """
    Search X for a topic using Grok.

    Optionally filter to specific accounts.
    """
    adapter = get_grok_adapter()
    result = await adapter.search_topic(
        topic=req.topic,
        hours=req.hours,
        accounts=req.accounts,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    return result


# --- Gemini YouTube/Podcast Endpoints ---

class GeminiVideoRequest(BaseModel):
    video_url: str
    focus: str | None = None
    include_timestamps: bool = True


class GeminiVideoBatchRequest(BaseModel):
    video_urls: list[str]
    focus: str | None = None


class GeminiAudioRequest(BaseModel):
    audio_url: str
    title: str | None = None
    focus: str | None = None


@router.post("/gemini/summarize-video")
async def gemini_summarize_video(req: GeminiVideoRequest) -> dict:
    """
    Summarize a YouTube video using Gemini.

    Gemini processes the video directly - no transcription needed.
    """
    adapter = get_gemini_adapter()
    result = await adapter.summarize_video(
        video_url=req.video_url,
        focus=req.focus,
        include_timestamps=req.include_timestamps,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    return result


@router.post("/gemini/summarize-videos")
async def gemini_summarize_videos(req: GeminiVideoBatchRequest) -> dict:
    """
    Summarize multiple YouTube videos.
    """
    if not req.video_urls:
        raise HTTPException(400, "At least one video URL required")

    adapter = get_gemini_adapter()
    results = await adapter.summarize_videos_batch(
        video_urls=req.video_urls,
        focus=req.focus,
    )

    errors = [r for r in results if "error" in r]
    if len(errors) == len(results):
        raise HTTPException(500, "All video summarizations failed")

    return {"results": results, "errors": len(errors)}


@router.post("/gemini/summarize-audio")
async def gemini_summarize_audio(req: GeminiAudioRequest) -> dict:
    """
    Summarize podcast audio using Gemini.

    Gemini can process audio up to 9.5 hours directly.
    """
    adapter = get_gemini_adapter()
    result = await adapter.summarize_audio_url(
        audio_url=req.audio_url,
        title=req.title,
        focus=req.focus,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    return result


# --- Simplified Briefing Endpoints ---

class SimpleBriefingRequest(BaseModel):
    x_sources: list[str] | None = None
    youtube_sources: list[str] | None = None
    hours_back: int = 24
    focus: str | None = None


class QuickBriefingRequest(BaseModel):
    accounts: list[str]
    hours: int = 24
    focus: str | None = None


@router.post("/briefing")
async def generate_simple_briefing(req: SimpleBriefingRequest) -> dict:
    """
    Generate a briefing using the simplified LLM-native approach.

    Uses Grok for X content and Gemini for YouTube.
    Much faster than the traditional API-based approach.
    """
    from briefly.services.simple_curation import get_simple_curation

    if not req.x_sources and not req.youtube_sources:
        raise HTTPException(400, "At least one source type required")

    service = get_simple_curation()
    result = await service.create_briefing(
        x_sources=req.x_sources,
        youtube_sources=req.youtube_sources,
        hours_back=req.hours_back,
        focus=req.focus,
    )

    return result


@router.post("/briefing/quick")
async def generate_quick_briefing(req: QuickBriefingRequest) -> dict:
    """
    Generate a quick X-only briefing.

    Fastest path - just asks Grok to summarize specified accounts.
    No YouTube, no complex processing.
    """
    from briefly.services.simple_curation import get_simple_curation

    if not req.accounts:
        raise HTTPException(400, "At least one account required")

    service = get_simple_curation()
    result = await service.quick_briefing(
        x_accounts=req.accounts,
        hours=req.hours,
        focus=req.focus,
    )

    if result.get("error"):
        raise HTTPException(500, result["summary"])

    return result


# --- Combined/Testing Endpoints ---

@router.get("/test/grok")
async def test_grok() -> dict:
    """
    Test Grok connectivity by summarizing @elonmusk.
    """
    adapter = get_grok_adapter()
    result = await adapter.summarize_account("elonmusk", hours=24)
    return {
        "status": "ok" if "error" not in result else "error",
        "result": result,
    }


@router.get("/test/gemini")
async def test_gemini() -> dict:
    """
    Test Gemini connectivity with a short video.
    """
    adapter = get_gemini_adapter()
    # Use a short YouTube video for testing
    result = await adapter.summarize_video(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # Famous short video
        include_timestamps=False,
    )
    return {
        "status": "ok" if "error" not in result else "error",
        "result": result,
    }
