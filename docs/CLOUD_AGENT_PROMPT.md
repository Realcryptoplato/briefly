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
