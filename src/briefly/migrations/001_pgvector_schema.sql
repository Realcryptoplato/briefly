-- Migration: 001_pgvector_schema
-- Description: Create pgvector tables for content storage and semantic search
-- Created: 2026-01-03

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
-- Note: This index requires at least 100 rows to be effective
-- For small datasets, queries will fallback to sequential scan
CREATE INDEX idx_chunks_embedding ON content_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index for joining chunks back to content
CREATE INDEX idx_chunks_content_id ON content_chunks(content_id);
