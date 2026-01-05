# PRD: n8n Media Extraction & Job Orchestration for Briefly 3000

## Overview

Make n8n the **primary execution engine** for Briefly 3000. All heavy lifting (extraction, transcription orchestration, briefing generation) runs in n8n workflows with granular progress reporting back to the dashboard. Briefly becomes a thin API layer for the UI and webhook endpoints.

## Problem Statement

Current architecture has critical issues:

### Execution Issues
1. **X API Rate Limits**: Free tier limits (100 tweets/15 min) cause 15-minute waits that timeout the dashboard
2. **Monolithic Execution**: All extraction happens on-demand during briefing generation
3. **No Retry Logic**: Failed extractions require full re-generation
4. **Limited Visibility**: Errors are logged but not easily monitored
5. **Single Credential**: Can't rotate API keys to avoid limits

### Job Persistence Issues (NEW)
6. **In-Memory Jobs**: `_jobs` dict lost on server restart or browser reload
7. **No Reconnection**: Close tab = lose visibility into running job
8. **No Job History**: Can't see past runs, debug failures
9. **UI Blocking**: Long transcriptions (5-10 min) with poor progress feedback

## Goals

1. **n8n as Execution Engine** - All heavy work runs in n8n, not FastAPI background tasks
2. **Granular Progress Reporting** - n8n POSTs progress at each step (per-podcast, per-source)
3. **Persistent Jobs** - Job state survives server restarts via n8n + thin local cache
4. **Reconnectable UI** - Browser can reconnect to running n8n execution
5. **Handle rate limits gracefully** - Queue, retry, and rotate credentials in n8n
6. **Operational visibility** - Visual workflows, execution history in n8n UI
7. **Modular architecture** - Reusable sub-workflows, easy platform additions

## Non-Goals

- Heavy job state in Briefly (n8n is source of truth)
- Real-time websockets (1-2s polling is sufficient)
- Multi-tenant job isolation (single-user for now)
- Moving LLM summarization to n8n (keep in Python for GPU/prompt control)

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
3. **n8n → Briefly Progress**: n8n POSTs progress updates during execution
4. **Shared Schema**: ContentItem dataclass is the contract between systems
5. **Credential Isolation**: Platform secrets stored in n8n, not Briefly

---

## Job Management & Progress Reporting (NEW)

This is the **critical addition** that makes n8n the execution engine with granular progress visible in the dashboard.

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            DASHBOARD (Browser)                                │
├──────────────────────────────────────────────────────────────────────────────┤
│  1. User clicks "Generate Briefing"                                          │
│  2. Poll GET /api/jobs/{id} every 1.5s                                       │
│  3. Display progress from response                                            │
│  4. On complete, show briefing                                                │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │
                    POST /api/jobs │ GET /api/jobs/{id}
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         BRIEFLY API (Thin Layer)                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  POST /api/jobs                                                               │
│    → Create job record (id, status=pending)                                   │
│    → POST to n8n webhook with job_id                                          │
│    → Return job_id immediately                                                │
│                                                                               │
│  GET /api/jobs/{id}                                                           │
│    → Return job from local cache (progress, status)                           │
│                                                                               │
│  POST /api/n8n/progress (webhook for n8n)                                     │
│    → Update job progress in local cache                                       │
│                                                                               │
│  POST /api/n8n/complete (webhook for n8n)                                     │
│    → Mark job complete, store result reference                                │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │
              Webhook trigger      │     Progress webhooks
                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         n8n WORKFLOW ENGINE                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  orchestrator-briefing-ondemand:                                              │
│                                                                               │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                     │
│  │ 1. Webhook  │────▶│ 2. Progress │────▶│ 3. Fetch X  │                     │
│  │ (job_id)    │     │ "Starting"  │     │             │                     │
│  └─────────────┘     └─────────────┘     └──────┬──────┘                     │
│                                                  │                            │
│                      ┌───────────────────────────┘                            │
│                      ▼                                                        │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                     │
│  │ 4. Progress │────▶│ 5. Fetch YT │────▶│ 6. Progress │                     │
│  │ "X: 25"     │     │             │     │ "YT: 12"    │                     │
│  └─────────────┘     └─────────────┘     └──────┬──────┘                     │
│                                                  │                            │
│                      ┌───────────────────────────┘                            │
│                      ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │ 7. Loop: For each podcast                                            │     │
│  │    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐       │     │
│  │    │ Progress     │────▶│ Fetch        │────▶│ Transcribe   │       │     │
│  │    │ "Pod 1/3"    │     │ Episodes     │     │ (if needed)  │       │     │
│  │    └──────────────┘     └──────────────┘     └──────────────┘       │     │
│  │           │                                         │                │     │
│  │           │          ┌──────────────────────────────┘                │     │
│  │           ▼          ▼                                               │     │
│  │    ┌──────────────────────┐                                          │     │
│  │    │ Progress             │  ◄── POST for EACH podcast iteration     │     │
│  │    │ "Pod 2/3: All-In"    │                                          │     │
│  │    │ "Transcribing ep 1"  │                                          │     │
│  │    └──────────────────────┘                                          │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                      │                                                        │
│                      ▼                                                        │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                     │
│  │ 8. Progress │────▶│ 9. Call     │────▶│ 10. Progress│                     │
│  │ "Summarize" │     │ Briefly API │     │ "Complete"  │                     │
│  └─────────────┘     │ /generate   │     └─────────────┘                     │
│                      └─────────────┘                                          │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Briefly Job API Endpoints (Thin Layer)

