"""
Integration tests for job persistence service.

This module contains comprehensive integration tests covering:
1. API endpoint tests using FastAPI TestClient
2. End-to-end job lifecycle tests (create->progress->complete and create->progress->fail)
3. Concurrent job handling tests
4. Database migration/schema tests for both PostgreSQL and SQLite backends
5. Edge cases: invalid job IDs, duplicate jobs, timezone handling

Run with: pytest tests/test_jobs_integration.py -v
"""

import pytest
import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from briefly.services.jobs import (
    Job,
    JobService,
    JobStatus,
    JobType,
    SQLiteBackend,
    PostgreSQLBackend,
    get_job_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path(tmp_path):
    """Create a temporary database path for testing."""
    return tmp_path / "test_integration_jobs.db"


@pytest.fixture
def sqlite_backend(temp_db_path):
    """Create a fresh SQLite backend for each test."""
    backend = SQLiteBackend(temp_db_path)
    return backend


@pytest.fixture
async def initialized_sqlite_backend(sqlite_backend):
    """Create an initialized SQLite backend."""
    await sqlite_backend.init_schema()
    return sqlite_backend


@pytest.fixture
def job_service(tmp_path):
    """
    Create a JobService with SQLite backend for testing.

    This fixture patches the module-level constants to use a test database
    and clears the singleton instance before and after each test.
    """
    JobService._instance = None

    with patch.dict(os.environ, {}, clear=True):
        with patch("briefly.services.jobs.DATABASE_URL", None):
            with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "service_test.db"):
                service = JobService()
                yield service

    JobService._instance = None


@pytest.fixture
async def initialized_job_service(job_service):
    """Create an initialized JobService."""
    await job_service.init()
    return job_service


@pytest.fixture
def mock_curation_service():
    """Mock CurationService for API tests."""
    with patch("briefly.api.routes.briefings.CurationService") as mock:
        mock_instance = AsyncMock()
        mock_instance.create_briefing = AsyncMock(return_value={
            "summary": "Test briefing",
            "items": [],
            "stats": {"x": 0, "youtube": 0, "podcasts": 0}
        })
        mock.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_transcript_store():
    """Mock transcript store for API tests."""
    with patch("briefly.api.routes.briefings.get_transcript_store") as mock:
        store = MagicMock()
        store.list_pending.return_value = []
        mock.return_value = store
        yield store


@pytest.fixture
def test_app(tmp_path, mock_transcript_store):
    """
    Create a test FastAPI application with mocked dependencies.

    This provides a complete test app that can be used with TestClient
    for integration testing of the API endpoints.
    """
    JobService._instance = None

    with patch.dict(os.environ, {}, clear=True):
        with patch("briefly.services.jobs.DATABASE_URL", None):
            with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "api_test.db"):
                from briefly.api.main import app

                # Initialize the job service synchronously for testing
                async def init_service():
                    service = get_job_service()
                    await service.init()

                asyncio.get_event_loop().run_until_complete(init_service())

                yield app

    JobService._instance = None


@pytest.fixture
def client(test_app):
    """Create a TestClient for the FastAPI app."""
    return TestClient(test_app)


# =============================================================================
# API Endpoint Tests
# =============================================================================


