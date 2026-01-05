# Briefly 3000

AI-powered media curation and daily briefings. The year 3000 called — they want their executive assistant back.

## Quick Start

### 1. Clone and Install

```bash
git clone https://github.com/Realcryptoplato/briefly.git
cd briefly
uv sync
```

### 2. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
# Required: X API, xAI API key
# Optional: OpenAI (for embeddings), YouTube API
```

### 3. Run the Server

```bash
# Development mode (SQLite, no database setup needed)
uv run uvicorn briefly.api.main:app --reload --port 8000
```

Open http://localhost:8000 to access the dashboard.

## Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

| Variable | Required | Description |
|----------|----------|-------------|
| `X_API_KEY` | Yes | X/Twitter API key |
| `X_API_KEY_SECRET` | Yes | X/Twitter API secret |
| `X_BEARER_TOKEN` | Yes | X/Twitter bearer token |
| `X_ACCESS_TOKEN` | Yes | X/Twitter access token |
| `X_ACCESS_TOKEN_SECRET` | Yes | X/Twitter access token secret |
| `XAI_API_KEY` | Yes | xAI API key for Grok |
| `OPENAI_API_KEY` | No | OpenAI key for embeddings |
| `DATABASE_URL` | No | PostgreSQL URL (defaults to SQLite) |

## Database Options

### Local Development (Default)

No configuration needed. Uses SQLite automatically.

### PostgreSQL (Production)

```bash
# Start local PostgreSQL with Docker
docker-compose up -d

# Set database URL
export DATABASE_URL="postgresql://briefly:briefly3000@localhost:5436/briefly"
```

### VPS/Production Database

```bash
# Get password from secure storage (1Password, etc.)
export VPS_DB_PASSWORD="<your-password>"
export DATABASE_URL="postgresql://briefly:${VPS_DB_PASSWORD}@100.101.168.91:5435/briefly"
```

## Project Structure

```
briefly/
├── src/briefly/           # Python source code
│   ├── api/               # FastAPI routes
│   ├── core/              # Configuration
│   └── services/          # Business logic
├── n8n-workflows/         # n8n workflow definitions
├── infrastructure/        # Docker configs for VPS
└── docs/                  # Documentation
```

## Development

```bash
# Run tests
uv run pytest

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
```

## Security Notes

- Never commit `.env` files (already in `.gitignore`)
- Store production passwords in secure storage (1Password, etc.)
- Use environment variables for all secrets
- The settings panel in the dashboard shows connection status without exposing passwords

## License

MIT