These are the **only endpoints** Briefly needs for job management. n8n handles everything else.

#### POST /api/jobs

**Purpose**: Create job, trigger n8n workflow

**Request**:
```json
{
    "type": "briefing",
    "params": {
        "hours_back": 24,
        "category_ids": ["cat-1", "cat-2"]
    }
}
```

**Response**:
```json
{
    "job_id": "job-uuid-123",
    "status": "pending",
    "n8n_triggered": true
}
```

**Implementation**:
```python
@router.post("/jobs")
async def create_job(req: CreateJobRequest):
    job_id = str(uuid4())

    # Store minimal job record
    jobs_cache[job_id] = {
        "id": job_id,
        "status": "pending",
        "progress": None,
        "created_at": datetime.utcnow().isoformat()
    }

    # Trigger n8n webhook (fire and forget)
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{N8N_WEBHOOK_URL}/briefing-ondemand",
            json={"job_id": job_id, **req.params}
        )

    return {"job_id": job_id, "status": "pending"}
```

---

#### GET /api/jobs/{job_id}

**Purpose**: Return current job status and progress

**Response** (during execution):
```json
{
    "id": "job-uuid-123",
    "status": "running",
    "progress": {
        "step": "Fetching podcasts",
        "step_detail": "Transcribing episode",
        "media_status": {
            "x": {"status": "done", "count": 25},
            "youtube": {"status": "done", "count": 12},
            "podcasts": {
                "status": "fetching",
                "stage": "transcribing",
                "current_podcast": "All-In Podcast",
                "current_episode": "E167: Market Analysis",
                "podcast_current": 2,
                "podcast_total": 3,
                "episode_current": 1,
                "episode_total": 4
            }
        },
        "elapsed_seconds": 45.2
    },
    "created_at": "2024-01-15T10:30:00Z"
}
```

**Response** (completed):
```json
{
    "id": "job-uuid-123",
    "status": "completed",
    "progress": {"step": "Complete"},
    "result": {
        "briefing_id": "briefing-456",
        "stats": {
            "x_count": 25,
            "youtube_count": 12,
            "podcast_count": 8
        }
    },
    "created_at": "2024-01-15T10:30:00Z",
    "completed_at": "2024-01-15T10:32:15Z"
}
```

---

#### POST /api/n8n/progress

**Purpose**: Webhook for n8n to push progress updates

**Request** (from n8n):
```json
{
    "job_id": "job-uuid-123",
    "step": "Fetching podcasts",
    "step_detail": "Transcribing episode",
    "media_status": {
        "x": {"status": "done", "count": 25},
        "youtube": {"status": "done", "count": 12},
        "podcasts": {
            "status": "fetching",
            "stage": "transcribing",
            "current_podcast": "All-In Podcast",
            "current_episode": "E167: Market Analysis",
            "podcast_current": 2,
            "podcast_total": 3
        }
    }
}
```

**Response**: `{"ok": true}`

**Implementation**:
```python
@router.post("/n8n/progress")
async def n8n_progress(req: ProgressUpdate):
    if req.job_id in jobs_cache:
        jobs_cache[req.job_id]["status"] = "running"
        jobs_cache[req.job_id]["progress"] = {
            "step": req.step,
            "step_detail": req.step_detail,
            "media_status": req.media_status,
            "updated_at": datetime.utcnow().isoformat()
        }
    return {"ok": True}
```

---

#### POST /api/n8n/complete

**Purpose**: Webhook for n8n to mark job complete

**Request** (from n8n):
```json
{
    "job_id": "job-uuid-123",
    "status": "completed",
    "result": {
        "briefing_id": "briefing-456",
        "stats": {...}
    }
}
```

