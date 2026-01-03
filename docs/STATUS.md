# Briefly 3000 - Current Status

## Overview

Briefly 3000 is an AI-powered media curation assistant that aggregates content from X (Twitter) and YouTube, summarizes it using Grok 4.1, and delivers personalized briefings via a web dashboard.

## What's Working

### Core Features

1. **X (Twitter) Integration**
   - User lookup with caching (file-based)
   - Timeline fetching for configured sources
   - Rate limit handling (Free tier: ~25 req/24h)
   - Bot account: @briefly3000

2. **YouTube Integration**
   - Channel lookup by handle (@channel) or ID
   - Video fetching from channel uploads
   - **Subscription import** - Can fetch ANY channel's public subscriptions (no OAuth needed)
   - Full transcript fetching using youtube-transcript-api (no API key needed)

3. **Transcript Pipeline**
   - Full transcript extraction (no character limits)
   - File-based storage (`.cache/transcripts/`)
   - Background summarization with Grok 4.1
   - Chunking strategy for long-form content (30k chars/chunk)
   - Cached summaries used in subsequent briefings

4. **AI Summarization**
   - Grok 4.1 Fast via xAI API (OpenAI-compatible)
   - Executive briefing generation
   - Source recommendations

5. **Web Dashboard** (http://localhost:8000)
   - Source management (add/remove X and YouTube sources)
   - YouTube subscription import
   - Briefing generation with real-time status
   - Transcript processing controls
   - View latest briefing with top posts

### Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI (Python 3.12) |
| Frontend | Alpine.js + Tailwind CSS |
| AI | Grok 4.1 Fast (xAI API) |
| Database | PostgreSQL 17 + pgvector (ready, not yet used) |
| Cache | File-based JSON (Redis available) |
| Package Manager | uv |

### API Endpoints

```
GET  /                              # Dashboard
GET  /api/sources                   # List sources
POST /api/sources                   # Add source
DELETE /api/sources/{platform}/{id} # Remove source
POST /api/sources/youtube/import    # Import YT subscriptions
GET  /api/sources/cache/stats       # Cache statistics

GET  /api/briefings                 # List briefings
POST /api/briefings/generate        # Generate new briefing
GET  /api/briefings/generate/{id}   # Job status
GET  /api/briefings/latest          # Latest briefing

GET  /api/briefings/transcripts/stats    # Transcript stats
POST /api/briefings/transcripts/process  # Process pending
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Web Dashboard                            │
│                  (Alpine.js + Tailwind)                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Server                          │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Sources   │  │  Briefings  │  │    Transcripts      │  │
│  │   Router    │  │   Router    │  │      Router         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   X Adapter     │  │ YouTube Adapter │  │ Summarization   │
│   (Tweepy)      │  │ (Google API)    │  │ Service (Grok)  │
└─────────────────┘  └─────────────────┘  └─────────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  User Cache     │  │ Transcript      │  │  Briefings      │
│  (.cache/users) │  │ Store           │  │  Storage        │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## Running the Project

```bash
# Start dependencies
docker-compose up -d

# Run the server
uv run python main.py

# Open dashboard
open http://localhost:8000
```

## Environment Variables

See `.env.example` for required configuration:
- X API credentials
- xAI (Grok) API key
- YouTube API key
- Database/Redis URLs

## Known Limitations

1. **X API Free Tier** - Very restrictive rate limits (~25 requests/24h)
2. **No persistent database** - Currently file-based storage
3. **No scheduled briefings** - Manual trigger only
4. **Single user** - No authentication/multi-tenancy

## Next Steps

See [PRD: Transcript Memory & Letta Integration](./PRD-transcript-memory.md) for the next major feature decision.
