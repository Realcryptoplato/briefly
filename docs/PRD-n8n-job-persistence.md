# PRD: n8n Job Persistence & UI Integration for Briefly 3000

## Overview

This PRD addresses the **control plane** for Briefly 3000 - how jobs are persisted, tracked, and displayed to users. It complements `PRD-n8n-extraction.md` which covers the extraction workflows themselves.

## Problem Statement

Current architecture has critical stability issues:

```python
# briefings.py:20 - THE PROBLEM
_jobs: dict[str, dict] = {}  # In-memory, lost on restart
```

**User Impact:**
1. **Browser reload** → Lose visibility into running job (job continues blindly)
2. **Server restart** → All in-flight jobs lost, no recovery
3. **Tab close** → Can't reconnect to see results
4. **Multiple tabs** → Can trigger duplicate jobs
5. **Long transcriptions** → 5-10 min Whisper jobs with no progress visibility

**Developer Impact:**
1. No job history for debugging
2. Can't see why a job failed after the fact
3. No metrics on job duration, success rates
4. Memory leaks from orphaned job entries

---

## Goals

1. **Persistent Jobs** - Jobs survive server restarts, tracked in database
2. **Reconnectable Progress** - User can reconnect to running job from any tab/device
3. **n8n Integration** - Heavy workflows run in n8n with status queryable from dashboard
4. **Graceful Degradation** - If n8n down, fall back to local execution
5. **Job History** - View past jobs, their status, duration, errors

## Non-Goals

- Real-time websocket updates (polling is sufficient at 1-2s intervals)
- Multi-tenant job isolation (single-user for now)
- Job queueing/prioritization (one job at a time is fine)
- Distributed job execution (single n8n instance)

---

## Architecture

### Current Architecture (Problematic)

```
┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│   FastAPI    │
│   (Alpine)   │     │   Server     │
└──────────────┘     └──────────────┘
       │                    │
       │ poll /status       │ in-memory _jobs dict
       │◀───────────────────│
       │                    │
    Job lost on          Job lost on
    tab close            server restart
```

### Target Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│   FastAPI    │────▶│     n8n      │
│   (Alpine)   │     │  (thin API)  │     │  (workflows) │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       │                    ▼                    ▼
       │             ┌──────────────┐     ┌──────────────┐
       │             │   SQLite     │     │  n8n DB      │
       │             │  (jobs.db)   │     │  (executions)│
       │             └──────────────┘     └──────────────┘
       │                    │                    │
       │ poll              jobs                 execution
       │ /api/jobs/{id}    table                history
       │◀──────────────────│◀───────────────────│
       │                    │
    Can reconnect       Survives
    from any tab        restarts
```

### Key Changes

| Current | Target |
|---------|--------|
| `_jobs` dict in memory | SQLite `jobs` table |
| Job ID = timestamp | Job ID = UUID |
| Progress via callback | Progress via DB + n8n API |
| Lost on reload | Reconnectable via job_id |
| No history | Full execution history |

---

## Data Model

### Jobs Table (PostgreSQL for Production, SQLite for Dev/Test)

**Database Selection:**
- **Production**: PostgreSQL (uses existing Supabase/self-hosted instance)
- **Development/Testing**: SQLite (zero-config, file-based)
- **Switching**: Based on `DATABASE_URL` environment variable

```sql
-- PostgreSQL version (production)
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50) NOT NULL,        -- 'briefing', 'transcription', 'extraction'
    status VARCHAR(20) NOT NULL,      -- 'pending', 'running', 'completed', 'failed'

    -- Timing
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    -- n8n Integration
    n8n_execution_id VARCHAR(100),    -- n8n execution ID if delegated
    n8n_workflow_id VARCHAR(100),     -- which workflow

    -- Progress (JSONB for efficient querying)
    progress JSONB,                   -- {"step": "...", "current": 5, "total": 10, ...}

    -- Input/Output
    input JSONB,                      -- Job parameters (sources, hours_back, etc.)
    output JSONB,                     -- Result data (briefing, stats, etc.)
    error TEXT,                       -- Error message if failed

    -- Metadata
    source VARCHAR(20) DEFAULT 'local' -- 'local' or 'n8n'
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id);
CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(type, status);
```

```sql
-- SQLite version (development/testing)
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,              -- UUID as text
    type TEXT NOT NULL,
    status TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    n8n_execution_id TEXT,
    n8n_workflow_id TEXT,

    progress JSON,
    input JSON,
    output JSON,
    error TEXT,

    source TEXT DEFAULT 'local'
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id);
```

### Progress JSON Schema

```json
{
    "step": "Fetching media",
    "step_detail": "Processing podcasts",
    "current": 5,
    "total": 10,
    "elapsed_seconds": 45.2,
    "eta_seconds": 30.0,
    "media_status": {
        "x": {"status": "done", "count": 25},
        "youtube": {"status": "done", "count": 8},
        "podcasts": {
            "status": "fetching",
            "stage": "transcribing",
            "current_podcast": "All-In Podcast",
            "current_episode": "E167: Market Analysis",
            "episode_current": 2,
            "episode_total": 5
        }
    }
}
```

---

## API Design

### Job Management Endpoints

```
POST /api/jobs
    Create a new job, return job_id immediately
    Body: {"type": "briefing", "params": {...}}
    Response: {"job_id": "uuid", "status": "pending"}

