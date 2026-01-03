# PRD: Phase 1 Testing - pgvector Memory Validation

**Status**: Ready for Testing Agent
**Branch to Test**: `claude/pgvector-memory-implementation-5e6ry`
**Base Branch**: `main`
**Related PRD**: `docs/PRD-phase1-pgvector.md`

---

## Overview

Validate the pgvector memory implementation before merging. Run all tests, fix any issues, and ensure the feature works end-to-end.

## Prerequisites

The testing agent must have access to:
- Docker (for PostgreSQL with pgvector)
- Python 3.12+
- OpenAI API key (for embeddings)

---

## Testing Tasks

### 1. Environment Setup

```bash
# Clone and checkout feature branch
git clone https://github.com/Realcryptoplato/briefly.git
cd briefly
git checkout claude/pgvector-memory-implementation-5e6ry

# Install dependencies
uv sync

# Copy environment template
cp .env.example .env
# Add required keys to .env:
# - OPENAI_API_KEY (required for embeddings)
# - Existing keys from .env.example
```

### 2. Database Setup

```bash
# Start PostgreSQL with pgvector
docker-compose up -d

# Wait for database to be ready
sleep 5

# Run migration
uv run python -c "
import asyncio
from sqlalchemy import text
from briefly.core.database import get_async_session

async def run_migration():
    async with get_async_session() as session:
        # Read and execute migration
        with open('src/briefly/migrations/001_pgvector_schema.sql') as f:
            sql = f.read()

        # Execute each statement
        for statement in sql.split(';'):
            statement = statement.strip()
            if statement:
                await session.execute(text(statement))

        print('Migration completed successfully')

asyncio.run(run_migration())
"
```

**Verify migration:**
```bash
docker exec -it briefly-postgres psql -U briefly -d briefly -c "\dt"
# Should show: content_items, content_chunks

docker exec -it briefly-postgres psql -U briefly -d briefly -c "\dx"
# Should show: vector extension enabled
```

### 3. Unit Tests

Create `tests/test_phase1_memory.py`:

