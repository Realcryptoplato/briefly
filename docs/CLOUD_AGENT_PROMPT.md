# Cloud Agent Implementation Prompt

## One-Liner for Claude Cloud Agent

```
Implement the job persistence system from docs/PRD-n8n-job-persistence.md. Create src/briefly/services/jobs.py with JobService class supporting PostgreSQL (prod via DATABASE_URL) and SQLite (dev fallback). Add Job dataclass, PostgreSQLBackend using asyncpg, SQLiteBackend using sqlite3. Update src/briefly/api/routes/briefings.py to use JobService instead of in-memory _jobs dict. Add new endpoints: GET /api/jobs/{id}, GET /api/jobs/active, GET /api/jobs (list). Add asyncpg to pyproject.toml optional deps. Create tests/test_jobs.py with mocked backends. DO NOT attempt to run tests or start servers - generate code only and explain what manual testing is needed.
```

---

## Detailed Prompt (for complex implementations)

```markdown
# Task: Implement Job Persistence System for Briefly 3000

## Context
You are a Claude Cloud Agent implementing the job persistence system described in `docs/PRD-n8n-job-persistence.md`. You CANNOT execute code, run tests, or start servers. Focus on code generation with comprehensive documentation.

## Your Limitations (IMPORTANT)
1. NO local execution - cannot run pytest, uv, pip, or any commands
2. NO file system verification - cannot check if files were created correctly
3. NO network access - cannot test PostgreSQL or SQLite connections
4. NO environment variables - cannot simulate DATABASE_URL switching

## What You CAN Do
1. Read existing files to understand the codebase structure
2. Write new Python files with correct syntax and types
3. Modify existing files (routes, config, etc.)
4. Generate test files that will be run later by humans/CI
5. Update pyproject.toml with new dependencies
6. Write documentation explaining manual verification steps

## Implementation Checklist

### 1. Create `src/briefly/services/jobs.py`
- [ ] Import statements (pathlib, json, os, datetime, uuid, dataclasses, typing, enum)
- [ ] JobStatus enum (pending, running, completed, failed, cancelled)
- [ ] JobType enum (briefing, transcription, extraction)
- [ ] Job dataclass with all fields from PRD
- [ ] DatabaseBackend Protocol
- [ ] PostgreSQLBackend class using asyncpg
- [ ] SQLiteBackend class using sqlite3
- [ ] JobService class with environment-based backend selection
- [ ] get_job_service() convenience function

### 2. Update `src/briefly/api/routes/briefings.py`
- [ ] Import JobService
- [ ] Replace `_jobs: dict[str, dict] = {}` with JobService
- [ ] Update generate_briefing() to use JobService.create()
- [ ] Update progress callback to use JobService.update_progress()
- [ ] Add GET /api/jobs/{job_id} endpoint
- [ ] Add GET /api/jobs/active endpoint
- [ ] Add GET /api/jobs endpoint (list recent)

### 3. Update `src/briefly/api/main.py`
- [ ] Import JobService
- [ ] Add `await job_service.init()` to lifespan startup

### 4. Update `pyproject.toml`
- [ ] Add `asyncpg>=0.29.0` to optional dependencies under `[project.optional-dependencies]`
- [ ] Create `postgres` extras group

### 5. Create `tests/test_jobs.py`
- [ ] Import pytest, unittest.mock
- [ ] Mock fixtures for backends
- [ ] Test JobService backend selection
- [ ] Test CRUD operations with mocks
- [ ] Test Job.to_dict() serialization

## Code Quality Requirements
- Type hints on ALL function signatures
- Docstrings on ALL public methods
- Async/await used correctly (SQLite backend wraps sync in async interface)
- No hardcoded credentials or paths (use env vars and Path)
- Error handling with meaningful messages

## Output Format
For each file, provide:
1. Full file path
2. Complete file contents (not diffs)
3. Explanation of key design decisions
4. Manual verification steps

## Manual Verification (for human after implementation)
After you generate the code, a human will need to:
1. `uv sync` to install dependencies
2. Set `DATABASE_URL` env var for PostgreSQL testing
3. Run `pytest tests/test_jobs.py -v`
4. Start server with `uv run uvicorn briefly.api.main:app`
5. Test endpoints with curl/httpie
6. Verify SQLite file created at `.cache/jobs.db` when no DATABASE_URL
```

---

## Quick Reference: File Locations

