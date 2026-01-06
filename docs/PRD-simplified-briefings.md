# PRD: Simplified Briefing System

## Overview

Overhaul Briefly 3000 to use LLM-native content access instead of traditional API-based fetching. This dramatically simplifies the architecture, eliminates rate limit issues, and reduces costs.

## Problem Statement

The current system faces critical limitations:

1. **X API Rate Limits**: Free tier allows only 1 request/15 minutes, making aggregation impossible
2. **Complex Pipeline**: Fetch tweets → store → embed → summarize (4+ steps)
3. **YouTube Transcription**: Requires transcript extraction, storage, and separate summarization
4. **Podcast Processing**: Would require Taddy subscription ($75-150/mo) + transcription + summarization
5. **High Latency**: Multiple API calls and processing steps = slow briefings

## Solution

Replace the entire content fetching pipeline with direct LLM calls:

| Source | Old Approach | New Approach |
|--------|--------------|--------------|
| X/Twitter | X API → Store → Embed → Summarize | Grok query (1 call) |
| YouTube | Fetch → Transcript → Store → Summarize | Gemini URL (1 call) |
| Podcasts | Taddy → Download → Transcribe → Summarize | Gemini audio URL (1 call) |

## Architecture

### Current (Complex)
```
Sources → Platform APIs → Vector Store → Embeddings → Search → LLM → Briefing
           (rate limited)   (PostgreSQL)   (OpenAI)
```

### New (Simplified)
```
Sources → LLM with Native Access → Briefing
          (Grok for X, Gemini for media)
```

## Technical Implementation

### Phase 1: Core Adapters (DONE)

- [x] `GrokAdapter` - X content via Grok's x_search
- [x] `GeminiAdapter` - YouTube/podcast via direct URL processing
- [x] `SimpleCurationService` - Orchestrates LLM-native briefings
- [x] API endpoints at `/api/llm/*`

### Phase 2: Dashboard Overhaul

#### 2.1 Source Management (Simplified)

**Current UI:**
- X sources with list sync status
- YouTube channels with transcript status
- Complex sync operations

**New UI:**
```
┌─────────────────────────────────────────────────────────┐
│ Sources                                    [+ Add Source] │
├─────────────────────────────────────────────────────────┤
│ 📱 X Accounts                                            │
│   @elonmusk, @sama, @kaboris, @lexfridman              │
│   [Edit] [Test Summary]                                 │
├─────────────────────────────────────────────────────────┤
│ 📺 YouTube Channels                                      │
│   MKBHD, Veritasium, Lex Fridman                        │
│   [Edit] [Test Summary]                                 │
├─────────────────────────────────────────────────────────┤
│ 🎙️ Podcasts                                              │
│   Lex Fridman Podcast, All-In, Planet Money             │
│   [Edit] [Test Summary]                                 │
└─────────────────────────────────────────────────────────┘
```

**Changes:**
- Remove X List sync UI (no longer needed)
- Remove transcript status indicators
- Add podcast sources (new!)
- Add "Test Summary" quick action per source type

#### 2.2 Briefing Generation (Simplified)

**Current Flow:**
1. Click "Generate Briefing"
2. Shows complex job status with multiple steps
3. Waits for vector storage, embedding, etc.
4. Returns structured sections

**New Flow:**
1. Click "Generate Briefing"
2. Simple progress: "Summarizing X... YouTube... Podcasts..."
3. Returns unified summary in ~30-60 seconds

**New Briefing UI:**
```
┌─────────────────────────────────────────────────────────┐
│ Generate Briefing                                        │
├─────────────────────────────────────────────────────────┤
│ Time Range: [Last 24h ▼]                                │
│ Focus (optional): [AI, crypto, tech____________]        │
│                                                          │
│ Sources to include:                                      │
│ [x] X Accounts (4)                                      │
│ [x] YouTube Channels (3)                                │
│ [x] Podcasts (3)                                        │
│                                                          │
│ [Generate Briefing]  [Quick X-Only]                     │
└─────────────────────────────────────────────────────────┘
```

#### 2.3 Briefing Display

