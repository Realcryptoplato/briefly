# PRD: Dashboard Integration with Job Persistence

## Overview

The backend now persists jobs to a database (SQLite locally, PostgreSQL in production) instead of keeping them in memory. This enables:
- Jobs survive server restarts
- Job history is queryable
- Multiple server instances can share job state

## Dashboard Impact: Minimal

**The dashboard should work exactly the same way.** The only changes are:
1. Job IDs are now UUIDs instead of timestamps
2. New optional endpoints available for enhanced features

## API Endpoints

### Existing (No Changes Required)

| Endpoint | Method | Description | Dashboard Uses |
|----------|--------|-------------|----------------|
| `/api/briefings/generate` | POST | Create briefing job | Yes - triggers generation |
| `/api/briefings/generate/{job_id}` | GET | Get job status | Yes - polls for progress |
| `/api/briefings` | GET | List recent briefings | Yes - shows history |
| `/api/briefings/latest` | GET | Get latest briefing | Yes - shows result |

**These endpoints work exactly as before.** The response format is identical.

### New Endpoints (Optional Enhancements)

| Endpoint | Method | Description | Use Case |
|----------|--------|-------------|----------|
| `/api/briefings/jobs` | GET | List all jobs (not just briefings) | Job history page |
| `/api/briefings/jobs/active` | GET | Get currently running job | Show spinner/progress |
| `/api/briefings/jobs/{id}` | GET | Get job by UUID | Deep link to job |

## Response Format Changes

### Job Status Response

**Before (in-memory):**
```json
{
  "status": "processing",
  "step": "Fetching sources...",
  "progress": {"current": 5, "total": 10}
}
```

**After (persisted) - backward compatible:**
```json
{
  "id": "c2fc9d62-a06e-4d7f-82eb-f77fc33cd897",
  "type": "briefing",
  "status": "running",
  "created_at": "2026-01-05T02:12:31.469387+00:00",
  "started_at": "2026-01-05T02:12:31.470061+00:00",
  "step": "Fetching sources...",
  "current": 5,
  "total": 10,
  "media_status": {
    "x": {"status": "completed", "count": 15},
    "youtube": {"status": "processing", "count": 5},
    "podcasts": {"status": "pending", "count": 0}
  }
}
```

**Key additions:**
- `id` - UUID instead of timestamp
- `type` - Job type (briefing, transcription, extraction)
- `created_at`, `started_at`, `completed_at` - ISO timestamps
- `media_status` - Per-platform progress (already existed)

## Job States

| Status | Meaning | Dashboard Action |
|--------|---------|------------------|
| `pending` | Job created, not started | Show "Queued" |
| `running` | Job in progress | Show progress bar, poll for updates |
| `completed` | Job finished successfully | Show result, stop polling |
| `failed` | Job failed | Show error message, stop polling |

## Recommended Dashboard Changes

### 1. No Changes Required (Works Today)
The current polling mechanism works:
```javascript
// Existing code works as-is
const checkStatus = async (jobId) => {
  const res = await fetch(`/api/briefings/generate/${jobId}`);
  const data = await res.json();
  if (data.status === 'completed') {
    showResult(data.result);
  } else if (data.status === 'failed') {
    showError(data.error);
  } else {
    setTimeout(() => checkStatus(jobId), 2000);
  }
};
```

### 2. Optional Enhancement: Active Job Detection
Show if a job is already running when page loads:
```javascript
const checkActiveJob = async () => {
  const res = await fetch('/api/briefings/jobs/active');
  const data = await res.json();
  if (data.active) {
    // Resume polling existing job
    showProgress(data.job);
    pollJobStatus(data.job.id);
  }
};
```

### 3. Optional Enhancement: Job History Page
List recent jobs with status:
```javascript
const loadJobHistory = async () => {
  const res = await fetch('/api/briefings/jobs?limit=20');
  const jobs = await res.json();
  // Display job list with status, timestamps, etc.
};
```

## Testing Checklist

- [ ] Generate briefing - job created, progress shown, completes
- [ ] Refresh page during generation - job still running (active job detected)
- [ ] Restart server during generation - job state preserved
- [ ] View job history - past jobs listed
- [ ] Failed job shows error message

## Branch Information

**Branch:** `claude/job-persistence-JyGZe`
**Tests:** 59/59 passing
**Files Changed:**
- `src/briefly/services/jobs.py` - New JobService
- `src/briefly/api/routes/briefings.py` - Updated endpoints
- `src/briefly/api/main.py` - Service initialization

## Questions for Dashboard Team

1. Do you want a dedicated "Job History" page?
2. Should we show active job on page load?
3. Any preference on job ID format display (full UUID vs shortened)?
4. Need WebSocket for real-time updates instead of polling?
