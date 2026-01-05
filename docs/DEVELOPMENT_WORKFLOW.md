# Briefly Development Workflow

## Team Structure

| Team | Focus Area | Primary Files |
|------|------------|---------------|
| **n8n Team** | Workflow orchestration | `n8n-workflows/` |
| **API Team** | Python backend | `src/briefly/` |
| **Dashboard Team** | Frontend UI | `src/briefly/dashboard/` |

## Shared Infrastructure

All teams share the same PostgreSQL database (with pgvector):

```
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL (pgvector)                     │
│                  Port 5436 (dev) / 5435 (prod)              │
├─────────────────────────────────────────────────────────────┤
│  jobs table          │  content_items    │  content_chunks  │
│  (job tracking)      │  (ingested data)  │  (embeddings)    │
└─────────────────────────────────────────────────────────────┘
         ▲                      ▲                    ▲
         │                      │                    │
    ┌────┴────┐            ┌────┴────┐          ┌────┴────┐
    │ n8n     │            │ Briefly │          │ Briefly │
    │ Flows   │───────────▶│   API   │◀─────────│Dashboard│
    └─────────┘            └─────────┘          └─────────┘
```

## Branch Strategy

```
main                    # Production-ready code
├── feature/*           # New features (API, dashboard)
├── n8n/*               # n8n workflow changes
└── fix/*               # Bug fixes
```

### Branch Naming

- `feature/add-podcast-ingestion` - New functionality
- `n8n/update-progress-webhooks` - n8n workflow updates
- `fix/job-status-race-condition` - Bug fixes

## Development Environments

### Local Development

```bash
# Start local services (SQLite, no dependencies)
uv run uvicorn briefly.api.main:app --reload --port 8000

# Or with local PostgreSQL + Redis
docker-compose up -d
DATABASE_URL=postgresql://briefly:briefly3000@localhost:5436/briefly \
  uv run uvicorn briefly.api.main:app --reload --port 8000
```

### VPS Integration Testing

```bash
# Connect to VPS PostgreSQL via Tailscale
export DATABASE_URL="postgresql://briefly:${POSTGRES_PASSWORD}@100.101.168.91:5435/briefly"
uv run uvicorn briefly.api.main:app --reload --port 8000
```

## Team Workflows

### n8n Team

1. **Edit workflows** in n8n UI (`n8n.ella-ai-care.com`)
2. **Export JSON** to `n8n-workflows/` directory
3. **Commit changes** with clear naming:
   ```bash
   git checkout -b n8n/update-podcast-adapter
   git add n8n-workflows/
   git commit -m "Update podcast adapter with progress reporting"
   git push origin n8n/update-podcast-adapter
   ```
4. **Create PR** for review

### API Team

1. **Develop locally** with SQLite (fast iteration)
2. **Test with PostgreSQL** before merging
3. **Standard PR workflow**:
   ```bash
   git checkout -b feature/add-search-endpoint
   # ... make changes ...
   uv run pytest
   git commit -m "Add semantic search endpoint"
   git push origin feature/add-search-endpoint
   ```

### Dashboard Team

1. **Run API locally** or connect to VPS
2. **Edit dashboard files** in `src/briefly/dashboard/`
3. **Test in browser** at `http://localhost:8000`
4. **Standard PR workflow**

## Integration Points

### n8n → API Webhooks

n8n workflows call these Briefly API endpoints:

| Endpoint | Purpose | Called By |
|----------|---------|-----------|
| `POST /api/n8n/progress` | Update job progress | All extraction workflows |
| `POST /api/n8n/complete` | Mark job complete | All extraction workflows |
| `GET /api/jobs/{id}` | Check job status | Orchestrator workflow |

### API → Database

The API auto-creates tables on startup. Schema changes require:

1. Add migration to `src/briefly/migrations/`
2. Update `JobService.init()` if needed
3. Document in PR description

## CI/CD Pipeline (Recommended)

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync
      - run: uv run pytest

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv run ruff check src/

  # n8n workflow validation (optional)
  validate-n8n:
    runs-on: ubuntu-latest
    if: contains(github.event.pull_request.changed_files, 'n8n-workflows/')
    steps:
      - uses: actions/checkout@v4
      - run: |
          for f in n8n-workflows/**/*.json; do
            python -m json.tool "$f" > /dev/null || exit 1
          done
```

## VPS Deployment

### PostgreSQL Migration (One-Time)

If the VPS already has `postgres:16-alpine`, migrate to pgvector:

```bash
ssh vultr-letta

# Backup existing data
docker exec briefly-postgres pg_dump -U briefly briefly > briefly_backup.sql

# Stop and remove old container
cd /opt/briefly/infrastructure/docker
docker-compose down

# Pull new image and start
docker-compose pull
docker-compose up -d

# Restore data (if needed)
docker exec -i briefly-postgres psql -U briefly briefly < briefly_backup.sql

# Install pgvector extension
docker exec briefly-postgres psql -U briefly briefly -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### API Deployment

```bash
ssh vultr-letta
cd /opt/briefly
git pull origin main
docker-compose restart briefly-api  # when containerized
```

## Conflict Resolution

### Database Schema Conflicts

If multiple teams need schema changes:

1. Coordinate via PRD or issue discussion
2. Use sequential migration numbers (`002_`, `003_`)
3. Test migrations locally before merging

### n8n Workflow Conflicts

n8n workflows are JSON files - merge conflicts are rare but possible:

1. Re-export from n8n UI (source of truth)
2. Commit with conflict resolution notes

## Communication

- **PRs**: Tag relevant team members
- **Database changes**: Announce in PR description
- **Breaking API changes**: Update PRD and notify n8n team