GET /api/jobs/{job_id}
    Get job status and progress
    Response: Full job object with progress

GET /api/jobs/{job_id}/result
    Get job result (blocks until complete or timeout)
    Query: ?timeout=30
    Response: Job output or 408 if timeout

DELETE /api/jobs/{job_id}
    Cancel a running job (best effort)

GET /api/jobs
    List recent jobs
    Query: ?status=running&limit=10
    Response: Array of job summaries

GET /api/jobs/active
    Get the currently running job (if any)
    Response: Job object or 404
```

### n8n Proxy Endpoints

```
POST /api/n8n/trigger/{workflow_id}
    Trigger n8n workflow, create local job to track it
    Body: Workflow input parameters
    Response: {"job_id": "uuid", "n8n_execution_id": "..."}

GET /api/n8n/execution/{execution_id}
    Proxy to n8n API for execution status
    Response: n8n execution object

POST /api/n8n/webhook/progress
    Webhook for n8n to push progress updates
    Body: {"job_id": "...", "progress": {...}}
```

---

## Job Lifecycle

### Local Job Flow

```
1. Browser: POST /api/jobs {type: "briefing", params: {...}}
2. Server: Insert job with status="pending", return job_id
3. Server: Start background task, update status="running"
4. Server: Update progress in DB as work proceeds
5. Browser: Poll GET /api/jobs/{job_id} every 1-2 seconds
6. Server: Return current progress from DB
7. Server: On completion, update status="completed", store output
8. Browser: Detect completion, display results
```

### n8n-Delegated Job Flow

```
1. Browser: POST /api/jobs {type: "briefing", params: {...}, delegate: "n8n"}
2. Server: Insert job with status="pending"
3. Server: POST to n8n webhook, get execution_id
4. Server: Update job with n8n_execution_id, status="running"
5. Browser: Poll GET /api/jobs/{job_id}
6. Server: Query n8n API for execution status, merge progress
7. n8n: (Optional) POST progress updates to /api/n8n/webhook/progress
8. Server: On n8n completion, update job status, store output
9. Browser: Detect completion, display results
```

### Reconnection Flow

```
1. User closes tab while job running
2. Job continues in background (local) or n8n (delegated)
3. User opens new tab
4. Browser: GET /api/jobs/active
5. Server: Return running job if exists
6. Browser: Resume polling that job's progress
7. UI shows "Reconnected to running job..."
```

---

## UI Integration

### Alpine.js State Management

```javascript
data() {
    return {
        currentJobId: null,
        jobStatus: null,
        reconnected: false,

        // On page load
        async init() {
            // Check for active job to reconnect
            const active = await fetch('/api/jobs/active').then(r => r.ok ? r.json() : null);
            if (active) {
                this.currentJobId = active.id;
                this.reconnected = true;
                this.startPolling();
            }
        },

        async generateBriefing() {
            // Create job
            const resp = await fetch('/api/jobs', {
                method: 'POST',
                body: JSON.stringify({type: 'briefing', params: {...}})
            });
            const job = await resp.json();
            this.currentJobId = job.job_id;
            this.startPolling();
        },

        startPolling() {
            this.pollInterval = setInterval(async () => {
                const status = await fetch(`/api/jobs/${this.currentJobId}`).then(r => r.json());
                this.jobStatus = status;

                if (status.status === 'completed' || status.status === 'failed') {
                    clearInterval(this.pollInterval);
                    this.handleJobComplete(status);
                }
            }, 1500);
        }
    }
}
```

### Progress Display

```html
<!-- Reconnection banner -->
<div x-show="reconnected" class="bg-blue-900 p-2 rounded mb-4">
    Reconnected to running job started at {{ formatTime(jobStatus?.started_at) }}