**New Briefing Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Daily Briefing - Jan 6, 2026                   [Share]  │
│ Focus: AI & Technology                                   │
├─────────────────────────────────────────────────────────┤
│ ## Executive Summary                                     │
│ [2-3 paragraph AI-generated overview of key themes]     │
│                                                          │
│ ## Key Takeaways                                         │
│ • Point 1                                               │
│ • Point 2                                               │
│ • Point 3                                               │
├─────────────────────────────────────────────────────────┤
│ ## X/Twitter Highlights                        [@expand] │
│ [Collapsible section with Grok's X summary]             │
├─────────────────────────────────────────────────────────┤
│ ## YouTube Highlights                          [@expand] │
│ [List of video summaries with thumbnails]               │
│ ┌──────┐ Video Title                                    │
│ │ thumb│ Channel • 2 hours ago                          │
│ └──────┘ [Summary preview...] [Watch] [Full Summary]    │
├─────────────────────────────────────────────────────────┤
│ ## Podcast Highlights                          [@expand] │
│ [List of episode summaries]                             │
│ 🎙️ Episode Title                                        │
│    Podcast Name • 45 min                                │
│    [Summary preview...] [Listen] [Full Summary]         │
└─────────────────────────────────────────────────────────┘
```

#### 2.4 Podcast Source Management

**New Feature: Add Podcast Sources**

Options for adding podcasts:
1. **Search by name** - Use podcast search API (Taddy free tier or iTunes)
2. **Paste RSS feed URL** - Direct feed import
3. **Import from app** - OPML import from podcast apps

**Podcast Source Storage:**
```json
{
  "podcasts": [
    {
      "name": "Lex Fridman Podcast",
      "feed_url": "https://lexfridman.com/feed/podcast/",
      "image_url": "...",
      "added_at": "2026-01-06T..."
    }
  ]
}
```

**Episode Discovery:**
- Parse RSS feed to get latest episodes
- Store episode audio URLs for Gemini processing
- No transcription needed!

### Phase 3: Remove Deprecated Code

After new system is stable, remove:

1. **X Lists system** (`services/x_lists.py`, list endpoints)
2. **Vector store for content** (keep for semantic search if needed)
3. **Transcript storage** (`services/transcripts.py`)
4. **Complex curation service** (keep as fallback initially)
5. **Embedding generation for content** (keep for search)

### Phase 4: Scheduling & Delivery

**Automated Briefings:**
```
┌─────────────────────────────────────────────────────────┐
│ Scheduled Briefings                                      │
├─────────────────────────────────────────────────────────┤
│ 🌅 Morning Briefing                          [Edit]     │
│    Daily at 7:00 AM • Last 24h • All sources            │
│    Delivery: Email, Telegram                            │
├─────────────────────────────────────────────────────────┤
│ 🌙 Evening Recap                             [Edit]     │
│    Daily at 6:00 PM • Last 12h • X only                 │
│    Delivery: Telegram                                   │
├─────────────────────────────────────────────────────────┤
│ [+ Add Schedule]                                        │
└─────────────────────────────────────────────────────────┘
```

## API Changes

### New Endpoints (Already Created)

```
POST /api/llm/briefing
  - Full briefing with X + YouTube + Podcasts
  - Body: { x_sources, youtube_sources, podcast_urls, hours_back, focus }

POST /api/llm/briefing/quick
  - Quick X-only briefing
  - Body: { accounts, hours, focus }

POST /api/llm/grok/summarize
  - Single X account summary

POST /api/llm/grok/summarize-batch
  - Multiple X accounts

POST /api/llm/gemini/summarize-video
  - YouTube video summary

POST /api/llm/gemini/summarize-audio
  - Podcast audio summary
```

### Deprecated Endpoints (Phase 3)

```
POST /api/sources/x/init-list      # No longer needed
POST /api/sources/x/sync           # No longer needed
GET  /api/sources/x/list-status    # No longer needed
GET  /api/sources/x/list-members   # No longer needed
POST /api/briefings/transcripts/*  # No longer needed
```

## Data Model Changes

### Sources File (`sources.json`)

**Current:**
```json
{
  "x": [
    {"identifier": "elonmusk", "list_synced": true, ...}
  ],
  "youtube": ["channel_id_1", "channel_id_2"],
  "x_list_id": "123...",
  "x_list_last_sync": "..."
}
```

**New:**
```json
{
  "x": ["elonmusk", "sama", "lexfridman"],
  "youtube": [
    {"channel_id": "UC...", "name": "MKBHD"}
  ],
  "podcasts": [
    {
      "name": "Lex Fridman Podcast",
      "feed_url": "https://...",
      "latest_episode_url": "https://..."
    }
  ],
  "settings": {
    "default_hours_back": 24,
    "default_focus": null
  }
}
```

## Cost Analysis

### Per Briefing (10 X accounts, 5 YouTube videos, 3 podcast episodes)

| Component | Old Cost | New Cost |
|-----------|----------|----------|
| X API | $0 (but rate limited) | $0.003 (Grok) |
| YouTube | ~$0.10 (transcription) | ~$0.05 (Gemini, 30min total) |
| Podcasts | ~$0.25 (Taddy + processing) | ~$0.15 (Gemini, 3hrs total) |
| Embeddings | ~$0.02 | $0 (not needed) |
| Final Summary | ~$0.01 | ~$0.01 |
| **Total** | **~$0.38 + rate limits** | **~$0.21** |

### Monthly (1 briefing/day)

| | Old | New |
|--|-----|-----|
| API Costs | ~$12 | ~$6.50 |
| Taddy Sub | $75-150 | $0 |
| **Total** | **$87-162** | **$6.50** |

**Savings: 92-96%**

## Success Metrics

1. **Briefing generation time**: < 60 seconds (down from 3-5 minutes)
2. **Error rate**: < 5% (down from ~30% due to rate limits)
3. **Cost per briefing**: < $0.25
4. **User satisfaction**: Briefings contain actionable insights

## Timeline

| Phase | Scope | Duration |
|-------|-------|----------|
| Phase 1 | Core adapters | DONE |
| Phase 2 | Dashboard overhaul | 1 week |
| Phase 3 | Remove deprecated code | 2-3 days |
| Phase 4 | Scheduling & delivery | 1 week |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Grok X search accuracy | Validate summaries, allow manual refresh |
| Gemini quota limits | Monitor usage, implement caching for repeated requests |
| Audio URL access issues | Fallback to download + upload for problematic URLs |
| LLM hallucinations | Include source links, allow drill-down to originals |

## Open Questions

1. **Semantic search**: Keep vector store for "search past briefings" feature?
2. **Offline access**: Cache briefings for offline viewing?
3. **Source verification**: How to validate Grok's X summaries are accurate?
4. **Podcast discovery**: Build podcast search or rely on manual RSS entry?

## Appendix: Tested & Working

```bash
# X via Grok - WORKS
POST /api/llm/grok/summarize-batch
{"usernames": ["elonmusk", "sama"], "hours": 24}

# YouTube via Gemini - WORKS
POST /api/llm/gemini/summarize-video
{"video_url": "https://youtube.com/watch?v=..."}

# Podcast via Gemini - WORKS
POST /api/llm/gemini/summarize-audio
{"audio_url": "https://...mp3", "title": "Episode Name"}
```
