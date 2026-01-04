# PRD: n8n Media Extraction Flows for Briefly 3000

## Overview

Port Briefly 3000's media extraction logic from Python adapters to n8n workflows for improved reliability, rate limit handling, and operational visibility. **Design for modularity and long-term growth** across many content platforms.

## Problem Statement

Current Python adapters face several challenges:
1. **X API Rate Limits**: Free tier limits (100 tweets/15 min) cause 15-minute waits that timeout the dashboard
2. **Monolithic Execution**: All extraction happens on-demand during briefing generation
3. **No Retry Logic**: Failed extractions require full re-generation
4. **Limited Visibility**: Errors are logged but not easily monitored
5. **Single Credential**: Can't rotate API keys to avoid limits
6. **Limited Extensibility**: Adding new platforms requires Python code changes

## Goals

1. **Decouple extraction from generation** - Run extraction on schedules, not on-demand
2. **Handle rate limits gracefully** - Queue, retry, and rotate credentials
3. **Provide operational visibility** - Visual workflows, execution history, alerts
4. **Enable incremental updates** - Only fetch new content since last run
5. **Support multiple credential sets** - Rotate keys to maximize throughput
6. **Modular architecture** - Reusable sub-workflows, easy platform additions
7. **Scalable design** - Handle 10x sources without architectural changes

## Non-Goals

- Replacing the Briefly dashboard (keep existing FastAPI UI)
- Moving summarization/LLM calls to n8n (keep in Python for GPU access)
- Real-time extraction (scheduled is sufficient)
- Replacing Python adapters entirely (keep as fallback/reference)

---

## Architecture

### Design Principles

1. **Hierarchy over Duplication** - Shared logic in sub-workflows, platform-specific in adapters
2. **Separation of Concerns** - Extraction, transformation, storage, notification are separate
3. **Platform Agnostic Core** - ContentItem schema is universal; adapters handle platform quirks
4. **Fail Gracefully** - Partial success is better than total failure
5. **Observable by Default** - Every workflow reports status, errors, metrics

### Workflow Hierarchy

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER                             │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │ Daily Briefing  │  │  On-Demand      │  │  Health Monitor     │  │
│  │ Orchestrator    │  │  Trigger        │  │  (Watchdog)         │  │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────────┘  │
└───────────┼─────────────────────┼───────────────────────────────────┘
            │                     │
            ▼                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PLATFORM ADAPTERS                               │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ X/Twitter│ │ YouTube  │ │ Podcast  │ │ Reddit   │ │ Email    │  │
│  │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter  │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘  │
│       │            │            │            │            │         │
│  ┌────┴─────┐ ┌────┴─────┐ ┌────┴─────┐      │            │         │
│  │Future:   │ │Future:   │ │Future:   │      │            │         │
│  │Bluesky   │ │Vimeo     │ │Spotify   │      │            │         │
│  └──────────┘ └──────────┘ └──────────┘      │            │         │
└───────────┬─────────┬─────────┬──────────────┴────────────┴─────────┘
            │         │         │
            ▼         ▼         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SHARED SUB-WORKFLOWS                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ Content     │  │ Rate Limit  │  │ Transcribe  │  │ Notify     │  │