**Or for failures**:
```json
{
    "job_id": "job-uuid-123",
    "status": "failed",
    "error": "X API rate limited after 50 tweets"
}
```

---

#### GET /api/jobs/active

**Purpose**: Check for running job (for reconnection on page load)

**Response** (if active job exists):
```json
{
    "id": "job-uuid-123",
    "status": "running",
    "progress": {...},
    "created_at": "..."
}
```

**Response** (no active job): `404`

---

### n8n Progress Reporting Sub-Workflow

This reusable sub-workflow is called throughout n8n workflows to report progress.

#### sub-progress-report

**Purpose**: POST progress update to Briefly API

**Input**:
```json
{
    "job_id": "job-uuid-123",
    "step": "Fetching podcasts",
    "step_detail": "Processing All-In Podcast",
    "media_status": {
        "x": {"status": "done", "count": 25},
        "podcasts": {
            "status": "fetching",
            "stage": "transcribing",
            "current_podcast": "All-In Podcast",
            "podcast_current": 2,
            "podcast_total": 3
        }
    }
}
```

**n8n Implementation**:
```
[Input] → [HTTP POST to /api/n8n/progress] → [Output]
```

**Usage in workflows**:
```
Every workflow step that takes >2 seconds should call sub-progress-report
```

---

### Granular Progress Patterns

#### Pattern 1: After Each Platform Fetch

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Fetch X      │────▶│ Progress:    │────▶│ Fetch YT     │
│              │     │ "X done: 25" │     │              │
└──────────────┘     └──────────────┘     └──────────────┘
```

**Progress payload**:
```json
{
    "job_id": "...",
    "step": "Fetching media",
    "media_status": {
        "x": {"status": "done", "count": 25},
        "youtube": {"status": "pending"},
        "podcasts": {"status": "pending"}
    }
}
```

---

#### Pattern 2: Loop Progress (Podcasts)

n8n's Loop node can call progress on each iteration:

```
┌─────────────────────────────────────────────────────────────┐
│  Loop Over Items: podcasts[]                                 │
│                                                              │
│  For each podcast:                                           │
│    1. sub-progress-report (current podcast)                  │
│    2. Fetch episodes from Taddy                              │
│    3. For episodes needing transcription:                    │
│       a. sub-progress-report (current episode, transcribing) │
│       b. Call Briefly /api/transcribe                        │
│       c. sub-progress-report (episode done)                  │
│    4. sub-progress-report (podcast done)                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Progress payload (mid-loop)**:
```json
{
    "job_id": "...",
    "step": "Processing podcasts",
    "step_detail": "Transcribing episode",
    "media_status": {
        "x": {"status": "done", "count": 25},
        "youtube": {"status": "done", "count": 12},
        "podcasts": {
            "status": "fetching",
            "stage": "transcribing",
            "current_podcast": "All-In Podcast",
            "current_episode": "E167: AI & Markets",
            "podcast_current": 2,
            "podcast_total": 5,
            "episode_current": 3,
            "episode_total": 4
        }
    }
}
```

---

#### Pattern 3: Stage Transitions

Report progress when entering new stages:

| Stage | Progress Step |
|-------|---------------|
| Start | "Starting briefing generation" |
| X fetch | "Fetching X posts" |
| X done | "X complete: 25 posts" |
| YT fetch | "Fetching YouTube videos" |
| YT done | "YouTube complete: 12 videos" |
| Pod start | "Processing podcasts (1/5)" |
| Pod transcribe | "Transcribing: All-In E167" |
| Pod done | "Podcasts complete: 8 episodes" |
| Summarize | "Generating AI summary" |
| Complete | "Briefing ready" |

---

### n8n Workflow: orchestrator-briefing-ondemand

**Trigger**: Webhook from Briefly POST /api/jobs

**Input**:
```json
{
    "job_id": "job-uuid-123",
    "hours_back": 24,
    "category_ids": ["tech", "news"]
}
```

**Flow**:

