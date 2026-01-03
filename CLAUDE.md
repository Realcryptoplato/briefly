# Briefly 3000 - AI-Powered Media Curation

## Project Overview
An executive assistant that delivers personalized daily media briefings from user-curated sources across X, YouTube, Reddit, and email. Users manually add sources they care about; Briefly 3000 aggregates, dedupes, and summarizes via AI.

**Key Insight**: No automatic follows fetching (X API limitation). User-curated sources model instead.

## Tech Stack
- **Language**: Python 3.12+
- **Web Framework**: FastAPI (async native, auto OpenAPI docs)
- **Task Queue**: Celery with Redis
- **Database**: PostgreSQL via Supabase or self-hosted
- **X API**: Bearer token auth (bot account @briefly3000, no user OAuth needed)
- **AI Backend**: Grok 4.1 (`grok-4-1-fast`) via xAI API
- **Frontend**: Next.js dashboard

## Architecture

### Core Data Flow (X)
1. User adds X usernames → stored in DB
2. Scheduled job (or on-demand):
   - Lookup user IDs via X API
   - Create temp private list on @briefly3000
   - Add members (throttled <300 per run)
   - GET /2/lists/:id/tweets (last 24-48h)
   - Delete list
3. Combine with other platforms → AI summary → deliver

### Platform Adapters
| Platform | Auth Method | Data Source |
|----------|-------------|-------------|
| X | Bearer token (bot) | Temp lists |
| YouTube | User OAuth | subscriptions.list API |
| Reddit | App-only | PRAW (hot/new posts) |
| Email | User OAuth | Gmail/Outlook API |

## Directory Structure
```
briefly/
├── src/
│   └── briefly/
│       ├── api/           # FastAPI routes
│       │   ├── routes/    # Endpoint modules
│       │   └── deps.py    # Dependencies (auth, db)
│       ├── services/      # Business logic
│       │   ├── curation.py
│       │   └── summarization.py
│       ├── adapters/      # Platform adapters
│       │   ├── base.py    # Abstract adapter
│       │   ├── x.py
│       │   ├── youtube.py
│       │   ├── reddit.py
│       │   └── email.py
│       ├── models/        # Pydantic + SQLAlchemy
│       ├── tasks/         # Celery tasks
│       └── core/          # Config, security
├── tests/
├── frontend/              # Next.js dashboard
├── .env.example
├── pyproject.toml
└── docker-compose.yml
```

## Code Conventions
- Use `uv` for dependency management
- Type hints required on all functions
- Pydantic models for API request/response
- Async functions for all I/O
- Environment variables via `.env` (pydantic-settings)

## API Keys Required
```env
# X API (Bot account @briefly3000)
X_BEARER_TOKEN=

# AI Summarization
GROK_API_KEY=           # Primary
OPENAI_API_KEY=         # Fallback

# Infrastructure
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=postgresql://...

# Future platforms
YOUTUBE_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
```

## Rate Limits (X Free/Basic)
- User lookup: 100 requests/24h (Free), 10K/mo (Basic)
- List tweets: 1 request/15 min (Free), varies (Basic)
- List members add: ~300/15 min
- **Strategy**: Batch users, throttle aggressively, cache IDs

## Compliance Notes
- X: No follows.read, no automated DMs/replies/posts
- All platforms: Off-platform curation only
- Privacy: Encrypt stored sources, no data sharing

## Human Tasks (Greg)
- [x] Create @briefly3000 X account
- [x] Register X Developer App
- [ ] Update @briefly3000 bio (see PRD)
- [ ] Provide Bearer Token
- [ ] Get Grok API key (or confirm OpenAI fallback)
- [ ] Set up hosting preference

## Development Phases
1. **Phase 1**: X adapter + basic API + CLI testing
2. **Phase 2**: Web dashboard (source management + briefing view)
3. **Phase 3**: YouTube/Reddit adapters + Grok polish
4. **Phase 4**: iOS app + voice readout