</div>

<!-- Progress stepper (existing, now reads from jobStatus) -->
<div class="stepper">
    <!-- Steps now reflect jobStatus.progress.step -->
</div>

<!-- Job history link -->
<a href="#" @click="showJobHistory = true">
    View job history ({{ jobHistory.length }} past jobs)
</a>
```

---

## n8n Workflow Integration

### Progress Webhook Pattern

n8n workflows call back to Briefly with progress updates:

```
┌─────────────────────────────────────────────────────────┐
│  n8n Workflow: orchestrator-daily                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. [Trigger] ──► 2. [Set job_id]                      │
│                        │                                │
│                        ▼                                │
│  3. [HTTP POST] ──────────────────────────────────────► │ POST /api/n8n/webhook/progress
│     "Starting extraction"                               │ {job_id, step: "Extracting X"}
│                        │                                │
│                        ▼                                │
│  4. [Execute: adapter-x]                               │
│                        │                                │
│                        ▼                                │
│  5. [HTTP POST] ──────────────────────────────────────► │ POST /api/n8n/webhook/progress
│     "X complete: 25 items"                             │ {job_id, step: "X done", x_count: 25}
│                        │                                │
│                        ▼                                │
│  ... continue for youtube, podcasts ...                │
│                        │                                │
│                        ▼                                │
│  N. [HTTP POST] ──────────────────────────────────────► │ POST /api/n8n/webhook/progress
│     "Complete"                                         │ {job_id, step: "Complete", output: {...}}
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Alternative: Polling n8n API

If webhook complexity is unwanted, Briefly can poll n8n:

```python
async def poll_n8n_status(job: Job):
    """Poll n8n for execution status."""
    if not job.n8n_execution_id:
        return

    resp = await httpx.get(
        f"{N8N_URL}/api/v1/executions/{job.n8n_execution_id}",
        headers={"X-N8N-API-KEY": N8N_API_KEY}
    )
    execution = resp.json()

    # Map n8n status to our status
    status_map = {
        "running": "running",
        "success": "completed",
        "error": "failed",
        "waiting": "running"
    }

    job.status = status_map.get(execution["status"], "running")

    # Extract progress from n8n execution data
    if execution.get("data", {}).get("resultData"):
        job.progress = extract_progress(execution["data"])
```

---

## Migration Path

### Phase 1: Add Database Layer (Non-Breaking)

1. Create `jobs.db` with schema
2. Add `JobService` class that writes to both memory and DB
3. Existing code continues to work
4. New endpoints read from DB

```python
class JobService:
    def create_job(self, type: str, params: dict) -> Job:
        job_id = str(uuid4())
        job = Job(id=job_id, type=type, status="pending", input=params)

        # Write to both (temporary)
        _jobs[job_id] = job.to_dict()  # Legacy
        self.db.insert(job)            # New

        return job

    def update_progress(self, job_id: str, progress: dict):
        # Update both
        if job_id in _jobs:
            _jobs[job_id]["progress"] = progress
        self.db.update_progress(job_id, progress)
```

### Phase 2: Add Reconnection

1. Add `GET /api/jobs/active` endpoint
2. Update frontend to check for active job on load
3. Test reconnection flow

### Phase 3: Add n8n Integration

1. Add n8n trigger endpoint
2. Add progress webhook endpoint
3. Update `generateBriefing` to optionally delegate to n8n
4. Test full n8n flow

### Phase 4: Remove Legacy

1. Remove in-memory `_jobs` dict
2. All reads/writes through `JobService`
3. Full persistence achieved

---

## Implementation Details

### Job Service Class (PostgreSQL + SQLite Abstraction)

