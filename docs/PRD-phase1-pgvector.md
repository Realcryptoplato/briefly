# PRD: Phase 1 - pgvector Memory Foundation

**Status**: Ready for Implementation
**Branch**: `feature/pgvector-memory`
**Estimated Effort**: 1-2 days
**Related Issue**: #1

---

## Overview

Implement pgvector-based storage for transcript chunks and semantic search. This is Phase 1 of the memory system - creating the "corpus memory" (the library) that enables queries like "What did anyone say about Bitcoin this week?"

## Goals

1. Store content chunks with vector embeddings in PostgreSQL
2. Enable semantic similarity search across all ingested content
3. Support filtering by source, platform, and time range
4. Prepare schema for future content sources (RSS, podcasts, etc.)

## Non-Goals (Phase 2+)

- Letta integration (Phase 2)
- Trend detection / topic extraction (Phase 3)
- New content source adapters (separate PRD)

---

## Technical Specification

### Database Schema

Create migration file: `src/briefly/migrations/001_pgvector_schema.sql`

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Unified content items table (all platforms)
CREATE TABLE content_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(20) NOT NULL,  -- 'x', 'youtube', 'podcast', 'rss', 'hn', 'bluesky'
    platform_id VARCHAR(100) NOT NULL,  -- Original ID from platform
    source_id VARCHAR(100) NOT NULL,  -- Channel/user/feed identifier
    source_name VARCHAR(200),
    title VARCHAR(500),
    content TEXT NOT NULL,
    url TEXT,
    metrics JSONB DEFAULT '{}',
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(platform, platform_id)
);

-- Content chunks for vector search
CREATE TABLE content_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id UUID NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_count INTEGER,
    embedding vector(1536),  -- OpenAI text-embedding-3-small dimensions
    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(content_id, chunk_index)
);

-- Indexes for performance
CREATE INDEX idx_content_items_platform ON content_items(platform);
CREATE INDEX idx_content_items_source ON content_items(source_id);
CREATE INDEX idx_content_items_published ON content_items(published_at DESC);
CREATE INDEX idx_content_items_ingested ON content_items(ingested_at DESC);

-- Vector similarity index (IVFFlat for speed)
CREATE INDEX idx_chunks_embedding ON content_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index for joining chunks back to content
CREATE INDEX idx_chunks_content_id ON content_chunks(content_id);
```

### Configuration Updates

Add to `src/briefly/core/config.py`:

```python
# OpenAI (for embeddings)
openai_api_key: str | None = None
embedding_model: str = "text-embedding-3-small"
embedding_dimensions: int = 1536

# Chunking settings
chunk_size_tokens: int = 500
chunk_overlap_tokens: int = 50
```

Add to `.env.example`:

```
# OpenAI (for embeddings)
OPENAI_API_KEY=sk-...
```

### New Service: Embedding Service

Create `src/briefly/services/embeddings.py`:

```python
"""Embedding generation service using OpenAI."""

import tiktoken
from openai import AsyncOpenAI
from briefly.core.config import get_settings