```
1. [Webhook Trigger]
   └─ Receives job_id, params

2. [Set Variables]
   └─ job_id, hours_back, sources

3. [sub-progress-report]
   └─ {"step": "Starting", "media_status": {all: pending}}

4. [Get Sources]
   └─ GET /api/sources?categories=tech,news

5. [Execute: adapter-x]
   └─ Fetch X posts (handles rate limits internally)

6. [sub-progress-report]
   └─ {"step": "X complete", "media_status": {x: done, count: N}}

7. [Execute: adapter-youtube]
   └─ Fetch YT videos + transcripts

8. [sub-progress-report]
   └─ {"step": "YouTube complete", "media_status": {youtube: done}}

9. [Loop: For each podcast source]
   │
   ├─ [sub-progress-report]
   │  └─ {"podcasts": {stage: "fetching", current_podcast: "..."}}
   │
   ├─ [Get Episodes from Taddy]
   │
   ├─ [Loop: Episodes needing transcription]
   │  │
   │  ├─ [sub-progress-report]
   │  │  └─ {stage: "transcribing", current_episode: "..."}
   │  │
   │  └─ [HTTP POST /api/transcribe]
   │     └─ Queue for Whisper processing
   │
   └─ [sub-progress-report]
      └─ {"podcasts": {podcast_current: N+1}}

10. [sub-progress-report]
    └─ {"step": "Generating summary"}

11. [HTTP POST /api/briefings/summarize]
    └─ Triggers Briefly to run LLM summarization

12. [sub-progress-report]
    └─ {"step": "Complete"}

13. [HTTP POST /api/n8n/complete]
    └─ {"job_id": "...", "status": "completed", "result": {...}}
```

---

### Job Cache Implementation (Briefly Side)

The Briefly-side job cache is intentionally thin - just enough to hold progress for UI polling.

**For detailed implementation**, see: `docs/PRD-dashboard-job-persistence.md`

That PRD covers:
- PostgreSQL (production) + SQLite (dev) dual-backend support
- Async database operations with asyncpg
- `JobService` class with full CRUD operations
- Reconnection logic for the dashboard

**Minimal interface needed by n8n**:

```python
# What n8n webhooks call:

POST /api/n8n/progress
  → job_cache.update_progress(job_id, progress_dict)

POST /api/n8n/complete
  → job_cache.complete(job_id, result_dict)
  OR
  → job_cache.fail(job_id, error_message)
```

**Key design points**:
- Don't persist on every progress update (too frequent, n8n calls often)
- Persist on create, complete, and fail
- Cleanup jobs older than 24 hours
- n8n is source of truth; this cache is for fast UI polling

---

### Dashboard Integration

The dashboard only needs to:
1. POST /api/jobs to start
2. Poll GET /api/jobs/{id} for progress
3. Check GET /api/jobs/active on page load

```javascript
// Alpine.js integration

async generateBriefing() {
    // Create job (triggers n8n)
    const resp = await fetch('/api/jobs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            type: 'briefing',
            params: {hours_back: this.hoursBack, category_ids: this.selectedCategories}
        })
    });
    const {job_id} = await resp.json();
    this.currentJobId = job_id;
    this.generating = true;
    this.startPolling();
},

startPolling() {
    this.pollInterval = setInterval(async () => {
        const job = await fetch(`/api/jobs/${this.currentJobId}`).then(r => r.json());

        // Update UI with progress
        this.jobStatus = job.status;
        this.jobProgress = job.progress;
        if (job.progress?.media_status) {
            this.mediaStatus = job.progress.media_status;
        }

        // Check for completion
        if (job.status === 'completed') {
            clearInterval(this.pollInterval);
            this.generating = false;
            this.showBriefing(job.result.briefing_id);
        } else if (job.status === 'failed') {
            clearInterval(this.pollInterval);
            this.generating = false;
            this.showError(job.error);
        }
    }, 1500);
},

// On page load - check for active job
async init() {
    try {
        const active = await fetch('/api/jobs/active').then(r => r.json());
        this.currentJobId = active.id;
        this.generating = true;
        this.reconnected = true;
        this.startPolling();
    } catch (e) {
        // No active job, normal state
    }
}
```

---

## Shared Sub-Workflows (Reusable Components)

These sub-workflows are called by platform adapters. Build these FIRST.

### 0. Progress Reporter (`sub-progress-report`) - BUILD FIRST

**Purpose**: Report progress to Briefly API for UI display. Called throughout all workflows.

**Input**:
```json
{
    "job_id": "job-uuid-123",
    "step": "Fetching podcasts",
    "step_detail": "Processing All-In Podcast",
    "media_status": {
        "x": {"status": "done", "count": 25},
        "youtube": {"status": "pending"},
        "podcasts": {
            "status": "fetching",
            "stage": "transcribing",
            "current_podcast": "All-In Podcast",
            "podcast_current": 2,
            "podcast_total": 5
        }
    }
}
```

**n8n Implementation**:
```
[Start] → [HTTP Request]
           Method: POST
           URL: {{$env.BRIEFLY_API_URL}}/api/n8n/progress
           Body: {{ $json }}
        → [End]
```