```python
"""Tests for Phase 1 pgvector memory implementation."""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

# Test EmbeddingService
class TestEmbeddingService:

    def test_count_tokens(self):
        """Test token counting."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        # Simple text
        count = service.count_tokens("Hello world")
        assert count == 2

        # Longer text
        count = service.count_tokens("The quick brown fox jumps over the lazy dog")
        assert count > 5

    def test_chunk_text_short(self):
        """Short text should return single chunk."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        text = "This is a short text."
        chunks = service.chunk_text(text, max_tokens=500)

        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_text_long(self):
        """Long text should be split into multiple chunks."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        # Create text longer than chunk size
        text = "This is a sentence. " * 200  # ~800 tokens
        chunks = service.chunk_text(text, max_tokens=100, overlap=10)

        assert len(chunks) > 1
        # Each chunk should be under limit
        for chunk in chunks:
            assert service.count_tokens(chunk) <= 110  # Allow some overflow for sentence boundaries

    def test_chunk_text_preserves_sentences(self):
        """Chunking should try to preserve sentence boundaries."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        text = "First sentence here. Second sentence here. Third sentence here."
        chunks = service.chunk_text(text, max_tokens=10, overlap=0)

        # Chunks should end at sentence boundaries when possible
        for chunk in chunks:
            assert chunk.strip().endswith('.') or chunk == chunks[-1]

    @pytest.mark.asyncio
    async def test_generate_embedding(self):
        """Test embedding generation with OpenAI."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        embedding = await service.generate_embedding("Test content about Bitcoin")

        assert isinstance(embedding, list)
        assert len(embedding) == 1536  # text-embedding-3-small dimensions
        assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_generate_embeddings_batch(self):
        """Test batch embedding generation."""
        from briefly.services.embeddings import EmbeddingService
        service = EmbeddingService()

        texts = ["First text", "Second text", "Third text"]
        embeddings = await service.generate_embeddings_batch(texts)

        assert len(embeddings) == 3
        assert all(len(e) == 1536 for e in embeddings)


# Test VectorStore
class TestVectorStore:

    @pytest.mark.asyncio
    async def test_store_content(self):
        """Test storing content with embeddings."""
        from briefly.services.vectorstore import VectorStore
        store = VectorStore()

        content_id = await store.store_content(
            platform="test",
            platform_id="test-001",
            source_id="test-source",
            source_name="Test Source",
            content="Bitcoin is a decentralized cryptocurrency that enables peer-to-peer transactions.",
            url="https://example.com/test",
            title="Test Article",
            published_at=datetime.now(timezone.utc),
        )

        assert content_id is not None

    @pytest.mark.asyncio
    async def test_store_content_deduplication(self):
        """Test that duplicate content is not stored twice."""
        from briefly.services.vectorstore import VectorStore
        store = VectorStore()

        # Store same content twice
        id1 = await store.store_content(
            platform="test",
            platform_id="test-dedup",
            source_id="test-source",
            source_name="Test Source",
            content="Duplicate content test",
        )

        id2 = await store.store_content(
            platform="test",
            platform_id="test-dedup",  # Same platform_id
            source_id="test-source",
            source_name="Test Source",
            content="Duplicate content test",
        )

        # Second call should return None (skipped)
        assert id1 is not None
        assert id2 is None

    @pytest.mark.asyncio
    async def test_search_basic(self):
        """Test basic semantic search."""
        from briefly.services.vectorstore import VectorStore
        store = VectorStore()

        # Store test content
        await store.store_content(
            platform="test",
            platform_id="test-search-001",
            source_id="test-source",
            source_name="Crypto Channel",
            content="Ethereum is transitioning to proof of stake consensus mechanism.",
        )

        # Search for related content
        results = await store.search("proof of stake blockchain")

        assert len(results) > 0
        # The stored content should be in results
        assert any("ethereum" in r.get("chunk_content", "").lower() for r in results)

    @pytest.mark.asyncio
    async def test_search_with_platform_filter(self):
        """Test search filtering by platform."""
        from briefly.services.vectorstore import VectorStore
        store = VectorStore()

        # Store content on different platforms
        await store.store_content(
            platform="youtube",
            platform_id="yt-filter-test",
            source_id="yt-channel",
            source_name="YouTube Channel",
            content="AI agents are revolutionizing software development.",
        )

        await store.store_content(
            platform="x",
            platform_id="x-filter-test",
            source_id="x-user",
            source_name="X User",
            content="AI agents discussion on Twitter.",
        )

        # Search with platform filter
        yt_results = await store.search("AI agents", platform="youtube")
        x_results = await store.search("AI agents", platform="x")

        # Results should only contain matching platform
        for r in yt_results:
            assert r.get("platform") == "youtube"
        for r in x_results:
            assert r.get("platform") == "x"

    @pytest.mark.asyncio
    async def test_search_with_date_filter(self):
        """Test search filtering by date range."""
        from briefly.services.vectorstore import VectorStore
        from datetime import timedelta
        store = VectorStore()

        now = datetime.now(timezone.utc)

        # Store old content
        await store.store_content(
            platform="test",
            platform_id="test-old",
            source_id="test-source",
            source_name="Test",
            content="Old content about machine learning.",
            published_at=now - timedelta(days=30),
        )

        # Store recent content
        await store.store_content(
            platform="test",
            platform_id="test-new",
            source_id="test-source",
            source_name="Test",
            content="New content about machine learning.",
            published_at=now - timedelta(hours=1),
        )

        # Search only recent
        results = await store.search(
            "machine learning",
            since=now - timedelta(days=7),
        )

        # Should only find recent content
        assert all(r.get("platform_id") != "test-old" for r in results)


# Test API endpoints
class TestSearchAPI:

    @pytest.mark.asyncio
    async def test_search_endpoint(self):
        """Test POST /api/search endpoint."""
        from fastapi.testclient import TestClient
        from briefly.api.main import app

        client = TestClient(app)

        response = client.post("/api/search", json={"query": "cryptocurrency"})

        assert response.status_code == 200
        data = response.json()
        assert "query" in data
        assert "count" in data
        assert "results" in data

    @pytest.mark.asyncio
    async def test_search_endpoint_get(self):
        """Test GET /api/search endpoint."""
        from fastapi.testclient import TestClient
        from briefly.api.main import app

        client = TestClient(app)

        response = client.get("/api/search?query=blockchain")

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "blockchain"

    @pytest.mark.asyncio
    async def test_stats_endpoint(self):
        """Test GET /api/search/stats endpoint."""
        from fastapi.testclient import TestClient
        from briefly.api.main import app

        client = TestClient(app)

        response = client.get("/api/search/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total_content_items" in data
        assert "total_chunks" in data
        assert "by_platform" in data


# Run all tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

Run tests:
```bash
uv run pytest tests/test_phase1_memory.py -v
```

### 4. Integration Test - End to End

Create `tests/test_e2e_memory.py`:

```python
"""End-to-end test for the memory system."""

