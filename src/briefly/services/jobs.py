"""
Job persistence service with PostgreSQL (prod) and SQLite (dev) support.

Database selection:
- If DATABASE_URL env var is set -> PostgreSQL (async with asyncpg)
- Otherwise -> SQLite (sync, file-based at .cache/jobs.db)
"""

from pathlib import Path
import json
import os
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass, asdict
from typing import Optional, Protocol, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)

# Environment-based DB selection
DATABASE_URL = os.getenv("DATABASE_URL")
SQLITE_PATH = Path(__file__).parent.parent.parent.parent / ".cache" / "jobs.db"


class JobStatus(str, Enum):
    """Job execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    """Types of jobs that can be executed."""
    BRIEFING = "briefing"
    TRANSCRIPTION = "transcription"
    EXTRACTION = "extraction"


@dataclass
class Job:
    """
    Represents a job in the system.

    Jobs track long-running operations like briefing generation,
    transcription, or content extraction from n8n workflows.
    """
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

    async def init_schema(self) -> None:
        """Initialize database schema."""
        ...

    async def insert_job(self, job: Job) -> None:
        """Insert a new job."""
        ...

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        ...

    async def get_active_job(self) -> Optional[Job]:
        """Get currently active job."""
        ...

    async def update_progress(self, job_id: str, progress: dict) -> None:
        """Update job progress."""
        ...

    async def complete_job(self, job_id: str, output: dict) -> None:
        """Mark job as completed."""
        ...

    async def fail_job(self, job_id: str, error: str) -> None:
        """Mark job as failed."""
        ...

    async def list_recent(self, limit: int) -> list[Job]:
        """List recent jobs."""
        ...


class PostgreSQLBackend:
    """PostgreSQL backend using asyncpg for production."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._pool = None

    async def _get_pool(self):
        """Get or create connection pool."""
        if self._pool is None:
            try:
                import asyncpg
                self._pool = await asyncpg.create_pool(self.database_url)
            except ImportError:
                raise ImportError(
                    "asyncpg is required for PostgreSQL support. "
                    "Install with: uv add asyncpg"
                )
        return self._pool

    async def init_schema(self) -> None:
        """Create jobs table if not exists."""
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
                )
            ''')
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)'
            )
            await conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id)'
            )
        logger.info("PostgreSQL jobs table initialized")

    async def insert_job(self, job: Job) -> None:
        """Insert a new job."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', job.id, job.type, job.status, job.created_at,
                json.dumps(job.input) if job.input is not None else None, job.source)

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM jobs WHERE id = $1', job_id)
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        """Get currently running job if any."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT * FROM jobs
                WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            ''')
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict) -> None:
        """Update job progress and set to running."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET progress = $1, status = 'running',
                started_at = COALESCE(started_at, NOW()) WHERE id = $2
            ''', json.dumps(progress), job_id)

    async def complete_job(self, job_id: str, output: dict) -> None:
        """Mark job as completed with output."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET status = 'completed', completed_at = NOW(),
                output = $1 WHERE id = $2
            ''', json.dumps(output), job_id)

    async def fail_job(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute('''
                UPDATE jobs SET status = 'failed', completed_at = NOW(),
                error = $1 WHERE id = $2
            ''', error, job_id)

    async def list_recent(self, limit: int = 20) -> list[Job]:
        """List recent jobs."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1', limit
            )
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row) -> Job:
        """Convert database row to Job object."""
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
        """Get SQLite connection."""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def init_schema(self) -> None:
        """Create jobs table if not exists."""
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
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)'
            )
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)'
            )
        logger.info(f"SQLite jobs table initialized at {self.db_path}")

    async def insert_job(self, job: Job) -> None:
        """Insert a new job."""
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (job.id, job.type, job.status, job.created_at.isoformat(),
                  json.dumps(job.input) if job.input is not None else None, job.source))

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT * FROM jobs WHERE id = ?', (job_id,)
            ).fetchone()
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        """Get currently running job if any."""
        with self._get_conn() as conn:
            row = conn.execute('''
                SELECT * FROM jobs WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            ''').fetchone()
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict) -> None:
        """Update job progress and set to running."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET progress = ?, status = 'running',
                started_at = COALESCE(started_at, ?) WHERE id = ?
            ''', (json.dumps(progress), now, job_id))

    async def complete_job(self, job_id: str, output: dict) -> None:
        """Mark job as completed with output."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET status = 'completed', completed_at = ?, output = ?
                WHERE id = ?
            ''', (now, json.dumps(output), job_id))

    async def fail_job(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute('''
                UPDATE jobs SET status = 'failed', completed_at = ?, error = ?
                WHERE id = ?
            ''', (now, error, job_id))

    async def list_recent(self, limit: int = 20) -> list[Job]:
        """List recent jobs."""
        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,)
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row) -> Job:
        """Convert database row to Job object."""
        def parse_dt(s):
            if not s:
                return None
            if isinstance(s, datetime):
                return s
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
        self._initialized = False

    @classmethod
    def get_instance(cls) -> 'JobService':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def db_type(self) -> str:
        """Return the database type being used."""
        return self._db_type

    async def init(self) -> None:
        """Initialize database schema. Call on app startup."""
        if not self._initialized:
            await self._backend.init_schema()
            self._initialized = True
            logger.info(f"JobService initialized with {self._db_type} backend")

    async def create(
        self,
        job_type: str,
        params: dict,
        source: str = "local"
    ) -> Job:
        """Create a new job."""
        job = Job(
            id=str(uuid4()),
            type=job_type,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc),
            input=params,
            source=source
        )
        await self._backend.insert_job(job)
        logger.info(f"Created job {job.id} of type {job_type}")
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
        logger.info(f"Completed job {job_id}")

    async def fail(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        await self._backend.fail_job(job_id, error)
        logger.warning(f"Failed job {job_id}: {error}")

    async def list_recent(self, limit: int = 20) -> list[Job]:
        """List recent jobs."""
        return await self._backend.list_recent(limit)


def get_job_service() -> JobService:
    """Get the job service singleton."""
    return JobService.get_instance()
