# n8n Workflows for Briefly 3000

This directory contains n8n workflow JSON exports for media extraction and briefing generation.

## Directory Structure

```
n8n-workflows/
├── sub/                    # Shared sub-workflows (import first)
│   └── sub-progress-report.json
├── adapters/               # Platform-specific extraction
│   ├── adapter-x.json
│   ├── adapter-youtube.json
│   └── adapter-podcast.json
├── orchestrator/           # High-level coordination
│   └── orchestrator-briefing-ondemand.json
├── delivery/               # Output channels (future)
└── scheduler/              # Timing & orchestration (future)
```

## Import Order

Import workflows in this order to ensure dependencies are satisfied:

1. `sub/` - Shared sub-workflows
2. `adapters/` - Platform adapters
3. `orchestrator/` - Orchestration workflows
4. `delivery/` - Delivery workflows
5. `scheduler/` - Scheduler workflows

## Required Credentials

Configure these credentials in n8n before running workflows:

| Credential Name | Type | Description |
|----------------|------|-------------|
| `Briefly API Auth` | HTTP Header Auth | API key for Briefly backend |
| `X API OAuth` | OAuth2 | Twitter/X API bearer token |
| `YouTube API Key` | HTTP Query Auth | YouTube Data API key |
| `Taddy API Key` | HTTP Header Auth | Taddy podcast API key |

## Environment Variables

Set these environment variables in n8n:

```
BRIEFLY_API_URL=http://localhost:8000
TADDY_USER_ID=your-taddy-user-id
```

## Workflow Descriptions

### Sub-Workflows

- **sub-progress-report**: Reports extraction progress to Briefly API via `POST /api/n8n/progress`

### Adapters

- **adapter-x**: Fetches tweets from X/Twitter with rate limit handling and credential rotation
  - Trigger: Every 15 minutes (cron) or on-demand
  - Rate limits: Tracks remaining calls, rotates credentials when exhausted

- **adapter-youtube**: Fetches recent videos from YouTube channels
  - Trigger: Every 30 minutes (cron) or on-demand
  - Gets channel uploads playlist, filters by recency

- **adapter-podcast**: Fetches podcast episodes via Taddy API
  - Trigger: Every 1 hour (cron) or on-demand
  - Queues episodes for transcription if no transcript available

### Orchestrators

- **orchestrator-briefing-ondemand**: Webhook-triggered extraction for specific platforms
  - Endpoint: `POST /webhook/briefing/ondemand`
  - Body: `{ "platforms": ["x", "youtube"], "hours_back": 24, "user_id": "..." }`

## API Endpoints Required

The Briefly API must implement these endpoints:

```
GET  /api/sources?platform={x|youtube|podcast}&active_only=true
POST /api/content/ingest
POST /api/content/transcribe
POST /api/n8n/progress
```

## Testing

1. Import all workflows in order
2. Configure credentials
3. Run `sub-progress-report` manually with test data
4. Run individual adapters with test sources
5. Trigger `orchestrator-briefing-ondemand` via webhook