```python
# services/jobs.py

"""
Job persistence service with PostgreSQL (prod) and SQLite (dev) support.

Database selection:
- If DATABASE_URL env var is set → PostgreSQL (async with asyncpg)
- Otherwise → SQLite (sync, file-based at .cache/jobs.db)

For cloud agents: Focus on the abstract interface. The _get_db() method
handles database selection automatically based on environment.
"""

from pathlib import Path
import json
import os
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass, asdict, field
from typing import Optional, Protocol, Any
from enum import Enum

# Environment-based DB selection
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL connection string
SQLITE_PATH = Path(__file__).parent.parent.parent.parent / ".cache" / "jobs.db"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    BRIEFING = "briefing"
    TRANSCRIPTION = "transcription"
    EXTRACTION = "extraction"


@dataclass
class Job:
    id: str
    type: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    n8n_execution_id: Optional[str] = None
    n8n_workflow_id: Optional[str] = None
    progress: Optional[dict] = None
    input: Optional[dict] = None
    output: Optional[dict] = None
    error: Optional[str] = None
    source: str = "local"

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            **asdict(self),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class DatabaseBackend(Protocol):
    """Protocol for database backends (PostgreSQL/SQLite)."""

    async def init_schema(self) -> None: ...
    async def insert_job(self, job: Job) -> None: ...
    async def get_job(self, job_id: str) -> Optional[Job]: ...
    async def get_active_job(self) -> Optional[Job]: ...
    async def update_progress(self, job_id: str, progress: dict) -> None: ...
    async def complete_job(self, job_id: str, output: dict) -> None: ...
    async def fail_job(self, job_id: str, error: str) -> None: ...
    async def list_recent(self, limit: int) -> list[Job]: ...


class PostgreSQLBackend:
    """PostgreSQL backend using asyncpg for production."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(self.database_url)
        return self._pool

    async def init_schema(self) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    type VARCHAR(50) NOT NULL,
                    status VARCHAR(20) NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    n8n_execution_id VARCHAR(100),
                    n8n_workflow_id VARCHAR(100),
                    progress JSONB,
                    input JSONB,
                    output JSONB,
                    error TEXT,
                    source VARCHAR(20) DEFAULT 'local'
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id);
            ''')

    async def insert_job(self, job: Job) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', job.id, job.type, job.status, job.created_at,
                json.dumps(job.input) if job.input else None, job.source)

    async def get_job(self, job_id: str) -> Optional[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM jobs WHERE id = $1', job_id)
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM jobs
                WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            ''')
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET progress = $1, status = 'running',
                started_at = COALESCE(started_at, NOW()) WHERE id = $2
            ''', json.dumps(progress), job_id)

    async def complete_job(self, job_id: str, output: dict) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET status = 'completed', completed_at = NOW(),
                output = $1 WHERE id = $2
            ''', json.dumps(output), job_id)

    async def fail_job(self, job_id: str, error: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET status = 'failed', completed_at = NOW(),
                error = $1 WHERE id = $2
            ''', error, job_id)

    async def list_recent(self, limit: int = 20) -> list[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1', limit
            )
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row) -> Job:
        return Job(
            id=str(row['id']),
            type=row['type'],
            status=row['status'],
            created_at=row['created_at'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
            n8n_execution_id=row['n8n_execution_id'],
            n8n_workflow_id=row['n8n_workflow_id'],
            progress=row['progress'],
            input=row['input'],
            output=row['output'],
            error=row['error'],
            source=row['source'] or 'local'
        )


class SQLiteBackend:
    """SQLite backend for development and testing."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    n8n_execution_id TEXT,
                    n8n_workflow_id TEXT,
                    progress JSON,
                    input JSON,
                    output JSON,
                    error TEXT,
                    source TEXT DEFAULT 'local'
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)')

    async def insert_job(self, job: Job) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (job.id, job.type, job.status, job.created_at.isoformat(),
                  json.dumps(job.input) if job.input else None, job.source))

    async def get_job(self, job_id: str) -> Optional[Job]:
        with self._get_conn() as conn:
            row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        with self._get_conn() as conn:
            row = conn.execute('''
                SELECT * FROM jobs WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            ''').fetchone()
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET progress = ?, status = 'running',
                started_at = COALESCE(started_at, ?) WHERE id = ?
            ''', (json.dumps(progress), datetime.now(timezone.utc).isoformat(), job_id))

    async def complete_job(self, job_id: str, output: dict) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET status = 'completed', completed_at = ?, output = ? WHERE id = ?
            ''', (datetime.now(timezone.utc).isoformat(), json.dumps(output), job_id))

    async def fail_job(self, job_id: str, error: str) -> None:
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET status = 'failed', completed_at = ?, error = ? WHERE id = ?
            ''', (datetime.now(timezone.utc).isoformat(), error, job_id))

    async def list_recent(self, limit: int = 20) -> list[Job]:
        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,)
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row) -> Job:
        from datetime import datetime
        def parse_dt(s):
            if not s: return None
            if isinstance(s, datetime): return s
            return datetime.fromisoformat(s.replace('Z', '+00:00'))

        return Job(
            id=row['id'],
            type=row['type'],
            status=row['status'],
            created_at=parse_dt(row['created_at']),
            started_at=parse_dt(row['started_at']),
            completed_at=parse_dt(row['completed_at']),
            n8n_execution_id=row['n8n_execution_id'],
            n8n_workflow_id=row['n8n_workflow_id'],
            progress=json.loads(row['progress']) if row['progress'] else None,
            input=json.loads(row['input']) if row['input'] else None,
            output=json.loads(row['output']) if row['output'] else None,
            error=row['error'],
            source=row['source'] or 'local'
        )


class JobService:
    """
    Main job service with automatic database backend selection.

    Usage:
        service = JobService()
        await service.init()  # Call once on startup

        job = await service.create("briefing", {"hours_back": 24})
        await service.update_progress(job.id, {"step": "fetching"})
        await service.complete(job.id, {"items": 45})
    """

    _instance: Optional['JobService'] = None

    def __init__(self):
        if DATABASE_URL:
            self._backend = PostgreSQLBackend(DATABASE_URL)
            self._db_type = "postgresql"
        else:
            self._backend = SQLiteBackend(SQLITE_PATH)
            self._db_type = "sqlite"

    @classmethod
    def get_instance(cls) -> 'JobService':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def db_type(self) -> str:
        return self._db_type

    async def init(self) -> None:
        """Initialize database schema. Call on app startup."""
        await self._backend.init_schema()

    async def create(self, job_type: str, params: dict, source: str = "local") -> Job:
        """Create a new job."""
        job = Job(
            id=str(uuid4()),
            type=job_type,
            status=JobStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            input=params,
            source=source
        )
        await self._backend.insert_job(job)
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        return await self._backend.get_job(job_id)

    async def get_active(self) -> Optional[Job]:
        """Get currently running job if any."""
        return await self._backend.get_active_job()

    async def update_progress(self, job_id: str, progress: dict) -> None:
        """Update job progress."""
        await self._backend.update_progress(job_id, progress)

    async def complete(self, job_id: str, output: dict) -> None:
        """Mark job as completed with output."""
        await self._backend.complete_job(job_id, output)

    async def fail(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        await self._backend.fail_job(job_id, error)

    async def list_recent(self, limit: int = 20) -> list[Job]:
        """List recent jobs."""
        return await self._backend.list_recent(limit)


# Convenience function for getting the service
def get_job_service() -> JobService:
    """Get the job service singleton."""
    return JobService.get_instance()
```