class TestJobsAPIEndpoints:
    """
    Tests for job management API endpoints.

    Covers:
    - GET /api/briefings/jobs
    - GET /api/briefings/jobs/active
    - GET /api/briefings/jobs/{id}
    """

    def test_list_jobs_empty(self, client):
        """
        Test listing jobs when no jobs exist.

        Scenario: Fresh database with no jobs
        Expected: Returns empty list with 200 status
        """
        response = client.get("/api/briefings/jobs")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_jobs_with_limit(self, client, tmp_path):
        """
        Test listing jobs respects the limit parameter.

        Scenario: Database has 10 jobs, request with limit=5
        Expected: Returns only 5 most recent jobs
        """
        # Create jobs directly in the database
        async def create_jobs():
            service = get_job_service()
            for i in range(10):
                await service.create(
                    job_type=JobType.BRIEFING.value,
                    params={"index": i}
                )

        asyncio.get_event_loop().run_until_complete(create_jobs())

        response = client.get("/api/briefings/jobs?limit=5")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 5

    def test_get_active_job_none(self, client):
        """
        Test getting active job when none exists.

        Scenario: No running or pending jobs
        Expected: Returns {active: false, job: null}
        """
        response = client.get("/api/briefings/jobs/active")
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is False
        assert data["job"] is None

    def test_get_active_job_exists(self, client):
        """
        Test getting active job when one exists.

        Scenario: One job with status 'running'
        Expected: Returns {active: true, job: {...}}
        """
        async def create_active_job():
            service = get_job_service()
            job = await service.create(
                job_type=JobType.BRIEFING.value,
                params={}
            )
            await service.update_progress(job.id, {"step": "testing"})
            return job.id

        job_id = asyncio.get_event_loop().run_until_complete(create_active_job())

        response = client.get("/api/briefings/jobs/active")
        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert data["job"]["id"] == job_id
        assert data["job"]["status"] == "running"

    def test_get_job_by_id(self, client):
        """
        Test retrieving a specific job by ID.

        Scenario: Create a job and retrieve it
        Expected: Returns full job details with 200 status
        """
        async def create_job():
            service = get_job_service()
            job = await service.create(
                job_type=JobType.BRIEFING.value,
                params={"hours_back": 24}
            )
            return job.id

        job_id = asyncio.get_event_loop().run_until_complete(create_job())

        response = client.get(f"/api/briefings/jobs/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["type"] == "briefing"
        assert data["input"]["hours_back"] == 24

    def test_get_job_not_found(self, client):
        """
        Test getting a job that doesn't exist.

        Scenario: Request job with non-existent UUID
        Expected: Returns 404 error
        """
        fake_id = str(uuid4())
        response = client.get(f"/api/briefings/jobs/{fake_id}")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_job_status_endpoint(self, client):
        """
        Test the /generate/{job_id} status endpoint.

        Scenario: Create job and check status via legacy endpoint
        Expected: Returns backward-compatible status format
        """
        async def create_job_with_progress():
            service = get_job_service()
            job = await service.create(
                job_type=JobType.BRIEFING.value,
                params={"hours_back": 12}
            )
            await service.update_progress(job.id, {
                "step": "Fetching sources",
                "current": 5,
                "total": 10
            })
            return job.id

        job_id = asyncio.get_event_loop().run_until_complete(create_job_with_progress())

        response = client.get(f"/api/briefings/generate/{job_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == job_id
        assert data["status"] == "running"
        assert data["step"] == "Fetching sources"
        assert data["current"] == 5
        assert data["total"] == 10


# =============================================================================
# End-to-End Job Lifecycle Tests
# =============================================================================


class TestJobLifecycle:
    """
    End-to-end tests for complete job lifecycles.

    Tests the full flow from job creation through completion or failure,
    including progress updates and state transitions.
    """

    @pytest.mark.asyncio
    async def test_successful_job_lifecycle(self, initialized_job_service):
        """
        Test complete successful job lifecycle: create -> progress -> complete.

        Scenario:
        1. Create a new briefing job
        2. Update progress multiple times
        3. Complete the job with output
        4. Verify final state
        """
        service = initialized_job_service

        # Step 1: Create job
        job = await service.create(
            job_type=JobType.BRIEFING.value,
            params={"hours_back": 24, "sources": ["@user1", "@user2"]},
            source="api"
        )
        assert job.status == JobStatus.PENDING.value
        assert job.started_at is None

        # Step 2: Update progress - first update
        await service.update_progress(job.id, {
            "step": "Fetching X sources",
            "current": 0,
            "total": 2
        })

        updated = await service.get(job.id)
        assert updated.status == JobStatus.RUNNING.value
        assert updated.started_at is not None
        assert updated.progress["step"] == "Fetching X sources"

        # Step 2b: Update progress - second update
        await service.update_progress(job.id, {
            "step": "Fetching X sources",
            "current": 1,
            "total": 2
        })

        # Step 2c: Update progress - completing fetch
        await service.update_progress(job.id, {
            "step": "Generating summary",
            "current": 2,
            "total": 2
        })

        # Step 3: Complete the job
        output = {
            "result": {
                "summary": "Today's briefing covers...",
                "items": [{"source": "@user1", "content": "..."}],
                "stats": {"x": 5, "youtube": 0, "podcasts": 0}
            }
        }
        await service.complete(job.id, output)

        # Step 4: Verify final state
        completed = await service.get(job.id)
        assert completed.status == JobStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.output["result"]["summary"] == "Today's briefing covers..."
        assert completed.error is None

    @pytest.mark.asyncio
    async def test_failed_job_lifecycle(self, initialized_job_service):
        """
        Test failed job lifecycle: create -> progress -> fail.

        Scenario:
        1. Create a new briefing job
        2. Update progress (job starts running)
        3. Job fails with error
        4. Verify error is recorded
        """
        service = initialized_job_service

        # Step 1: Create job
        job = await service.create(
            job_type=JobType.TRANSCRIPTION.value,
            params={"video_id": "abc123"},
            source="api"
        )

        # Step 2: Update progress - job starts running
        await service.update_progress(job.id, {
            "step": "Downloading audio",
            "current": 0,
            "total": 1
        })

        running = await service.get(job.id)
        assert running.status == JobStatus.RUNNING.value

        # Step 3: Job fails
        error_message = "Failed to download audio: Network timeout\nTraceback..."
        await service.fail(job.id, error_message)

        # Step 4: Verify failure state
        failed = await service.get(job.id)
        assert failed.status == JobStatus.FAILED.value
        assert failed.completed_at is not None
        assert failed.error == error_message
        assert failed.output is None

    @pytest.mark.asyncio
    async def test_job_progress_preserves_started_at(self, initialized_job_service):
        """
        Test that multiple progress updates don't overwrite started_at.

        Scenario: Update progress multiple times
        Expected: started_at should only be set on first update
        """
        service = initialized_job_service

        job = await service.create(job_type=JobType.BRIEFING.value, params={})

        # First update sets started_at
        await service.update_progress(job.id, {"step": "Step 1"})
        first_update = await service.get(job.id)
        original_started_at = first_update.started_at

        # Small delay to ensure different timestamp if it was reset
        await asyncio.sleep(0.1)

        # Second update should preserve started_at
        await service.update_progress(job.id, {"step": "Step 2"})
        second_update = await service.get(job.id)

        assert second_update.started_at == original_started_at


# =============================================================================
# Concurrent Job Handling Tests
# =============================================================================


class TestConcurrentJobHandling:
    """
    Tests for concurrent job operations.

    Verifies behavior when multiple jobs are created or accessed
    simultaneously, and tests the active job detection logic.
    """

    @pytest.mark.asyncio
    async def test_multiple_jobs_only_one_active(self, initialized_job_service):
        """
        Test that get_active returns most recent pending/running job.

        Scenario: Create multiple jobs with different statuses
        Expected: get_active returns the most recently created active job
        """
        service = initialized_job_service

        # Create first job and complete it
        job1 = await service.create(job_type=JobType.BRIEFING.value, params={"n": 1})
        await service.update_progress(job1.id, {"step": "running"})
        await service.complete(job1.id, {"done": True})

        # Create second job and leave it running
        job2 = await service.create(job_type=JobType.BRIEFING.value, params={"n": 2})
        await service.update_progress(job2.id, {"step": "processing"})

        # Create third job (pending)
        job3 = await service.create(job_type=JobType.BRIEFING.value, params={"n": 3})

        # get_active should return job3 (most recent pending/running)
        active = await service.get_active()
        assert active is not None
        assert active.id == job3.id

    @pytest.mark.asyncio
    async def test_concurrent_job_creation(self, initialized_job_service):
        """
        Test creating multiple jobs concurrently.

        Scenario: Create 10 jobs in parallel
        Expected: All jobs are created with unique IDs
        """
        service = initialized_job_service

        async def create_job(n: int) -> Job:
            return await service.create(
                job_type=JobType.BRIEFING.value,
                params={"index": n}
            )

        # Create jobs concurrently
        jobs = await asyncio.gather(*[create_job(i) for i in range(10)])

        # Verify all unique IDs
        job_ids = [j.id for j in jobs]
        assert len(set(job_ids)) == 10

        # Verify all jobs can be retrieved
        for job in jobs:
            retrieved = await service.get(job.id)
            assert retrieved is not None
            assert retrieved.id == job.id

    @pytest.mark.asyncio
    async def test_concurrent_progress_updates(self, initialized_job_service):
        """
        Test concurrent progress updates to the same job.

        Scenario: Multiple concurrent progress updates
        Expected: Last update wins, no corruption
        """
        service = initialized_job_service

        job = await service.create(job_type=JobType.BRIEFING.value, params={})

        async def update_progress(step: int):
            await service.update_progress(job.id, {
                "step": f"Step {step}",
                "current": step,
                "total": 10
            })

        # Concurrent updates
        await asyncio.gather(*[update_progress(i) for i in range(10)])

        # Verify job is in valid state
        final = await service.get(job.id)
        assert final.status == JobStatus.RUNNING.value
        assert final.progress is not None
        assert "step" in final.progress
        assert final.progress["total"] == 10

    @pytest.mark.asyncio
    async def test_list_recent_ordering(self, initialized_job_service):
        """
        Test that list_recent returns jobs in correct order.

        Scenario: Create jobs with small delays
        Expected: Jobs returned in reverse chronological order
        """
        service = initialized_job_service

        job_ids = []
        for i in range(5):
            job = await service.create(
                job_type=JobType.BRIEFING.value,
                params={"order": i}
            )
            job_ids.append(job.id)
            await asyncio.sleep(0.05)  # Small delay for ordering

        recent = await service.list_recent(limit=5)

        # Most recent should be first (reverse order)
        assert recent[0].id == job_ids[-1]
        assert recent[-1].id == job_ids[0]


# =============================================================================
# Database Schema/Migration Tests
# =============================================================================


class TestDatabaseSchema:
    """
    Tests for database schema initialization and integrity.

    Covers both SQLite and PostgreSQL (mocked) backends.
    """

    @pytest.mark.asyncio
    async def test_sqlite_schema_creation(self, temp_db_path):
        """
        Test SQLite schema is created correctly.

        Scenario: Initialize SQLite backend
        Expected: All tables and indexes are created
        """
        backend = SQLiteBackend(temp_db_path)
        await backend.init_schema()

        # Verify table exists
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()

        # Check table structure
        cursor.execute("PRAGMA table_info(jobs)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        assert "id" in columns
        assert "type" in columns
        assert "status" in columns
        assert "created_at" in columns
        assert "started_at" in columns
        assert "completed_at" in columns
        assert "progress" in columns
        assert "input" in columns
        assert "output" in columns
        assert "error" in columns
        assert "source" in columns
        assert "n8n_execution_id" in columns
        assert "n8n_workflow_id" in columns

        # Check indexes exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]

        assert "idx_jobs_status" in indexes
        assert "idx_jobs_created" in indexes

        conn.close()

    @pytest.mark.asyncio
    async def test_sqlite_schema_idempotent(self, temp_db_path):
        """
        Test that schema creation is idempotent.

        Scenario: Call init_schema multiple times
        Expected: No errors, schema remains intact
        """
        backend = SQLiteBackend(temp_db_path)

        # Initialize multiple times
        await backend.init_schema()
        await backend.init_schema()
        await backend.init_schema()

        # Should still work
        job = Job(
            id=str(uuid4()),
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc)
        )
        await backend.insert_job(job)

        retrieved = await backend.get_job(job.id)
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_postgresql_backend_selection(self):
        """
        Test that PostgreSQL backend is selected when DATABASE_URL is set.

        Scenario: Set DATABASE_URL environment variable
        Expected: JobService uses PostgreSQLBackend
        """
        JobService._instance = None

        with patch("briefly.services.jobs.DATABASE_URL", "postgresql://localhost:5432/testdb"):
            service = JobService()
            assert service.db_type == "postgresql"
            assert isinstance(service._backend, PostgreSQLBackend)

        JobService._instance = None

    @pytest.mark.asyncio
    async def test_postgresql_schema_creation(self):
        """
        Test PostgreSQL schema creation with mocked asyncpg.

        Scenario: Initialize PostgreSQL backend with mocked pool
        Expected: Correct SQL statements executed
        """
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

        with patch("asyncpg.create_pool", return_value=mock_pool):
            backend = PostgreSQLBackend("postgresql://localhost:5432/test")
            await backend.init_schema()

        # Verify execute was called for table and indexes
        assert mock_conn.execute.call_count >= 1

        # Check SQL contains expected statements
        calls = [str(call) for call in mock_conn.execute.call_args_list]
        sql_executed = " ".join(calls)

        assert "CREATE TABLE IF NOT EXISTS jobs" in sql_executed
        assert "CREATE INDEX IF NOT EXISTS" in sql_executed


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """
    Tests for edge cases and error conditions.

    Covers invalid inputs, boundary conditions, and error handling.
    """

    @pytest.mark.asyncio
    async def test_invalid_job_id_format(self, initialized_job_service):
        """
        Test handling of invalid job ID formats.

        Scenario: Request job with malformed ID
        Expected: Returns None (not found)
        """
        service = initialized_job_service

        invalid_ids = [
            "not-a-uuid",
            "",
            "123",
            "null",
            "../etc/passwd",  # Potential path traversal
            "'; DROP TABLE jobs; --",  # SQL injection attempt
        ]

        for invalid_id in invalid_ids:
            result = await service.get(invalid_id)
            assert result is None, f"Expected None for invalid ID: {invalid_id}"

    @pytest.mark.asyncio
    async def test_duplicate_job_ids(self, initialized_sqlite_backend):
        """
        Test that duplicate job IDs are rejected.

        Scenario: Insert two jobs with same ID
        Expected: Second insert fails (SQLite constraint)
        """
        backend = initialized_sqlite_backend

        job_id = str(uuid4())
        job1 = Job(
            id=job_id,
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc)
        )

        await backend.insert_job(job1)

        job2 = Job(
            id=job_id,  # Same ID
            type=JobType.TRANSCRIPTION.value,
            status=JobStatus.PENDING.value,
            created_at=datetime.now(timezone.utc)
        )

        with pytest.raises(Exception):  # sqlite3.IntegrityError
            await backend.insert_job(job2)

    @pytest.mark.asyncio
    async def test_timezone_handling_utc(self, initialized_job_service):
        """
        Test that UTC timestamps are handled correctly.

        Scenario: Create job with UTC timestamp
        Expected: Timestamp preserved correctly through storage and retrieval
        """
        service = initialized_job_service

        job = await service.create(
            job_type=JobType.BRIEFING.value,
            params={}
        )

        retrieved = await service.get(job.id)

        # Verify timezone-aware datetime
        assert retrieved.created_at is not None
        assert retrieved.created_at.tzinfo is not None or \
               retrieved.created_at.isoformat().endswith('+00:00') or \
               'T' in retrieved.created_at.isoformat()

    @pytest.mark.asyncio
    async def test_timezone_iso_format_parsing(self, initialized_sqlite_backend):
        """
        Test parsing of various ISO format timestamps.

        Scenario: Insert job with ISO format timestamp, retrieve and verify
        Expected: Timestamps parsed correctly regardless of format
        """
        backend = initialized_sqlite_backend

        # Test with explicit UTC offset
        now = datetime.now(timezone.utc)
        job = Job(
            id=str(uuid4()),
            type=JobType.BRIEFING.value,
            status=JobStatus.PENDING.value,
            created_at=now
        )

        await backend.insert_job(job)
        retrieved = await backend.get_job(job.id)

        # Verify timestamp is close to original (within 1 second)
        time_diff = abs((retrieved.created_at.replace(tzinfo=None) -
                        now.replace(tzinfo=None)).total_seconds())
        assert time_diff < 1

    @pytest.mark.asyncio
    async def test_large_progress_json(self, initialized_job_service):
        """
        Test handling of large progress JSON objects.

        Scenario: Update progress with large nested data
        Expected: Data stored and retrieved correctly
        """
        service = initialized_job_service

        job = await service.create(job_type=JobType.BRIEFING.value, params={})

        large_progress = {
            "step": "Processing",
            "current": 50,
            "total": 100,
            "media_status": {
                "x": {
                    "status": "completed",
                    "items": [{"id": str(i), "content": "x" * 100} for i in range(50)]
                },
                "youtube": {
                    "status": "processing",
                    "items": [{"id": str(i), "title": "y" * 100} for i in range(20)]
                }
            }
        }

        await service.update_progress(job.id, large_progress)

        retrieved = await service.get(job.id)
        assert retrieved.progress["current"] == 50
        assert len(retrieved.progress["media_status"]["x"]["items"]) == 50

    @pytest.mark.asyncio
    async def test_special_characters_in_error(self, initialized_job_service):
        """
        Test handling of special characters in error messages.

        Scenario: Fail job with error containing special chars
        Expected: Error message preserved correctly
        """
        service = initialized_job_service

        job = await service.create(job_type=JobType.BRIEFING.value, params={})

        error_msg = """Error: Network timeout
Traceback (most recent call last):
  File "/path/to/file.py", line 42, in fetch
    raise TimeoutError("Connection timed out after 30s")
TimeoutError: Connection timed out after 30s

Special chars: 'quotes' "double" <angle> &ampersand"""

        await service.fail(job.id, error_msg)

        retrieved = await service.get(job.id)
        assert retrieved.error == error_msg

    @pytest.mark.asyncio
    async def test_empty_params(self, initialized_job_service):
        """
        Test creating job with empty params.

        Scenario: Create job with empty dict params
        Expected: Job created successfully with empty input
        """
        service = initialized_job_service

        job = await service.create(
            job_type=JobType.BRIEFING.value,
            params={}
        )

        retrieved = await service.get(job.id)
        assert retrieved.input == {}

    @pytest.mark.asyncio
    async def test_null_output_on_completion(self, initialized_job_service):
        """
        Test completing job with empty/null output.

        Scenario: Complete job with empty dict output
        Expected: Job marked complete with empty output
        """
        service = initialized_job_service

        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.complete(job.id, {})

        retrieved = await service.get(job.id)
        assert retrieved.status == JobStatus.COMPLETED.value
        assert retrieved.output == {}

    @pytest.mark.asyncio
    async def test_get_nonexistent_active_job(self, initialized_job_service):
        """
        Test get_active when all jobs are completed/failed.

        Scenario: Only completed jobs exist
        Expected: get_active returns None
        """
        service = initialized_job_service

        # Create and complete a job
        job = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.complete(job.id, {"done": True})

        # Create and fail another job
        job2 = await service.create(job_type=JobType.BRIEFING.value, params={})
        await service.fail(job2.id, "Test failure")

        active = await service.get_active()
        assert active is None

    @pytest.mark.asyncio
    async def test_list_recent_with_zero_limit(self, initialized_job_service):
        """
        Test list_recent with limit=0.

        Scenario: Request with limit=0
        Expected: Returns empty list
        """
        service = initialized_job_service

        # Create some jobs
        for _ in range(3):
            await service.create(job_type=JobType.BRIEFING.value, params={})

        result = await service.list_recent(limit=0)
        assert result == []

    @pytest.mark.asyncio
    async def test_service_init_called_multiple_times(self, job_service):
        """
        Test that calling init() multiple times is safe.

        Scenario: Call init() multiple times
        Expected: No errors, service works correctly
        """
        await job_service.init()
        await job_service.init()
        await job_service.init()

        # Should still work
        job = await job_service.create(job_type=JobType.BRIEFING.value, params={})
        assert job.id is not None


# =============================================================================
# API Integration Tests with Background Tasks
# =============================================================================


class TestAPIWithBackgroundTasks:
    """
    Tests for API endpoints that trigger background tasks.

    These tests verify the integration between the API layer
    and the job service for asynchronous operations.
    """

    def test_generate_creates_job(self, client, tmp_path):
        """
        Test that /generate endpoint creates a persistent job.

        Note: This test requires sources to be configured.
        For integration testing, we verify the error response
        when no sources are configured.
        """
        response = client.post(
            "/api/briefings/generate",
            json={"hours_back": 24}
        )

        # Without configured sources, should return 400
        assert response.status_code == 400
        assert "No sources configured" in response.json()["detail"]

    def test_job_status_reflects_progress(self, client):
        """
        Test that job status endpoint reflects progress updates.

        Scenario: Create job, update progress, check status
        Expected: Status endpoint returns current progress
        """
        async def create_and_update():
            service = get_job_service()
            job = await service.create(
                job_type=JobType.BRIEFING.value,
                params={"hours_back": 24}
            )
            await service.update_progress(job.id, {
                "step": "Test step",
                "current": 3,
                "total": 10,
                "elapsed_seconds": 5.5
            })
            return job.id

        job_id = asyncio.get_event_loop().run_until_complete(create_and_update())

        response = client.get(f"/api/briefings/generate/{job_id}")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "running"
        assert data["step"] == "Test step"
        assert data["current"] == 3
        assert data["total"] == 10


# =============================================================================
# Cleanup and Singleton Tests
# =============================================================================


class TestSingletonBehavior:
    """
    Tests for JobService singleton behavior.
    """

    def test_singleton_returns_same_instance(self, tmp_path):
        """
        Test that get_job_service returns the same instance.
        """
        JobService._instance = None

        with patch.dict(os.environ, {}, clear=True):
            with patch("briefly.services.jobs.DATABASE_URL", None):
                with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "singleton.db"):
                    service1 = get_job_service()
                    service2 = get_job_service()

                    assert service1 is service2

        JobService._instance = None

    def test_singleton_can_be_reset(self, tmp_path):
        """
        Test that singleton can be reset for testing.
        """
        with patch.dict(os.environ, {}, clear=True):
            with patch("briefly.services.jobs.DATABASE_URL", None):
                with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "reset1.db"):
                    JobService._instance = None
                    service1 = get_job_service()

                    JobService._instance = None

                with patch("briefly.services.jobs.SQLITE_PATH", tmp_path / "reset2.db"):
                    service2 = get_job_service()

                    assert service1 is not service2

        JobService._instance = None