│  │ Ingestion   │  │ Manager     │  │ Queue       │  │ (Telegram) │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ Source      │  │ Error       │  │ Dedup       │  │ Metrics    │  │
│  │ Fetcher     │  │ Handler     │  │ Filter      │  │ Reporter   │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │
└───────────┬─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     BRIEFLY API LAYER                               │
├─────────────────────────────────────────────────────────────────────┤
│  POST /api/content/ingest     GET /api/sources                      │
│  POST /api/content/transcribe GET /api/extraction/status            │
│  POST /api/briefings/generate GET /api/content/pending              │
└─────────────────────────────────────────────────────────────────────┘
```

### Integration Points

1. **n8n → Briefly API**: All content flows through REST API (not direct DB)
2. **Briefly → n8n Webhooks**: Dashboard can trigger on-demand extraction
3. **Shared Schema**: ContentItem dataclass is the contract between systems
4. **Credential Isolation**: Platform secrets stored in n8n, not Briefly

---

## Shared Sub-Workflows (Reusable Components)

These sub-workflows are called by platform adapters. Build these FIRST.

### 1. Source Fetcher (`sub-source-fetcher`)

**Purpose**: Get sources for a specific platform from Briefly API

**Input**:
```json
{
  "platform": "x|youtube|podcast|reddit|email",
  "active_only": true
}
```

**Output**:
```json
{
  "sources": [
    {"identifier": "@elonmusk", "platform_id": "123", "display_name": "Elon Musk"}
  ],
  "count": 15
}
```

**Implementation**: Single HTTP GET to `GET /api/sources?platform={platform}`

---

### 2. Rate Limit Manager (`sub-rate-limit`)

**Purpose**: Check/update rate limit state, rotate credentials if needed

**Input**:
```json
{
  "platform": "x",
  "credential_id": "token_1",
  "remaining": 5,
  "reset_at": "2024-01-15T10:45:00Z"
}
```

**Output**:
```json
{
  "can_proceed": true,
  "use_credential": "token_2",
  "wait_seconds": 0
}
```

**State Storage**: n8n static data (persists between executions)

**Logic**:
- Track remaining calls per credential
- If exhausted, try next credential in rotation
- If all exhausted, return wait time until earliest reset

---

### 3. Content Ingestion (`sub-content-ingest`)

**Purpose**: Deduplicate and store ContentItems to Briefly API

**Input**:
```json
{
  "items": [/* array of ContentItem */],
  "platform": "x"
}
```

**Output**:
```json
{
  "inserted": 12,
  "duplicates_skipped": 3,
  "errors": []
}
```

**Implementation**:
- POST to `/api/content/ingest` (bulk, idempotent)
- Briefly handles dedup by `platform_id`

---

### 4. Error Handler (`sub-error-handler`)

**Purpose**: Standardized error handling with alerts

**Input**:
```json
{
  "workflow": "x-adapter",
  "error_type": "rate_limit|api_error|timeout|auth",
  "message": "X API returned 429",
  "context": {"source": "@elonmusk"}
}
```

**Actions**:
- Log to n8n execution
- If `error_type` in [auth, repeated_failure]: Send Telegram alert
- Update workflow metrics

---

### 5. Notify (`sub-notify`)

**Purpose**: Send notifications (Telegram, Discord, etc.)

**Input**:
```json
{
  "channel": "telegram|discord",
  "priority": "normal|urgent",
  "message": "Daily briefing ready",
  "url": "https://briefly.app/briefings/123"
}
```

**Implementation**: HTTP POST to configured webhook

---

### 6. Transcription Queue (`sub-transcribe-queue`)

**Purpose**: Queue audio/video for transcription

**Input**:
```json
{
  "platform": "podcast|youtube",
  "platform_id": "episode_123",
  "audio_url": "https://...",
  "duration_seconds": 3600
}
```

**Output**:
```json
{
  "job_id": "transcribe_abc123",
  "estimated_wait": 300
}
```

**Implementation**: POST to `/api/content/transcribe`

---

### 7. Metrics Reporter (`sub-metrics`)

**Purpose**: Report extraction metrics for monitoring

**Input**:
```json
{
  "workflow": "x-adapter",
  "run_id": "exec_123",
  "sources_processed": 15,
  "items_extracted": 45,
  "items_ingested": 42,
  "errors": 1,
  "duration_seconds": 32
}
```

**Storage**: POST to `/api/extraction/metrics` or n8n static data

---

## Platform Adapter Workflows

Each adapter follows the same pattern but handles platform-specific quirks.

### Adapter Interface (Contract)

Every platform adapter MUST:
1. Call `sub-source-fetcher` to get sources
2. Call `sub-rate-limit` before API requests
3. Transform platform data to ContentItem schema
4. Call `sub-content-ingest` with results
5. Call `sub-error-handler` on failures
6. Call `sub-metrics` on completion

```
┌─────────────────────────────────────────────────────────┐
│                  ADAPTER TEMPLATE                       │
├─────────────────────────────────────────────────────────┤
│  1. Trigger (Cron or Webhook)                           │
│  2. sub-source-fetcher → Get sources for platform       │
│  3. sub-rate-limit → Check if can proceed               │
│  4. [PLATFORM-SPECIFIC] → Fetch from API                │
│  5. Transform → Convert to ContentItem[]                │
│  6. sub-content-ingest → Store to Briefly               │
│  7. (Optional) sub-transcribe-queue → Queue audio       │
│  8. sub-metrics → Report stats                          │
│  9. sub-error-handler → Handle any failures             │
└─────────────────────────────────────────────────────────┘
```

---

### 1. X/Twitter Adapter (`adapter-x`)

**Trigger**: Cron every 15 minutes

**Platform-Specific Logic**:
- User lookup: GET `/2/users/by/username/{username}`
- Timeline: GET `/2/users/{id}/tweets`
- Credential rotation (multiple bearer tokens)
- Temp list strategy for 300+ users (optional optimization)

**Rate Limits** (Free Tier):
- 100 tweet reads / 15 min
- 100 user lookups / 24 hours

**ContentItem Mapping**:
```javascript
{
  platform: "x",
  platform_id: tweet.id,
  source_identifier: `@${tweet.author.username}`,
  source_name: tweet.author.name,
  content: tweet.text,
  url: `https://x.com/${username}/status/${tweet.id}`,
  posted_at: tweet.created_at,
  metrics: {
    like_count: tweet.public_metrics.like_count,
    retweet_count: tweet.public_metrics.retweet_count,
    reply_count: tweet.public_metrics.reply_count,
    impression_count: tweet.public_metrics.impression_count
  }
}
```

---

### 2. YouTube Adapter (`adapter-youtube`)

**Trigger**: Cron every 30 minutes

**Platform-Specific Logic**:
- Channel → Uploads playlist: GET `/channels?part=contentDetails`
- Playlist items: GET `/playlistItems?playlistId={id}`
- Video details: GET `/videos?part=snippet,statistics,contentDetails`
- Transcript: Call Python endpoint or youtube-transcript-api

**Rate Limits**: 10,000 units/day (generous)

**ContentItem Mapping**:
```javascript
{
  platform: "youtube",
  platform_id: video.id,
  source_identifier: video.snippet.channelId,
  source_name: video.snippet.channelTitle,
  content: video.transcript || video.snippet.description,
  url: `https://youtube.com/watch?v=${video.id}`,
  posted_at: video.snippet.publishedAt,
  metrics: {
    view_count: video.statistics.viewCount,
    like_count: video.statistics.likeCount,
    comment_count: video.statistics.commentCount,
    has_transcript: !!video.transcript,
    duration_seconds: parseDuration(video.contentDetails.duration)
  }
}
```

**Special**: If no transcript available, call `sub-transcribe-queue` for Whisper

---

### 3. Podcast Adapter (`adapter-podcast`)

**Trigger**: Cron every 1 hour

**Platform-Specific Logic**:
- Taddy GraphQL API: Query recent episodes
- Check transcript availability in response
- Extract audio URL for local transcription

**ContentItem Mapping**:
```javascript
{
  platform: "podcast",
  platform_id: episode.uuid,
  source_identifier: episode.podcastId,
  source_name: episode.podcastName,
  content: episode.transcript || episode.description,
  url: episode.url,
  posted_at: episode.publishedAt,
  metrics: {
    duration_seconds: episode.duration,
    has_transcript: !!episode.transcript,
    transcript_source: episode.transcript ? "taddy" : null,
    can_transcribe_locally: !episode.transcript && episode.audioUrl
  }
}
```

**Special**: Queue for Whisper transcription if no transcript

---

### 4. Reddit Adapter (`adapter-reddit`) [FUTURE]

**Trigger**: Cron every 30 minutes

**Platform-Specific Logic**:
- Subreddit posts: GET `/r/{subreddit}/hot.json`
- No auth needed for public subreddits
- Optional: User posts for followed users

**ContentItem Mapping**:
```javascript
{
  platform: "reddit",
  platform_id: post.id,
  source_identifier: `r/${post.subreddit}`,
  source_name: post.subreddit_name_prefixed,
  content: post.selftext || post.title,
  url: `https://reddit.com${post.permalink}`,
  posted_at: new Date(post.created_utc * 1000),
  metrics: {
    score: post.score,
    upvote_ratio: post.upvote_ratio,
    num_comments: post.num_comments
  }
}
```

---

### 5. Email Adapter (`adapter-email`) [FUTURE]

**Trigger**: Cron every 15 minutes

**Platform-Specific Logic**:
- Gmail API with OAuth
- Filter by newsletter labels/senders
- Parse HTML to text

**ContentItem Mapping**:
```javascript
{
  platform: "email",
  platform_id: message.id,
  source_identifier: message.from,
  source_name: extractSenderName(message.from),
  content: parseEmailBody(message),
  url: null, // Emails don't have public URLs
  posted_at: message.internalDate,
  metrics: {
    has_attachments: message.attachments.length > 0
  }
}
```

---

### 6. Blog/RSS Adapter (`adapter-rss`) [FUTURE]

**Trigger**: Cron every 1 hour

**Platform-Specific Logic**:
- Parse RSS/Atom feeds
- Extract full content if available
- Handle various feed formats

**ContentItem Mapping**:
```javascript
{
  platform: "blog",
  platform_id: hashOfUrl(item.link),
  source_identifier: feed.url,
  source_name: feed.title,
  content: item.content || item.description,
  url: item.link,
  posted_at: item.pubDate,
  metrics: {}
}
```

---

## Orchestration Workflows

### Master Orchestrator (`orchestrator-daily`)

**Trigger**: Cron daily at user's briefing_time (default 7 AM)

**Flow**:
```
1. Get active platforms from Briefly API
2. Trigger adapters in parallel:
   ├── Execute adapter-x (wait: false)
   ├── Execute adapter-youtube (wait: false)
   └── Execute adapter-podcast (wait: false)
