"""Tests for job persistence service."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import json
import os

from briefly.services.jobs import (
    Job,
    JobService,
    JobStatus,
    JobType,
    SQLiteBackend,
    PostgreSQLBackend,
    get_job_service,
)


class TestJob:
    """Tests for Job dataclass."""

    def test_job_creation(self):
        """Test creating a Job instance."""
        now = datetime.now(timezone.utc)
        job = Job(
            id="test-123",
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=now,
        )
        assert job.id == "test-123"
        assert job.type == "briefing"
        assert job.status == "pending"
        assert job.created_at == now

    def test_job_to_dict(self):
        """Test Job.to_dict() serialization."""
        now = datetime.now(timezone.utc)
        job = Job(
            id="test-456",
            type=JobType.TRANSCRIPTION.value,
            status=JobStatus.RUNNING.value,
            created_at=now,
            started_at=now,
            progress={"step": "processing", "current": 5, "total": 10},
            input={"hours_back": 24},
        )
        result = job.to_dict()

        assert result["id"] == "test-456"
        assert result["type"] == "transcription"
        assert result["status"] == "running"
        assert result["created_at"] == now.isoformat()
        assert result["started_at"] == now.isoformat()
        assert result["progress"] == {"step": "processing", "current": 5, "total": 10}
        assert result["input"] == {"hours_back": 24}

    def test_job_to_dict_with_null_dates(self):
        """Test Job.to_dict() with null optional dates."""
        now = datetime.now(timezone.utc)
        job = Job(
            id="test-789",
            type=JobType.EXTRACTION.value,
            status=JobStatus.PENDING.value,
            created_at=now,
        )
        result = job.to_dict()

        assert result["started_at"] is None
        assert result["completed_at"] is None


class TestJobStatus:
    """Tests for JobStatus enum."""

    def test_status_values(self):
        """Test all status values exist."""
        assert JobStatus.PENDING.value == "pending"
        assert JobStatus.RUNNING.value == "running"
        assert JobStatus.COMPLETED.value == "completed"
        assert JobStatus.FAILED.value == "failed"
        assert JobStatus.CANCELLED.value == "cancelled"


class TestJobType:
    """Tests for JobType enum."""

    def test_type_values(self):
        """Test all type values exist."""
        assert JobType.BRIEFING.value == "briefing"
        assert JobType.TRANSCRIPTION.value == "transcription"
        assert JobType.EXTRACTION.value == "extraction"


class TestSQLiteBackend:
    """Tests for SQLite backend."""

    @pytest.fixture
    def backend(self, tmp_path):
        """Create SQLite backend with temp path."""
        db_path = tmp_path / "test_jobs.db"
        return SQLiteBackend(db_path)

    @pytest.mark.asyncio
    async def test_init_schema(self, backend):
        """Test schema initialization."""
        await backend.init_schema()
        # Should not raise

    @pytest.mark.asyncio
    async def test_insert_and_get_job(self, backend):
        """Test inserting and retrieving a job."""
        await backend.init_schema()

        now = datetime.now(timezone.utc)
        job = Job(
            id="sqlite-test-1",
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=now,
            input={"test": "data"},
        )
        await backend.insert_job(job)

        retrieved = await backend.get_job("sqlite-test-1")
        assert retrieved is not None
        assert retrieved.id == "sqlite-test-1"
        assert retrieved.type == "briefing"
        assert retrieved.status == "pending"
        assert retrieved.input == {"test": "data"}

    @pytest.mark.asyncio
    async def test_get_nonexistent_job(self, backend):
        """Test getting a job that doesn't exist."""
        await backend.init_schema()
        result = await backend.get_job("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_progress(self, backend):
        """Test updating job progress."""
        await backend.init_schema()

        job = Job(
            id="progress-test",
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc),
        )
        await backend.insert_job(job)

        await backend.update_progress("progress-test", {"step": "fetching", "current": 3, "total": 10})

        updated = await backend.get_job("progress-test")
        assert updated.status == "running"
        assert updated.started_at is not None
        assert updated.progress == {"step": "fetching", "current": 3, "total": 10}

    @pytest.mark.asyncio
    async def test_complete_job(self, backend):
        """Test completing a job."""
        await backend.init_schema()

        job = Job(
            id="complete-test",
            type=JobType.BRIEFING.value,
            status=JobStatus.RUNNING.value,
            created_at=datetime.now(timezone.utc),
        )
        await backend.insert_job(job)

        await backend.complete_job("complete-test", {"items": 42})

        completed = await backend.get_job("complete-test")
        assert completed.status == "completed"
        assert completed.completed_at is not None
        assert completed.output == {"items": 42}

    @pytest.mark.asyncio
    async def test_fail_job(self, backend):
        """Test failing a job."""
        await backend.init_schema()

        job = Job(
            id="fail-test",
            type=JobType.BRIEFING.value,
            status=JobStatus.RUNNING.value,
            created_at=datetime.now(timezone.utc),
        )
        await backend.insert_job(job)

        await backend.fail_job("fail-test", "Something went wrong")

        failed = await backend.get_job("fail-test")
        assert failed.status == "failed"
        assert failed.completed_at is not None
        assert failed.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_list_recent(self, backend):
        """Test listing recent jobs."""
        await backend.init_schema()

        # Create 5 jobs
        for i in range(5):
            job = Job(
                id=f"list-test-{i}",
                type=JobType.BRIEFING.value,
                status=JobStatus.COMPLETED.value,
                created_at=datetime.now(timezone.utc),
            )
            await backend.insert_job(job)

        jobs = await backend.list_recent(limit=3)
        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_get_active_job(self, backend):
        """Test getting active job."""
        await backend.init_schema()

        # Create a running job
        job = Job(
            id="active-test",
            type=JobType.BRIEFING.value,
            status=JobStatus.RUNNING.value,
            created_at=datetime.now(timezone.utc),
        )
        await backend.insert_job(job)

        active = await backend.get_active_job()
        assert active is not None
        assert active.id == "active-test"

    @pytest.mark.asyncio
    async def test_get_active_job_none(self, backend):
        """Test getting active job when none exists."""
        await backend.init_schema()

        # Create only completed jobs
        job = Job(
            id="completed-only",
            type=JobType.BRIEFING.value,
            status=JobStatus.COMPLETED.value,
            created_at=datetime.now(timezone.utc),
        )
        await backend.insert_job(job)

        active = await backend.get_active_job()
        assert active is None