| Component | Path |
|-----------|------|
| Job Service | `src/briefly/services/jobs.py` |
| Briefings Route | `src/briefly/api/routes/briefings.py` |
| Main App | `src/briefly/api/main.py` |
| Config | `src/briefly/core/config.py` |
| Dependencies | `pyproject.toml` |
| Tests | `tests/test_jobs.py` |
| PRD Reference | `docs/PRD-n8n-job-persistence.md` |

---

## Expected Deliverables

1. **`src/briefly/services/jobs.py`** (~300 lines)
   - Complete JobService implementation
   - Both PostgreSQL and SQLite backends

2. **`src/briefly/api/routes/briefings.py`** (modified)
   - New job endpoints
   - JobService integration

3. **`src/briefly/api/main.py`** (modified)
   - JobService initialization on startup

4. **`pyproject.toml`** (modified)
   - asyncpg optional dependency

5. **`tests/test_jobs.py`** (~150 lines)
   - Unit tests with mocked backends

6. **Summary document** explaining:
   - What was implemented
   - What needs manual testing
   - Known limitations or TODOs

---

## Tester Agent One-Liner

```
Review the job persistence implementation on branch feature/job-persistence. Read src/briefly/services/jobs.py and tests/test_jobs.py. Create additional integration tests in tests/test_jobs_integration.py covering: (1) API endpoint tests using FastAPI TestClient for /api/briefings/jobs, /api/briefings/jobs/active, /api/briefings/jobs/{id}, (2) End-to-end job lifecycle tests (create->progress->complete and create->progress->fail), (3) Concurrent job handling tests, (4) Database migration/schema tests for both PostgreSQL and SQLite backends, (5) Edge cases: invalid job IDs, duplicate jobs, timezone handling. Use pytest fixtures, mock external dependencies. DO NOT run tests - generate code only. Output test file with clear docstrings explaining each test scenario.
```

---

## Tester Agent Detailed Prompt

```markdown
# Task: Create Integration Test Suite for Job Persistence

## Context
You are reviewing the job persistence implementation on branch `feature/job-persistence`. The implementation includes:
- `src/briefly/services/jobs.py` - JobService with PostgreSQL/SQLite backends
- `tests/test_jobs.py` - Existing unit tests (26 tests)
- Updated `src/briefly/api/routes/briefings.py` with new job endpoints

## Your Task
Create `tests/test_jobs_integration.py` with comprehensive integration tests.

## Test Categories Required

### 1. API Endpoint Tests (using FastAPI TestClient)
- GET /api/briefings/jobs - returns list of jobs
- GET /api/briefings/jobs/active - returns active job or null
- GET /api/briefings/jobs/{id} - returns specific job
- GET /api/briefings/generate/{job_id} - backward compatibility check
- POST /api/briefings/generate - creates job and returns job_id

### 2. Job Lifecycle Tests
- Full success path: create → update_progress → complete
- Full failure path: create → update_progress → fail
- Progress updates accumulate correctly
- Timestamps set at correct stages

### 3. Concurrent Job Tests
- Multiple jobs can be created
- get_active returns most recent pending/running job
- Completed jobs don't appear as active

### 4. Backend-Specific Tests
- SQLite: File created at correct path
- SQLite: Schema persists across restarts
- PostgreSQL: Connection pool management (mocked)
- Backend selection based on DATABASE_URL

### 5. Edge Cases
- Invalid UUID job_id returns 404
- Empty job list returns empty array
- Job with null optional fields serializes correctly
- Large progress dict stored/retrieved correctly
- Unicode in error messages handled

### 6. Data Integrity Tests
- Job.to_dict() output matches expected schema
- Datetime fields in ISO format
- JSON fields (progress, input, output) round-trip correctly

## Test File Structure
```python
"""Integration tests for job persistence system."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
# ... imports

@pytest.fixture
def client():
    """FastAPI test client with initialized JobService."""
    ...

class TestJobAPIEndpoints:
    """API endpoint integration tests."""
    ...

class TestJobLifecycle:
    """End-to-end job lifecycle tests."""
    ...

class TestConcurrentJobs:
    """Concurrent job handling tests."""
    ...

class TestEdgeCases:
    """Edge case and error handling tests."""
    ...
```

## Output Requirements
1. Complete `tests/test_jobs_integration.py` file
2. Clear docstrings on each test class and method
3. Proper pytest fixtures for setup/teardown
4. Mock external dependencies (no actual DB connections)
5. Comments explaining any complex test logic
```
