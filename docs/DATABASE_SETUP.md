# Briefly Database Configuration

## Overview

Briefly uses a dual-database architecture:
- **Development**: SQLite (local file-based, zero config) or PostgreSQL 17 with pgvector (local Docker)
- **Production**: PostgreSQL 17 with pgvector on VPS (port 5435)

The `JobService` in `src/briefly/services/jobs.py` automatically selects the backend based on the `DATABASE_URL` environment variable.

## pgvector Support

Both dev and prod PostgreSQL instances use `pgvector/pgvector:pg17` for:
- Vector embeddings storage (1536 dimensions, OpenAI text-embedding-3-small)
- Semantic search via cosine similarity
- Content deduplication

## Database Selection Logic

```python
# If DATABASE_URL is set → PostgreSQL
# If DATABASE_URL is NOT set → SQLite (.cache/jobs.db)
```

## Local Development (SQLite)

No configuration needed. The API will automatically use SQLite:

```bash
# Start local API (uses SQLite by default)
uv run uvicorn src.briefly.api.main:app --reload --port 8000
```

SQLite database location: `.cache/jobs.db`

## Production (PostgreSQL)

### VPS Database Details

| Property | Value |
|----------|-------|
| Host | `100.101.168.91` (via Tailscale) |
| Port | `5435` |
| Database | `briefly` |
| User | `briefly` |
| Container | `briefly-postgres` |

### Connection String

```bash
# For apps connecting from outside VPS
DATABASE_URL=postgresql://briefly:BrieflyDB2025Secure!@100.101.168.91:5435/briefly

# For Docker containers on briefly-network
DATABASE_URL=postgresql://briefly:BrieflyDB2025Secure!@briefly-postgres:5432/briefly
```

### Switching to Production Database

Set the `DATABASE_URL` environment variable:

```bash
# Option 1: Export in shell
export DATABASE_URL="postgresql://briefly:BrieflyDB2025Secure!@100.101.168.91:5435/briefly"
uv run uvicorn src.briefly.api.main:app --reload --port 8000

# Option 2: In .env file
echo 'DATABASE_URL=postgresql://briefly:BrieflyDB2025Secure!@100.101.168.91:5435/briefly' >> .env
```

## n8n Workflow Integration

The n8n workflows on VPS (`n8n.ella-ai-care.com`) call the Briefly API. Currently configured to reach local dev API via Tailscale:

```
BRIEFLY_API_URL=http://100.100.44.61:8000
```

When deploying Briefly API to VPS, update to:
```
BRIEFLY_API_URL=http://localhost:8000  # or container name
```

## VPS Infrastructure

### Container Status

```bash
# SSH to VPS
ssh vultr-letta

# Check Briefly postgres
docker ps | grep briefly

# View logs
docker logs briefly-postgres
```

### Docker Compose Location

```
/opt/briefly/infrastructure/docker/docker-compose.yml
```

### Port Mapping (VPS)

| Port | Service |
|------|---------|
| 5432 | Letta postgres (internal) |
| 5433 | Ella postgres |
| 5434 | Letta postgres v012 |
| 5435 | **Briefly postgres** |
| 5678 | n8n |

## Schema Initialization

The `JobService.init()` method creates tables automatically:

```python
from briefly.services.jobs import JobService

service = JobService()
await service.init()  # Creates tables if not exist
```

Tables created:
- `jobs` - Job tracking with status, progress, input/output

## Testing Database Connection

```python
# Test PostgreSQL connection
import asyncpg

async def test_connection():
    conn = await asyncpg.connect(
        "postgresql://briefly:BrieflyDB2025Secure!@100.101.168.91:5435/briefly"
    )
    result = await conn.fetchval("SELECT version()")
    print(result)
    await conn.close()
```

## Troubleshooting

### "Connection refused" to VPS database

1. Check Tailscale is connected: `tailscale status`
2. Verify VPS IP: `ssh vultr-letta "tailscale ip -4"` → should be `100.101.168.91`
3. Check container is running: `ssh vultr-letta "docker ps | grep briefly"`

### SQLite being used instead of PostgreSQL

Verify `DATABASE_URL` is set:
```bash
echo $DATABASE_URL
```

Check API health endpoint reports correct database:
```bash
curl http://localhost:8000/health
# Should show: "database": "postgresql" (not "sqlite")
```