### Updated Briefing Route

```python
# routes/briefings.py

from briefly.services.jobs import JobService

job_service = JobService()

@router.post("/generate")
async def generate_briefing(req: GenerateRequest, background_tasks: BackgroundTasks) -> dict:
    # Create persistent job
    job = job_service.create("briefing", {
        "hours_back": req.hours_back,
        "category_ids": req.category_ids
    })

    # ... existing source loading logic ...

    def progress_callback(step: str, current: int, total: int, elapsed: float, media_status: dict | None = None):
        progress = {
            "step": step,
            "current": current,
            "total": total,
            "elapsed_seconds": round(elapsed, 1),
            "media_status": media_status
        }
        job_service.update_progress(job.id, progress)

    async def run_briefing():
        try:
            service = CurationService()
            result = await service.create_briefing(
                # ... params ...
                progress_callback=progress_callback,
            )
            job_service.complete(job.id, result)
        except Exception as e:
            job_service.fail(job.id, str(e))

    background_tasks.add_task(run_briefing)

    return {"job_id": job.id, "status": "pending"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = job_service.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return asdict(job)


@router.get("/jobs/active")
async def get_active_job() -> dict:
    job = job_service.get_active()
    if not job:
        raise HTTPException(404, "No active job")
    return asdict(job)
```

---

## Testing Plan

### Unit Tests

1. `JobService.create()` - Creates job in DB
2. `JobService.update_progress()` - Updates progress JSON
3. `JobService.complete()` - Sets status and output
4. `JobService.get_active()` - Returns running job

### Integration Tests

1. Create job → Poll status → See progress → Complete
2. Create job → Close connection → Reconnect → Resume polling
3. Create job → Server restart → Job status preserved
4. Create n8n job → Track execution → Complete

### Manual Tests