import pytest
import asyncio
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_full_pipeline():
    """Test complete flow: store content -> generate briefing -> search."""
    from briefly.services.vectorstore import VectorStore
    from briefly.services.curation import CurationService

    store = VectorStore()

    # 1. Store some test content directly
    await store.store_content(
        platform="youtube",
        platform_id="test-video-1",
        source_id="UCtest",
        source_name="Test Channel",
        content="""
        In this video we discuss the future of artificial intelligence.
        AI agents are becoming more capable every day.
        Large language models like GPT-4 and Claude are transforming how we work.
        The implications for software development are enormous.
        """,
        title="The Future of AI",
        url="https://youtube.com/watch?v=test1",
        published_at=datetime.now(timezone.utc),
    )

    await store.store_content(
        platform="x",
        platform_id="tweet-123",
        source_id="elonmusk",
        source_name="Elon Musk",
        content="Tesla is working on robotaxis. Full self-driving is coming soon.",
        url="https://x.com/elonmusk/status/123",
        published_at=datetime.now(timezone.utc),
    )

    await store.store_content(
        platform="youtube",
        platform_id="test-video-2",
        source_id="UCtest2",
        source_name="Crypto Channel",
        content="""
        Bitcoin just hit a new all-time high.
        Institutional adoption is accelerating.
        Ethereum staking yields remain attractive.
        The crypto market is maturing rapidly.
        """,
        title="Crypto Market Update",
        url="https://youtube.com/watch?v=test2",
        published_at=datetime.now(timezone.utc),
    )

    # 2. Test semantic search
    ai_results = await store.search("artificial intelligence and language models")
    assert len(ai_results) > 0
    assert any("ai" in r.get("chunk_content", "").lower() for r in ai_results)

    crypto_results = await store.search("bitcoin cryptocurrency market")
    assert len(crypto_results) > 0
    assert any("bitcoin" in r.get("chunk_content", "").lower() for r in crypto_results)

    # 3. Test platform filtering
    yt_only = await store.search("future technology", platform="youtube")
    assert all(r.get("platform") == "youtube" for r in yt_only)

    # 4. Test stats
    from briefly.api.routes.search import get_vector_stats
    stats = await get_vector_stats()
    assert stats["total_content_items"] >= 3
    assert stats["total_chunks"] >= 3

    print("✅ End-to-end test passed!")


if __name__ == "__main__":
    asyncio.run(test_full_pipeline())
```

Run e2e test:
```bash
uv run python tests/test_e2e_memory.py
```

### 5. Manual Testing Checklist

Start the server and verify manually:

```bash
# Start server
uv run python main.py
```

**Dashboard Tests** (http://localhost:8000):

- [ ] Search panel is visible in left sidebar
- [ ] Vector stats show item/chunk counts
- [ ] Search input accepts queries
- [ ] Search returns results with similarity scores
- [ ] Results show platform icons and source names
- [ ] Clicking search with empty query handles gracefully

**API Tests** (curl):

```bash
# Test search endpoint
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "cryptocurrency"}'

# Test with filters
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "AI", "platform": "youtube", "limit": 5}'

# Test stats
curl http://localhost:8000/api/search/stats

# Test GET variant
curl "http://localhost:8000/api/search?query=bitcoin&limit=3"
```

### 6. Fix Any Issues

If tests fail:
1. Read error messages carefully
2. Fix the issue in the relevant file
3. Re-run tests
4. Commit fixes with descriptive message

Common issues to check:
- Missing `OPENAI_API_KEY` in .env
- Database not running or migration not applied
- Import errors (check all imports)
- Async/await issues
- Type mismatches in SQL queries

### 7. Cleanup and Commit

After all tests pass:

```bash
# Remove test data from database (optional)
docker exec -it briefly-postgres psql -U briefly -d briefly -c "
DELETE FROM content_chunks WHERE content_id IN (
  SELECT id FROM content_items WHERE platform = 'test'
);
DELETE FROM content_items WHERE platform = 'test';
"

# Add test file
git add tests/

# Commit any fixes
git add -A
git commit -m "Add Phase 1 memory tests and fixes

- tests/test_phase1_memory.py: Unit tests for embeddings and vector store
- tests/test_e2e_memory.py: End-to-end integration test
- Fixed: [list any issues fixed]

🤖 Generated with [Claude Code](https://claude.com/claude-code)"

git push
```

---

## Success Criteria

All must pass before merging:

- [ ] Database migration runs without errors
- [ ] `uv run pytest tests/test_phase1_memory.py -v` - All tests pass
- [ ] `uv run python tests/test_e2e_memory.py` - E2E test passes
- [ ] Server starts without errors
- [ ] Dashboard search UI works
- [ ] API endpoints return valid responses
- [ ] No Python warnings or deprecations

---

## Output

After testing completes, report:
1. Test results (pass/fail counts)
2. Any issues found and fixed
3. Final commit hash
4. Confirmation ready for merge

---

*Created: 2026-01-03*
*For: Testing Cloud Agent*