3. Wait for all to complete (timeout: 10 min)
4. Check minimum content thresholds
5. POST /api/briefings/generate
6. Poll for completion
7. sub-notify → Send Telegram with briefing link
```

**Error Handling**:
- If any adapter fails: Continue with others (partial success)
- If all fail: sub-notify with error, skip briefing generation
- If briefing generation fails: sub-notify with error

---

### On-Demand Trigger (`orchestrator-ondemand`)

**Trigger**: Webhook from Briefly dashboard

**Input**:
```json
{
  "platforms": ["x", "youtube"],
  "hours_back": 24,
  "user_id": "user_123"
}
```

**Flow**:
1. Trigger only requested platform adapters
2. Wait for completion
3. Return extracted content count to webhook response

---

## Delivery Workflows (Output Layer)

Briefings need to reach users through their preferred channels.

### Delivery Interface (Contract)

All delivery workflows receive:
```json
{
  "user_id": "user_123",
  "briefing_id": "briefing_456",
  "briefing_url": "https://briefly.app/briefings/456",
  "summary": "Your daily briefing is ready with 45 items...",
  "stats": {
    "x_count": 20,
    "youtube_count": 15,
    "podcast_count": 10
  }
}
```

---

### 1. Telegram Delivery (`delivery-telegram`)

**Trigger**: Called by orchestrator after briefing complete

**Message Format**:
```
📰 Your Daily Briefing is Ready!