class EmbeddingService:
    """Generate embeddings for text content."""

    def __init__(self):
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.embedding_model
        self._tokenizer = tiktoken.encoding_for_model("gpt-4")

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._tokenizer.encode(text))

    def chunk_text(self, text: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
        """
        Split text into overlapping chunks.

        Uses sentence boundaries when possible.
        """
        # Implementation: split on sentences, accumulate until max_tokens
        # Include overlap from previous chunk
        pass

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        response = await self._client.embeddings.create(
            model=self._model,
            input=text
        )
        return response.data[0].embedding

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts (batched)."""
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts
        )
        return [item.embedding for item in response.data]
```

### New Service: Vector Store

Create `src/briefly/services/vectorstore.py`:

```python
"""Vector storage and retrieval using pgvector."""

import uuid
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from briefly.core.database import get_async_session
from briefly.services.embeddings import EmbeddingService

class VectorStore:
    """Store and search content with vector embeddings."""

    def __init__(self):
        self._embeddings = EmbeddingService()

    async def store_content(
        self,
        platform: str,
        platform_id: str,
        source_id: str,
        source_name: str,
        content: str,
        url: str | None = None,
        title: str | None = None,
        metrics: dict | None = None,
        published_at: datetime | None = None,
    ) -> uuid.UUID:
        """
        Store content item and generate chunks with embeddings.

        Returns the content_id.
        """
        async with get_async_session() as session:
            # 1. Insert content item
            # 2. Chunk the content
            # 3. Generate embeddings for chunks (batched)
            # 4. Insert chunks with embeddings
            pass

    async def search(
        self,
        query: str,
        limit: int = 10,
        platform: str | None = None,
        source_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict]:
        """
        Semantic search across all content.

        Returns list of matching content with similarity scores.
        """
        # 1. Generate embedding for query
        # 2. Perform cosine similarity search with filters
        # 3. Join back to content_items for metadata
        # 4. Return ranked results

        query_embedding = await self._embeddings.generate_embedding(query)

        async with get_async_session() as session:
            # pgvector cosine similarity search
            sql = text("""
                SELECT
                    ci.id,
                    ci.platform,
                    ci.source_name,
                    ci.title,
                    ci.url,
                    ci.published_at,
                    cc.content as chunk_content,
                    1 - (cc.embedding <=> :embedding) as similarity
                FROM content_chunks cc
                JOIN content_items ci ON cc.content_id = ci.id
                WHERE 1=1
                    AND (:platform IS NULL OR ci.platform = :platform)
                    AND (:source_id IS NULL OR ci.source_id = :source_id)
                    AND (:since IS NULL OR ci.published_at >= :since)
                    AND (:until IS NULL OR ci.published_at <= :until)
                ORDER BY cc.embedding <=> :embedding
                LIMIT :limit
            """)

            result = await session.execute(sql, {
                "embedding": str(query_embedding),
                "platform": platform,
                "source_id": source_id,
                "since": since,
                "until": until,
                "limit": limit,
            })

            return [dict(row) for row in result.fetchall()]
```

### API Endpoints

Add to `src/briefly/api/routes/search.py`:

```python
"""Semantic search endpoints."""

from fastapi import APIRouter, Query
from datetime import datetime
from briefly.services.vectorstore import VectorStore

router = APIRouter()

@router.post("")
async def search_content(
    query: str,
    limit: int = Query(default=10, le=50),
    platform: str | None = None,
    source_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """
    Semantic search across all ingested content.

    Examples:
    - "What did anyone say about Bitcoin?"
    - "AI regulation discussions"
    - "Ethereum staking opinions"
    """
    store = VectorStore()
    results = await store.search(
        query=query,
        limit=limit,
        platform=platform,
        source_id=source_id,
        since=since,
        until=until,
    )

    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


@router.get("/stats")
async def vector_stats() -> dict:
    """Get statistics about stored content and embeddings."""
    # Return counts of content_items and content_chunks by platform
    pass
```

Register router in `src/briefly/api/main.py`:

```python
from briefly.api.routes import search
app.include_router(search.router, prefix="/api/search", tags=["search"])
```

### Integration: Update Curation Service

Modify `src/briefly/services/curation.py` to store content in vector store after fetching:

```python
async def create_briefing(self, ...):
    # ... existing fetch logic ...

    # NEW: Store content in vector store for future search
    store = VectorStore()
    for item in all_items:
        await store.store_content(
            platform=item.platform,
            platform_id=item.platform_id,
            source_id=item.source_identifier,
            source_name=item.source_name,
            content=item.content,
            url=item.url,
            metrics=item.metrics,
            published_at=item.posted_at,
        )

    # ... existing summarization logic ...
```

### Database Setup

Update `src/briefly/core/database.py` to support async sessions:

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from contextlib import asynccontextmanager

# Create async engine
async_engine = create_async_engine(
    settings.database_url.replace("postgresql://", "postgresql+asyncpg://"),
    echo=settings.debug,
)

async_session_maker = async_sessionmaker(async_engine, class_=AsyncSession)

@asynccontextmanager
async def get_async_session():
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

Add dependency: `uv add asyncpg`

### Dashboard Updates

Add search UI to `src/briefly/api/templates/dashboard.html`:

```html
<!-- Search Panel (add to left sidebar) -->
<div class="bg-gray-800 rounded-lg p-6">
    <h2 class="text-xl font-semibold mb-4 flex items-center gap-2">
        <span class="text-2xl">🔍</span> Semantic Search
    </h2>

    <form @submit.prevent="searchContent" class="mb-4">
        <input
            type="text"
            x-model="searchQuery"
            placeholder="What did anyone say about..."
            class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-sm mb-2"
        >
        <button type="submit" :disabled="searching" class="w-full bg-cyan-600 hover:bg-cyan-500 px-4 py-2 rounded text-sm font-medium">
            <span x-show="searching">Searching...</span>
            <span x-show="!searching">Search</span>
        </button>
    </form>

    <div x-show="searchResults.length" class="space-y-2 max-h-64 overflow-y-auto">
        <template x-for="result in searchResults" :key="result.id">
            <div class="bg-gray-700 rounded p-2 text-sm">
                <div class="flex items-center gap-1 text-xs text-gray-400">
                    <span x-text="result.platform"></span>
                    <span>•</span>
                    <span x-text="result.source_name"></span>
                </div>
                <p class="mt-1 line-clamp-2" x-text="result.chunk_content"></p>
                <div class="text-xs text-cyan-400 mt-1">
                    <span x-text="(result.similarity * 100).toFixed(1)"></span>% match
                </div>
            </div>
        </template>
    </div>
</div>
```

Add JavaScript methods:

```javascript
searchQuery: '',
searchResults: [],
searching: false,

async searchContent() {
    if (!this.searchQuery.trim()) return;
    this.searching = true;

    try {
        const resp = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: this.searchQuery })
        });
        const data = await resp.json();
        this.searchResults = data.results;
    } catch (e) {
        console.error(e);
    } finally {
        this.searching = false;
    }
}
```

---

## Testing

### Unit Tests

Create `tests/test_embeddings.py`:

```python
import pytest
from briefly.services.embeddings import EmbeddingService

@pytest.mark.asyncio
async def test_chunk_text():
    service = EmbeddingService()
    text = "Long text... " * 500
    chunks = service.chunk_text(text, max_tokens=100)
    assert len(chunks) > 1
    assert all(service.count_tokens(c) <= 100 for c in chunks)

@pytest.mark.asyncio
async def test_generate_embedding():
    service = EmbeddingService()
    embedding = await service.generate_embedding("Test content")
    assert len(embedding) == 1536
```

### Integration Tests

Create `tests/test_vectorstore.py`:

```python
import pytest
from briefly.services.vectorstore import VectorStore

@pytest.mark.asyncio
async def test_store_and_search():
    store = VectorStore()

    # Store test content
    content_id = await store.store_content(
        platform="test",
        platform_id="test-123",
        source_id="test-source",
        source_name="Test Source",
        content="Bitcoin is a decentralized digital currency.",
    )

    # Search for it
    results = await store.search("cryptocurrency")
    assert len(results) > 0
    assert any(r["platform_id"] == "test-123" for r in results)
```

---

## Acceptance Criteria

- [ ] Database migration runs successfully with pgvector enabled
- [ ] Content items are stored with platform/source metadata
- [ ] Text is chunked into ~500 token segments with overlap
- [ ] Embeddings are generated via OpenAI API
- [ ] Semantic search returns relevant results ranked by similarity
- [ ] Search supports filtering by platform, source, and date range
- [ ] Dashboard has working search UI
- [ ] Existing briefing flow stores content in vector store
- [ ] Stats endpoint shows content/chunk counts

---

## Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    # ... existing ...
    "asyncpg>=0.29.0",
    "pgvector>=0.2.4",
    "tiktoken>=0.5.0",
]
```

---

## Environment Variables

Required in `.env`:

```
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://briefly:briefly3000@localhost:5436/briefly
```

---

## Rollback Plan

If issues arise:
1. Drop tables: `DROP TABLE content_chunks; DROP TABLE content_items;`
2. Revert to file-based transcript storage
3. Search functionality gracefully degrades (returns empty)

---

*Created: 2026-01-03*
*Author: Claude Opus 4.5*
*For: Cloud Agent Implementation*
