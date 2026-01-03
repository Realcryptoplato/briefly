# PRD: Transcript Memory & Agent Integration

## Problem Statement

Briefly 3000 currently stores full video transcripts and generates summaries, but this content exists in isolated files. There's no way to:
- Query across all ingested content semantically ("What did anyone say about AI regulation?")
- Have the agent "remember" insights from past briefings
- Build up knowledge over time

## Proposed Solution

Integrate transcript storage with a vector database and/or Letta for persistent agent memory.

## Options

### Option A: pgvector (Standalone RAG)

Use the PostgreSQL instance we already have with pgvector extension.

**Approach:**
1. Chunk transcripts into ~500 token segments
2. Generate embeddings (OpenAI, Voyage, or local model)
3. Store in pgvector with metadata (video_id, channel, timestamp, topics)
4. RAG retrieval during briefing generation

**Pros:**
- Simple, no external dependencies
- Full control over chunking/retrieval
- Already have PostgreSQL running
- Lower latency

**Cons:**
- No agent reasoning/memory
- Manual RAG implementation
- No conversation history

**Effort:** ~1-2 days

---

### Option B: Letta Integration

Use existing Letta instance for agent memory.

**Approach:**
1. Create a Briefly agent in Letta
2. Ingest transcripts as archival memory
3. Agent can recall and reason about past content
4. Briefings become conversations with memory

**Pros:**
- Already running Letta
- Built-in memory management
- Agent can reason across sources
- Conversation history maintained
- Handles chunking/retrieval automatically

**Cons:**
- Additional latency (Letta API calls)
- Less control over retrieval
- Dependency on Letta availability

**Effort:** ~2-3 days

---

### Option C: Hybrid (pgvector + Letta)

Store in pgvector for fast retrieval, sync highlights to Letta for agent reasoning.

**Approach:**
1. Primary storage in pgvector (all transcripts)
2. Key insights/summaries synced to Letta archival memory
3. Use pgvector for bulk retrieval, Letta for agent interactions
4. Best of both worlds

**Pros:**
- Fast bulk queries via pgvector
- Agent memory for reasoning
- Redundancy

**Cons:**
- More complex architecture
- Data sync overhead
- Two systems to maintain

**Effort:** ~3-4 days

---

## Data Model

### Transcript Chunk (pgvector)

```sql
CREATE TABLE transcript_chunks (
    id UUID PRIMARY KEY,
    video_id VARCHAR(20) NOT NULL,
    channel_id VARCHAR(50) NOT NULL,
    channel_name VARCHAR(200),
    video_title VARCHAR(500),
    chunk_index INTEGER,
    content TEXT NOT NULL,
    embedding vector(1536),  -- OpenAI ada-002
    topics TEXT[],
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Metadata for filtering
    platform VARCHAR(20) DEFAULT 'youtube',
    duration_seconds INTEGER,
    view_count INTEGER
);

CREATE INDEX ON transcript_chunks USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON transcript_chunks (video_id);
CREATE INDEX ON transcript_chunks (channel_id);
CREATE INDEX ON transcript_chunks (published_at);
```

### Letta Memory Structure

```
Agent: briefly_curator

Core Memory:
- User preferences
- Source priorities
- Summary style preferences

Archival Memory:
- Key insights from transcripts
- Notable quotes
- Recurring themes
- Cross-source connections

Recall Memory:
- Recent briefing topics
- User questions/feedback
```

## Embedding Options

| Provider | Model | Dimensions | Cost | Notes |
|----------|-------|------------|------|-------|
| OpenAI | text-embedding-3-small | 1536 | $0.02/1M tokens | Best quality/cost |
| OpenAI | text-embedding-3-large | 3072 | $0.13/1M tokens | Highest quality |
| Voyage | voyage-3 | 1024 | $0.06/1M tokens | Good for long docs |
| Local | nomic-embed-text | 768 | Free | Self-hosted |

**Recommendation:** Start with `text-embedding-3-small` for simplicity.

## Use Cases

### 1. Semantic Search
```
User: "What have crypto influencers said about ETH staking?"
→ Query pgvector for relevant chunks
→ Return sources with timestamps
```

### 2. Trend Detection
```
Agent: "I've noticed 5 of your sources mentioned 'AI agents' this week,
up from 0 last week. Here's what they're saying..."
```

### 3. Cross-Source Synthesis
```
Agent: "Both @balajis and Lex Fridman discussed longevity this week.
Balaji focused on... while Lex's guest argued..."
```

### 4. Personalized Briefings
```
User: "Skip anything about memecoins"
→ Letta remembers preference
→ Future briefings filter accordingly
```

## Implementation Plan

### Phase 1: pgvector Foundation (Option A)
1. Add embedding generation to transcript processor
2. Create chunks table with pgvector
3. Implement similarity search endpoint
4. Update briefing generation to use RAG

### Phase 2: Letta Integration (if chosen)
1. Create Briefly agent in Letta
2. Define memory schema
3. Implement transcript → Letta sync
4. Add conversational briefing mode

## Questions to Resolve

1. **Embedding provider** - OpenAI vs self-hosted?
2. **Chunk size** - 500 tokens? 1000?
3. **Letta deployment** - Where is it running? API endpoint?
4. **Retention policy** - How long to keep old transcripts?
5. **Privacy** - Any content that shouldn't be stored?

## Success Metrics

- Query response time < 500ms
- Relevant chunk retrieval (precision > 0.8)
- User satisfaction with semantic search
- Reduced redundancy in briefings

## Decision Needed

**Which option should we implement?**

- [ ] Option A: pgvector only
- [ ] Option B: Letta only
- [ ] Option C: Hybrid approach

---

*Created: 2026-01-02*
*Status: Awaiting Decision*