📊 45 items from 15 sources:
  • 🐦 20 X posts
  • 📺 15 YouTube videos
  • 🎙️ 10 Podcast episodes

📝 Summary:
{first 500 chars of summary}...

🔗 Read full: {briefing_url}
```

**Implementation**:
- HTTP POST to Telegram Bot API
- Markdown formatting
- Include inline keyboard with "View Briefing" button

---

### 2. Email Delivery (`delivery-email`)

**Trigger**: Called by orchestrator after briefing complete

**Email Format**:
- Subject: `Your Daily Briefing - {date}`
- HTML template with summary + top items
- CTA button to dashboard

**Implementation**:
- n8n Send Email node or external service (SendGrid, Resend)
- User email from Briefly API

---

### 3. Discord Delivery (`delivery-discord`)

**Trigger**: Called by orchestrator after briefing complete

**Message Format**:
- Embed with summary
- Fields for each platform count
- Link to full briefing

**Implementation**:
- HTTP POST to Discord webhook
- Rich embed formatting

---

### 4. Webhook Delivery (`delivery-webhook`) [FUTURE]

**Purpose**: Push briefings to external systems (Slack, Zapier, etc.)

**Implementation**:
- POST to user-configured webhook URL
- Include full briefing JSON payload

---

## Scheduler System

Users have different briefing schedules. n8n needs to trigger at the right time.

### User Schedule Model

```json
{
  "user_id": "user_123",
  "timezone": "America/New_York",
  "briefing_time": "07:00",
  "delivery_channels": ["telegram", "email"],
  "active_platforms": ["x", "youtube", "podcast"],
  "enabled": true
}
```

---

### Schedule Manager (`scheduler-manager`)

**Trigger**: Cron every 5 minutes

**Purpose**: Check if any users are due for briefing generation

**Flow**:
```
1. GET /api/users/schedules/due
   - Returns users whose briefing_time is within next 5 min
   - Accounts for timezone