1. Start briefing → Close tab → Reopen → See progress
2. Start briefing → Restart server → Job recovers (future with n8n)
3. View job history → See past 20 jobs

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Job survival on tab close | 0% visibility | 100% reconnectable |
| Job survival on server restart | 0% | 100% (with n8n) |
| Job history retention | 0 | Last 100 jobs |
| Progress poll latency | N/A | <100ms |
| Duplicate job prevention | None | Active job check |

---

## Open Questions

1. **Job expiration**: How long to keep completed jobs?
   - Proposal: 7 days, configurable

2. **Concurrent jobs**: Allow multiple jobs or strict single-job?
   - Proposal: Single active job, queue others (later)

3. **n8n fallback**: If n8n is down, auto-fallback to local?
   - Proposal: Yes, with warning in UI

4. **Progress granularity**: How often to update DB?
   - Proposal: Every 2 seconds max, debounced

---

## Dependencies

**Production (PostgreSQL)**:
- `asyncpg` - Async PostgreSQL driver
- Existing PostgreSQL instance (Supabase or self-hosted)

**Development (SQLite)**:
- SQLite (stdlib, no new deps)

**Shared**:
- n8n API access (for delegated jobs)
- Existing `PRD-n8n-extraction.md` workflows

**pyproject.toml additions**:
```toml
[project.optional-dependencies]
postgres = ["asyncpg>=0.29.0"]
```

---

## Cloud Agent Implementation Notes

**For Claude Cloud Agents** (non-CLI execution environment):

### Limitations to Consider

1. **No Local Execution**: Cloud agents cannot run `pytest` or start servers
2. **No File System Persistence**: Cannot verify SQLite file creation
3. **No Network Calls**: Cannot test actual PostgreSQL connections
4. **No Environment Variables**: Cannot set `DATABASE_URL` at runtime

### Implementation Strategy for Cloud Agents

1. **Code Generation Only**: Focus on writing correct code, not running it
2. **Type Hints**: Add comprehensive type hints for static analysis
3. **Docstrings**: Document behavior that would normally be tested
4. **Mock-Friendly Design**: Structure code to be easily mockable

### Testing Approach

```python
# tests/test_jobs.py - Cloud agent should generate these tests
# but acknowledge they must be run locally or in CI

import pytest
from unittest.mock import AsyncMock, patch

@pytest.fixture
def mock_sqlite_backend():
    """Mock SQLite backend for unit tests."""
    backend = AsyncMock()
    backend.get_job.return_value = Job(
        id="test-123",
        type="briefing",
        status="running",
        created_at=datetime.now(timezone.utc)
    )
    return backend

async def test_job_service_uses_sqlite_when_no_database_url(mock_sqlite_backend):
    """Verify SQLite is used when DATABASE_URL is not set."""
    with patch.dict(os.environ, {}, clear=True):
        with patch('briefly.services.jobs.SQLiteBackend', return_value=mock_sqlite_backend):
            service = JobService()
            assert service.db_type == "sqlite"

async def test_job_service_uses_postgres_when_database_url_set(mock_sqlite_backend):
    """Verify PostgreSQL is used when DATABASE_URL is set."""
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://..."}):
        with patch('briefly.services.jobs.PostgreSQLBackend') as mock_pg:
            service = JobService()
            assert service.db_type == "postgresql"
```

### Verification Checklist (for human review)

- [ ] `JobService` class created in `src/briefly/services/jobs.py`
- [ ] `Job` dataclass with all required fields
- [ ] `PostgreSQLBackend` with asyncpg
- [ ] `SQLiteBackend` with sqlite3
- [ ] Environment-based backend selection
- [ ] API routes updated in `src/briefly/api/routes/briefings.py`
- [ ] Tests in `tests/test_jobs.py`
- [ ] `asyncpg` added to optional deps in `pyproject.toml`

---

## Timeline Estimate

| Phase | Effort |
|-------|--------|
| Phase 1: Database layer | 2-3 hours |
| Phase 2: Reconnection UI | 1-2 hours |
| Phase 3: n8n integration | 3-4 hours |
| Phase 4: Cleanup & testing | 1-2 hours |

**Total: ~8-10 hours of development**

---

## References

- [n8n Execution API](https://docs.n8n.io/api/api-reference/#tag/Execution)
- [SQLite JSON Functions](https://www.sqlite.org/json1.html)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- `PRD-n8n-extraction.md` - Companion extraction workflow PRD