class TestJobService:
    """Tests for JobService."""

    @pytest.fixture
    def service(self, tmp_path):
        """Create JobService with SQLite backend for testing."""
        # Clear singleton
        JobService._instance = None

        # Patch environment to use SQLite
        with patch.dict(os.environ, {}, clear=True):
            with patch("briefly.services.jobs.DATABASE_URL", None):
                with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "test.db"):
                    service = JobService()
                    yield service

        # Clean up singleton
        JobService._instance = None

    @pytest.mark.asyncio
    async def test_service_init(self, service):
        """Test service initialization."""
        await service.init()
        assert service._initialized is True

    @pytest.mark.asyncio
    async def test_service_create_job(self, service):
        """Test creating a job through service."""
        await service.init()

        job = await service.create(
            job_type=JobType.BRIEFING.value,
            params={"hours_back": 24},
            source="test",
        )

        assert job.id is not None
        assert job.type == "briefing"
        assert job.status == "pending"
        assert job.input == {"hours_back": 24}
        assert job.source == "test"

    @pytest.mark.asyncio
    async def test_service_get_job(self, service):
        """Test getting a job through service."""
        await service.init()

        created = await service.create(
            job_type=JobType.BRIEFING.value,
            params={},
        )

        retrieved = await service.get(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id

    @pytest.mark.asyncio
    async def test_service_update_progress(self, service):
        """Test updating progress through service."""
        await service.init()

        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.update_progress(job.id, {"step": "processing"})

        updated = await service.get(job.id)
        assert updated.status == "running"
        assert updated.progress == {"step": "processing"}

    @pytest.mark.asyncio
    async def test_service_complete(self, service):
        """Test completing a job through service."""
        await service.init()

        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.complete(job.id, {"result": "success"})

        completed = await service.get(job.id)
        assert completed.status == "completed"
        assert completed.output == {"result": "success"}

    @pytest.mark.asyncio
    async def test_service_fail(self, service):
        """Test failing a job through service."""
        await service.init()

        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.fail(job.id, "Error occurred")

        failed = await service.get(job.id)
        assert failed.status == "failed"
        assert failed.error == "Error occurred"

    @pytest.mark.asyncio
    async def test_service_list_recent(self, service):
        """Test listing recent jobs through service."""
        await service.init()

        # Create a few jobs
        for _ in range(3):
            await service.create(job_type=JobType.BRIEFING.value, params={})

        jobs = await service.list_recent(limit=10)
        assert len(jobs) >= 3

    @pytest.mark.asyncio
    async def test_service_get_active(self, service):
        """Test getting active job through service."""
        await service.init()

        # Create and start a job
        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.update_progress(job.id, {"step": "starting"})

        active = await service.get_active()
        assert active is not None
        assert active.id == job.id

    def test_service_db_type_sqlite(self, service):
        """Test that SQLite is selected when no DATABASE_URL."""
        assert service.db_type == "sqlite"


class TestJobServiceBackendSelection:
    """Tests for backend selection logic."""

    def test_selects_postgresql_when_database_url_set(self):
        """Test PostgreSQL backend is selected with DATABASE_URL."""
        JobService._instance = None

        with patch("briefly.services.jobs.DATABASE_URL", "postgresql://localhost/test"):
            service = JobService()
            assert service.db_type == "postgresql"

        JobService._instance = None

    def test_selects_sqlite_when_no_database_url(self):
        """Test SQLite backend is selected without DATABASE_URL."""
        JobService._instance = None

        with patch("briefly.services.jobs.DATABASE_URL", None):
            service = JobService()
            assert service.db_type == "sqlite"

        JobService._instance = None


class TestGetJobService:
    """Tests for get_job_service singleton."""

    def test_returns_singleton(self):
        """Test that get_job_service returns the same instance."""
        JobService._instance = None

        with patch("briefly.services.jobs.DATABASE_URL", None):
            service1 = get_job_service()
            service2 = get_job_service()
            assert service1 is service2

        JobService._instance = None