2. For each due user:
   ├── Trigger orchestrator-daily with user_id
   └── Mark user as "processing" to prevent duplicates
3. Log scheduled runs
```

**Why not one cron per user?**
- n8n free tier limits cron triggers
- Centralized scheduling is easier to monitor
- Handles timezone changes gracefully

---

### Schedule Setter (`scheduler-set`)

**Trigger**: Webhook from Briefly dashboard

**Purpose**: Update user's briefing schedule

**Input**:
```json
{
  "user_id": "user_123",
  "timezone": "America/New_York",
  "briefing_time": "07:00",
  "delivery_channels": ["telegram"],
  "enabled": true
}
```

**Flow**:
1. Validate input (timezone, time format)
2. POST /api/users/{user_id}/schedule
3. Return confirmation

---

### Schedule Sync (`scheduler-sync`)

**Trigger**: Cron daily at midnight UTC

**Purpose**: Sync n8n schedule cache with Briefly API

**Flow**:
1. GET /api/users/schedules (all users)
2. Update n8n static data cache
3. Log any discrepancies

---

## Complete Workflow Hierarchy (Updated)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SCHEDULER LAYER                                 │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │ Schedule        │  │  Schedule       │  │  Schedule           │  │
│  │ Manager (cron)  │  │  Setter (hook)  │  │  Sync (daily)       │  │
│  └────────┬────────┘  └─────────────────┘  └─────────────────────┘  │
└───────────┼─────────────────────────────────────────────────────────┘
            │ triggers per-user
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER                             │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │ Daily Briefing  │  │  On-Demand      │  │  Health Monitor     │  │
│  │ Orchestrator    │  │  Trigger        │  │  (Watchdog)         │  │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────────┘  │
└───────────┼─────────────────────┼───────────────────────────────────┘
            │                     │
            ▼                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PLATFORM ADAPTERS                               │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ X/Twitter│ │ YouTube  │ │ Podcast  │ │ Reddit   │ │ Email    │  │
│  │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter  │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└───────────┬─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SHARED SUB-WORKFLOWS                            │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ Content     │  │ Rate Limit  │  │ Transcribe  │  │ Metrics    │  │
│  │ Ingestion   │  │ Manager     │  │ Queue       │  │ Reporter   │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────┐  │
│  │ Source      │  │ Error       │  │ Dedup Filter                │  │
│  │ Fetcher     │  │ Handler     │  │                             │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────────┘  │
└───────────┬─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     DELIVERY LAYER                                  │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌────────────┐  │
│  │ Telegram    │  │ Email       │  │ Discord     │  │ Webhook    │  │
│  │ Delivery    │  │ Delivery    │  │ Delivery    │  │ (Future)   │  │
│  └─────────────┘  └─────────────┘  └─────────────┘  └────────────┘  │
└───────────┬─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     BRIEFLY API LAYER                               │
├─────────────────────────────────────────────────────────────────────┤
│  POST /api/content/ingest     GET /api/sources                      │
│  POST /api/content/transcribe GET /api/extraction/status            │
│  POST /api/briefings/generate GET /api/content/pending              │
│  GET /api/users/schedules/due POST /api/users/{id}/schedule         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### Content Item (n8n → Briefly)

```json
{
  "platform": "x|youtube|podcast",
  "platform_id": "unique-id-from-platform",
  "source_identifier": "@username|channel_id|podcast_id",
  "source_name": "Display Name",
  "content": "Text content or transcript",
  "url": "https://...",
  "posted_at": "2024-01-15T10:30:00Z",
  "fetched_at": "2024-01-15T11:00:00Z",
  "metrics": {
    "like_count": 100,
    "retweet_count": 50,
    "view_count": 1000
  },
  "metadata": {
    "has_transcript": true,
    "transcript_source": "youtube|taddy|whisper",
    "duration_seconds": 3600
  }
}
```

### Briefly API Endpoints (New)

```
POST /api/content/ingest
  - Bulk insert content items
  - Handles deduplication
  - Returns inserted count