**Usage**: Call this sub-workflow:
- After each platform adapter completes
- Inside loops (for each podcast/episode)
- Before and after long operations (transcription, summarization)
- On errors (with error details in step_detail)

---

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
│   ├── sub-progress-report.json   # ⭐ BUILD FIRST - progress to Briefly
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
    ├── orchestrator-briefing-ondemand.json  # ⭐ Dashboard triggers this
    └── orchestrator-health.json   # Watchdog/monitoring
```

**Import Order**: sub/ → adapters/ → delivery/ → scheduler/ → orchestrator/

**Critical Path** (minimum for dashboard integration):
1. `sub-progress-report.json` - Progress reporting
2. `orchestrator-briefing-ondemand.json` - Full briefing pipeline
3. Briefly job endpoints - Receive webhooks

---

## Implementation Phases

### Phase 0: Foundation (API Endpoints + Job Management)
**Goal**: Briefly API ready to receive n8n calls AND report progress

**Job Management Endpoints (Priority - enables all other work)**:
- [ ] `POST /api/jobs` - Create job, trigger n8n webhook
- [ ] `GET /api/jobs/{id}` - Return job status and progress
- [ ] `GET /api/jobs/active` - Return currently running job (for reconnection)
- [ ] `POST /api/n8n/progress` - Webhook for n8n progress updates
- [ ] `POST /api/n8n/complete` - Webhook for n8n job completion
- [ ] `JobCache` class - Thin in-memory + disk persistence

**Content Endpoints**:
- [ ] `POST /api/content/ingest` - Bulk content ingestion with dedup
- [ ] `GET /api/sources?platform={x}` - Sources by platform
- [ ] `GET /api/extraction/status` - Last extraction times
- [ ] `POST /api/content/transcribe` - Queue transcription job
- [ ] `POST /api/briefings/summarize` - Trigger LLM summarization (called by n8n)

**Deliverable**: OpenAPI spec for n8n HTTP nodes

**Teams can work in parallel**:
- Briefly team: Implement job endpoints
- n8n team: Build sub-progress-report and test against mock endpoint

---

### Phase 1: Core Sub-Workflows
**Goal**: Reusable building blocks that all adapters need

- [ ] `sub-progress-report` - POST progress to Briefly (BUILD FIRST)
- [ ] `sub-source-fetcher` - Simple HTTP GET wrapper
- [ ] `sub-content-ingest` - POST to ingest endpoint
- [ ] `sub-error-handler` - Logging + conditional alerts
- [ ] `sub-notify` - Telegram HTTP POST (start simple)
- [ ] `sub-metrics` - Store to n8n static data

**Test**: Each sub-workflow independently with mock data
**Critical**: Test progress reporting end-to-end (n8n → Briefly → Dashboard)

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

## Decisions Made

1. **Job persistence**: n8n is source of truth, Briefly has thin cache for progress
   - **Decision**: Thin `JobCache` in Briefly, full execution history in n8n
   - **Rationale**: Avoid duplicating n8n's persistence, minimize Briefly complexity

2. **Progress reporting**: n8n POSTs progress to Briefly webhooks
   - **Decision**: `sub-progress-report` calls `POST /api/n8n/progress`
   - **Rationale**: Real-time progress without polling n8n API

3. **Dashboard integration**: Webhook triggers + progress webhooks
   - **Decision**: Dashboard → Briefly → n8n webhook, n8n → Briefly progress webhook
   - **Rationale**: Clean separation, dashboard doesn't know about n8n directly

4. **Transcript processing**: n8n queues, Python/Whisper transcribes
   - **Decision**: n8n calls `POST /api/content/transcribe`, Briefly runs Whisper
   - **Rationale**: GPU access needed for local Whisper, n8n orchestrates

## Open Questions

1. **Multi-user content isolation**: Should n8n store content per-user or globally?
   - **Current thinking**: Global store with user→source mapping
   - **Trade-off**: Simpler dedup vs. per-user filtering complexity

2. **Rate limit state persistence**: n8n static data vs. Redis vs. Briefly API?
   - **Current thinking**: n8n static data (simple, survives restarts)
   - **Trade-off**: Not shared across n8n instances if scaled

3. **n8n fallback**: What happens if n8n is down when user clicks Generate?
   - **Options**: (a) Error message, (b) Fall back to local Python execution
   - **Recommendation**: Start with error message, add fallback later if needed

---

## References

- [n8n Documentation](https://docs.n8n.io/)
- [X API Rate Limits](https://developer.twitter.com/en/docs/twitter-api/rate-limits)
- [Briefly Current Architecture](../src/briefly/adapters/)
