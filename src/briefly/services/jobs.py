"""
Job persistence service with PostgreSQL (prod) and SQLite (dev) support.

Database selection:
- If DATABASE_URL env var is set → PostgreSQL (async with asyncpg)
- Otherwise → SQLite (sync, file-based at .cache/jobs.db)

Usage:
    service = JobService()
    await service.init()  # Call once on startup

    job = await service.create("briefing", {"hours_back": 24})
    await service.update_progress(job.id, {"step": "fetching"})
    await service.complete(job.id, {"items": 45})
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import uuid4


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
    """Represents a job in the system."""

    id: str
    type: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    n8n_execution_id: Optional[str] = None
    n8n_workflow_id: Optional[str] = None
    progress: Optional[dict[str, Any]] = None
    input: Optional[dict[str, Any]] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    source: str = "local"

    def to_dict(self) -> dict[str, Any]:
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
    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None: ...
    async def update_status(self, job_id: str, status: str) -> None: ...
    async def complete_job(self, job_id: str, output: dict[str, Any]) -> None: ...
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
            await conn.execute("""
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
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(type, status)"
            )

    async def insert_job(self, job: Job) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES ($1::uuid, $2, $3, $4, $5, $6)
                """,
                job.id,
                job.type,
                job.status,
                job.created_at,
                json.dumps(job.input) if job.input else None,
                job.source,
            )

    async def get_job(self, job_id: str) -> Optional[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM jobs WHERE id = $1::uuid", job_id
            )
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT * FROM jobs
                WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            """)
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs SET progress = $1, status = 'running',
                started_at = COALESCE(started_at, NOW()) WHERE id = $2::uuid
                """,
                json.dumps(progress),
                job_id,
            )

    async def update_status(self, job_id: str, status: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET status = $1 WHERE id = $2::uuid",
                status,
                job_id,
            )

    async def complete_job(self, job_id: str, output: dict[str, Any]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs SET status = 'completed', completed_at = NOW(),
                output = $1 WHERE id = $2::uuid
                """,
                json.dumps(output),
                job_id,
            )

    async def fail_job(self, job_id: str, error: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE jobs SET status = 'failed', completed_at = NOW(),
                error = $1 WHERE id = $2::uuid
                """,
                error,
                job_id,
            )

    async def list_recent(self, limit: int = 20) -> list[Job]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT $1", limit
            )
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row) -> Job:
        return Job(
            id=str(row["id"]),
            type=row["type"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            n8n_execution_id=row["n8n_execution_id"],
            n8n_workflow_id=row["n8n_workflow_id"],
            progress=row["progress"],
            input=row["input"],
            output=row["output"],
            error=row["error"],
            source=row["source"] or "local",
        )


class SQLiteBackend:
    """SQLite backend for development and testing."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
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
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_n8n ON jobs(n8n_execution_id)"
            )

    async def insert_job(self, job: Job) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, type, status, created_at, input, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.type,
                    job.status,
                    job.created_at.isoformat(),
                    json.dumps(job.input) if job.input else None,
                    job.source,
                ),
            )

    async def get_job(self, job_id: str) -> Optional[Job]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._row_to_job(row) if row else None

    async def get_active_job(self) -> Optional[Job]:
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM jobs WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
            return self._row_to_job(row) if row else None

    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET progress = ?, status = 'running',
                started_at = COALESCE(started_at, ?) WHERE id = ?
                """,
                (
                    json.dumps(progress),
                    datetime.now(timezone.utc).isoformat(),
                    job_id,
                ),
            )

    async def update_status(self, job_id: str, status: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET status = ? WHERE id = ?",
                (status, job_id),
            )

    async def complete_job(self, job_id: str, output: dict[str, Any]) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = 'completed', completed_at = ?, output = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(output),
                    job_id,
                ),
            )

    async def fail_job(self, job_id: str, error: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = 'failed', completed_at = ?, error = ?
                WHERE id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), error, job_id),
            )

    async def list_recent(self, limit: int = 20) -> list[Job]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        def parse_dt(s: str | datetime | None) -> Optional[datetime]:
            if not s:
                return None
            if isinstance(s, datetime):
                return s
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        return Job(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            created_at=parse_dt(row["created_at"]),
            started_at=parse_dt(row["started_at"]),
            completed_at=parse_dt(row["completed_at"]),
            n8n_execution_id=row["n8n_execution_id"],
            n8n_workflow_id=row["n8n_workflow_id"],
            progress=json.loads(row["progress"]) if row["progress"] else None,
            input=json.loads(row["input"]) if row["input"] else None,
            output=json.loads(row["output"]) if row["output"] else None,
            error=row["error"],
            source=row["source"] or "local",
        )


# Environment-based DB selection
DATABASE_URL = os.getenv("DATABASE_URL")
SQLITE_PATH = Path(__file__).parent.parent.parent.parent / ".cache" / "jobs.db"


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

    _instance: Optional[JobService] = None

    def __init__(self, database_url: Optional[str] = None):
        url = database_url or DATABASE_URL
        if url:
            self._backend: DatabaseBackend = PostgreSQLBackend(url)
            self._db_type = "postgresql"
        else:
            self._backend = SQLiteBackend(SQLITE_PATH)
            self._db_type = "sqlite"

    @classmethod
    def get_instance(cls) -> JobService:
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (for testing)."""
        cls._instance = None

    @property
    def db_type(self) -> str:
        return self._db_type

    async def init(self) -> None:
        """Initialize database schema. Call on app startup."""
        await self._backend.init_schema()

    async def create(
        self,
        job_type: str,
        params: Optional[dict[str, Any]] = None,
        source: str = "local",
    ) -> Job:
        """Create a new job."""
        job = Job(
            id=str(uuid4()),
            type=job_type,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc),
            input=params,
            source=source,
        )
        await self._backend.insert_job(job)
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        return await self._backend.get_job(job_id)

    async def get_active(self) -> Optional[Job]:
        """Get currently running job if any."""
        return await self._backend.get_active_job()

    async def update_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        """Update job progress."""
        await self._backend.update_progress(job_id, progress)

    async def update_status(self, job_id: str, status: str) -> None:
        """Update job status."""
        await self._backend.update_status(job_id, status)

    async def complete(self, job_id: str, output: dict[str, Any]) -> None:
        """Mark job as completed with output."""
        await self._backend.complete_job(job_id, output)

    async def fail(self, job_id: str, error: str) -> None:
        """Mark job as failed with error message."""
        await self._backend.fail_job(job_id, error)

    async def list_recent(self, limit: int = 20) -> list[Job]:
        """List recent jobs."""
        return await self._backend.list_recent(limit)


def get_job_service() -> JobService:
    """Get the job service singleton."""
    return JobService.get_instance()