GET /api/sources
  - Returns all configured sources
  - Used by n8n to know what to fetch

POST /api/content/transcribe
  - Queue item for local transcription
  - Returns job ID

GET /api/extraction/status
  - Returns last extraction times
  - Used by n8n to determine what's new
```

---

## n8n Configuration

### Required Credentials

1. **X API** (Bearer Token) - Multiple for rotation
2. **YouTube Data API** - Single key sufficient
3. **Taddy API** - Key + User ID
4. **Briefly API** - Internal auth token
5. **Telegram Bot** - For notifications

### Environment Variables

```
BRIEFLY_API_URL=http://localhost:8000
BRIEFLY_API_KEY=internal-secret
X_BEARER_TOKEN_1=...
X_BEARER_TOKEN_2=...
YOUTUBE_API_KEY=...
TADDY_API_KEY=...
TADDY_USER_ID=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### n8n Workflow Files

Store workflow JSON exports in `briefly/n8n-workflows/` with clear naming:

```
n8n-workflows/
├── README.md                      # Setup & import instructions
│
├── sub/                           # Shared sub-workflows (build first)
│   ├── sub-source-fetcher.json
│   ├── sub-rate-limit.json
│   ├── sub-content-ingest.json
│   ├── sub-error-handler.json
│   ├── sub-notify.json
│   ├── sub-transcribe-queue.json
│   └── sub-metrics.json
│
├── adapters/                      # Platform-specific extraction
│   ├── adapter-x.json
│   ├── adapter-youtube.json
│   ├── adapter-podcast.json
│   ├── adapter-reddit.json        # Future
│   ├── adapter-email.json         # Future
│   └── adapter-rss.json           # Future
│
├── delivery/                      # Output channels
│   ├── delivery-telegram.json
│   ├── delivery-email.json
│   ├── delivery-discord.json
│   └── delivery-webhook.json      # Future
│
├── scheduler/                     # Timing & orchestration
│   ├── scheduler-manager.json
│   ├── scheduler-set.json
│   └── scheduler-sync.json
│
└── orchestrator/                  # High-level coordination
    ├── orchestrator-daily.json
    ├── orchestrator-ondemand.json
    └── orchestrator-health.json   # Watchdog/monitoring
```

**Import Order**: sub/ → adapters/ → delivery/ → scheduler/ → orchestrator/

---

## Implementation Phases

### Phase 0: Foundation (API Endpoints)
**Goal**: Briefly API ready to receive n8n calls

- [ ] `POST /api/content/ingest` - Bulk content ingestion with dedup
- [ ] `GET /api/sources?platform={x}` - Sources by platform
- [ ] `GET /api/extraction/status` - Last extraction times
- [ ] `POST /api/content/transcribe` - Queue transcription job
- [ ] `GET /api/users/schedules/due` - Users due for briefing
- [ ] `POST /api/users/{id}/schedule` - Update user schedule
- [ ] `GET /api/extraction/metrics` - Extraction stats

**Deliverable**: OpenAPI spec for n8n HTTP nodes

---

### Phase 1: Shared Sub-Workflows
**Goal**: Reusable building blocks

- [ ] `sub-source-fetcher` - Simple HTTP GET wrapper
- [ ] `sub-content-ingest` - POST to ingest endpoint
- [ ] `sub-error-handler` - Logging + conditional alerts
- [ ] `sub-notify` - Telegram HTTP POST (start simple)
- [ ] `sub-metrics` - Store to n8n static data

**Test**: Each sub-workflow independently with mock data

---

### Phase 2: X Adapter (Priority)
**Goal**: Fix rate limit issues, prove architecture

- [ ] `adapter-x` - Full implementation
- [ ] `sub-rate-limit` - Credential rotation for X
- [ ] Test with real X API (watch rate limits)
- [ ] Compare extraction vs Python adapter
- [ ] Run in parallel with Python for 1 week

**Success Metric**: >95% extraction success rate

---

### Phase 3: Delivery Layer
**Goal**: Users receive briefings via preferred channel

- [ ] `delivery-telegram` - Rich message formatting
- [ ] `delivery-email` - HTML template (optional)
- [ ] Connect to orchestrator output

**Test**: Manual trigger → receive notification

---

### Phase 4: Scheduler System
**Goal**: Automated per-user briefing generation

- [ ] `scheduler-manager` - 5-min cron check
- [ ] `scheduler-set` - Webhook for dashboard
- [ ] `orchestrator-daily` - Full pipeline
- [ ] Test with multiple timezones

**Success Metric**: Briefings arrive within 5 min of scheduled time

---

### Phase 5: YouTube & Podcast Adapters
**Goal**: Extend to remaining platforms

- [ ] `adapter-youtube` - With transcript detection
- [ ] `adapter-podcast` - With Taddy integration
- [ ] `sub-transcribe-queue` - Local Whisper integration
- [ ] Test transcript → summary flow

---

### Phase 6: Future Platforms
**Goal**: Easy expansion

- [ ] `adapter-reddit` - Public subreddits
- [ ] `adapter-email` - Gmail OAuth (complex)
- [ ] `adapter-rss` - Generic RSS/Atom
- [ ] Template for new adapters (copy & modify)

---

### Phase 7: Monitoring & Reliability
**Goal**: Production-grade observability

- [ ] `orchestrator-health` - Watchdog workflow
- [ ] n8n dashboard with key metrics
- [ ] Alert escalation (Telegram → Discord → Email)
- [ ] Automatic retry for transient failures

---

## Success Metrics

1. **X Extraction Success Rate**: >95% (vs current ~50% due to rate limits)
2. **Content Freshness**: <30 min latency for X, <1 hour for YouTube/Podcasts
3. **Operational Visibility**: All failures visible in n8n UI
4. **Rate Limit Efficiency**: Zero 15-minute waits

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| n8n downtime affects extraction | Run n8n with Docker + auto-restart |
| Multiple credential management | Use n8n credential rotation node |
| Data sync issues | Use platform_id as unique key, idempotent inserts |
| Increased complexity | Keep Python adapters as fallback |

---

## Architectural Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| n8n → DB | Via API (not direct) | Consistency, dedup logic in one place, easier auth |
| n8n location | Same VPS (vultr-letta) | Already running, port 5678, simplify networking |
| Migration strategy | Parallel operation | Python adapters remain fallback during transition |
| Credential storage | n8n secrets only | Don't duplicate in Briefly, single source of truth |
| Schedule management | Centralized 5-min cron | Avoid n8n trigger limits, easier timezone handling |
| Sub-workflow pattern | n8n Execute Workflow node | Native support, clean separation, testable |
| Error strategy | Partial success | Continue with working platforms if one fails |

## Open Questions

1. **Multi-user content isolation**: Should n8n store content per-user or globally?
   - **Current thinking**: Global store with user→source mapping
   - **Trade-off**: Simpler dedup vs. per-user filtering complexity

2. **Transcript processing location**: n8n queue → Python Whisper, or n8n handles audio?
   - **Current thinking**: n8n queues, Python transcribes (GPU on local machine)
   - **Trade-off**: n8n could call cloud Whisper API for VPS-only deployments

3. **Rate limit state persistence**: n8n static data vs. Redis vs. Briefly API?
   - **Current thinking**: n8n static data (simple, survives restarts)
   - **Trade-off**: Not shared across n8n instances if scaled

4. **Dashboard integration depth**: Light webhook triggers vs. deep n8n embedding?
   - **Current thinking**: Webhooks for triggers, polling for status
   - **Trade-off**: n8n UI could be embedded via iframe for power users

---

## References

- [n8n Documentation](https://docs.n8n.io/)
- [X API Rate Limits](https://developer.twitter.com/en/docs/twitter-api/rate-limits)
- [Briefly Current Architecture](../src/briefly/adapters/)
