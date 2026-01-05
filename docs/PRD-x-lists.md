# PRD: X Lists Integration for Efficient API Usage

## Overview

Replace individual user timeline fetching with X Lists to dramatically reduce API calls and avoid rate limits. Instead of fetching N user timelines (N API calls), fetch 1 list timeline (1 API call).

## Problem Statement

Current approach:
- Each X source = 1 API call to user timeline
- 100 sources = 100 API calls
- Quickly hits rate limits (900 second cooldown)
- Slow, sequential fetching

X Lists solution:
- Add users to a private list (once)
- Fetch list timeline = ALL users' tweets in 1 call
- Massive reduction in API usage
- Much faster briefing generation

## X API Rate Limits

| Endpoint | Rate Limit (per 15 min) |
|----------|------------------------|
| User Timeline | 900 (app) / 180 (user) |
| List Timeline | 900 (app) / 180 (user) |
| List Members Add | 300 |
| List Members Remove | 300 |

**Key insight**: Fetching 1 list with 100 members = 1 API call, not 100.

## Solution Design

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Briefly Dashboard                     │
│  [Add @user] → Stored locally + added to X List         │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   X Lists Manager                        │
│  - Creates private list "briefly_sources"               │
│  - Manages list membership (respects rate limits)       │
│  - Syncs local sources ↔ list members                   │
└─────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                   X Adapter (Updated)                    │
│  - fetch_content() → fetches LIST timeline              │
│  - Falls back to individual if list unavailable         │
│  - Single API call for all sources                      │
└─────────────────────────────────────────────────────────┘
```

### Bot Account Setup

**Account**: `@briefly3000` (already configured in .env)

**List Configuration**:
- Name: `briefly_sources` (private)
- Description: "Briefly 3000 curated sources"
- Private: Yes (not visible to others)

### List Management Flow

#### Adding a Source

```
User clicks "Add @elonmusk"
    │
    ▼
Save to local sources.json
    │
    ▼
Queue for list addition
    │
    ▼
Background job adds to X List
(respects 300/15min rate limit)
    │
    ▼
Mark as "synced" in sources.json
```

#### Fetching Content

```
Generate Briefing clicked
    │
    ▼
Check if list exists and is synced
    │
    ├─ Yes: Fetch list timeline (1 API call)
    │
    └─ No: Fall back to individual timelines
           (current behavior)
```

## Implementation

### 1. X List Service

```python
# src/briefly/services/x_lists.py

class XListManager:
    """Manages X Lists for efficient timeline fetching."""

    def __init__(self):
        self.list_name = "briefly_sources"
        self.list_id: str | None = None

    async def ensure_list_exists(self) -> str:
        """Create list if it doesn't exist, return list ID."""
        # Check for existing list
        lists = await self._get_owned_lists()
        for lst in lists:
            if lst["name"] == self.list_name:
                self.list_id = lst["id"]
                return self.list_id

        # Create new private list
        self.list_id = await self._create_list(
            name=self.list_name,
            description="Briefly 3000 curated sources",
            private=True
        )
        return self.list_id

    async def add_member(self, username: str) -> bool:
        """Add user to list. Returns False if rate limited."""
        user_id = await self._get_user_id(username)
        if not user_id:
            return False

        try:
            await self._add_list_member(self.list_id, user_id)
            return True
        except RateLimitError:
            return False

    async def remove_member(self, username: str) -> bool:
        """Remove user from list."""
        user_id = await self._get_user_id(username)
        if not user_id:
            return False

        await self._remove_list_member(self.list_id, user_id)
        return True

    async def get_list_timeline(
        self,
        start_time: datetime,
        end_time: datetime,
        max_results: int = 100
    ) -> list[ContentItem]:
        """Fetch all tweets from list members in time range."""
        # Single API call gets ALL members' tweets
        tweets = await self._fetch_list_tweets(
            list_id=self.list_id,
            start_time=start_time,
            end_time=end_time,
            max_results=max_results
        )
        return [self._tweet_to_content_item(t) for t in tweets]

    async def sync_sources(self, sources: list[str]) -> dict:
        """Sync local sources with list membership."""
        current_members = await self._get_list_members()
        current_usernames = {m["username"].lower() for m in current_members}
        target_usernames = {s.lower().lstrip("@") for s in sources}

        to_add = target_usernames - current_usernames
        to_remove = current_usernames - target_usernames

        added = []
        removed = []
        failed = []

        for username in to_add:
            if await self.add_member(username):
                added.append(username)
            else:
                failed.append(username)

        for username in to_remove:
            if await self.remove_member(username):
                removed.append(username)

        return {
            "added": added,
            "removed": removed,
            "failed": failed,
            "total_members": len(target_usernames) - len(failed)
        }
```

### 2. Update X Adapter

```python
# src/briefly/adapters/x.py

class XAdapter:
    def __init__(self):
        self._list_manager = XListManager()
        self._use_lists = True  # Feature flag

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """Fetch content, preferring list timeline."""

        if self._use_lists:
            try:
                # Ensure list is synced
                await self._list_manager.ensure_list_exists()
                await self._list_manager.sync_sources(identifiers)

                # Single API call for all sources
                return await self._list_manager.get_list_timeline(
                    start_time=start_time,
                    end_time=end_time
                )
            except Exception as e:
                logger.warning(f"List fetch failed, falling back: {e}")

        # Fallback to individual timelines
        return await self._fetch_individual_timelines(
            identifiers, start_time, end_time
        )
```

### 3. Source Sync Status

Update sources.json structure:

```json
{
  "x": [
    {
      "identifier": "elonmusk",
      "added_at": "2026-01-05T...",
      "list_synced": true,
      "list_sync_error": null
    }
  ],
  "x_list_id": "1234567890",
  "x_list_last_sync": "2026-01-05T..."
}
```

### 4. Background Sync Job

```python
# Periodic job to sync sources with list
async def sync_x_list_job():
    """Run every 5 minutes to sync pending sources."""
    sources = load_sources()
    x_sources = [s["identifier"] for s in sources.get("x", [])]

    manager = XListManager()
    await manager.ensure_list_exists()
    result = await manager.sync_sources(x_sources)

    # Update sync status
    sources["x_list_last_sync"] = datetime.now().isoformat()
    save_sources(sources)

    return result
```

## API Endpoints

### POST /api/sources/x/sync

Manually trigger list sync:

```json
{
  "action": "sync",
  "result": {
    "added": ["newuser1", "newuser2"],
    "removed": [],
    "failed": [],
    "total_members": 45
  }
}
```

### GET /api/sources/x/list-status

Check list sync status:

```json
{
  "list_id": "1234567890",
  "list_name": "briefly_sources",
  "member_count": 45,
  "last_sync": "2026-01-05T09:00:00Z",
  "pending_adds": 2,
  "pending_removes": 0
}
```

## Rate Limit Handling

### Adding Members

```python
class ListMemberQueue:
    """Queue additions to respect rate limits."""

    async def queue_add(self, username: str):
        """Add to queue, process respecting limits."""
        self.pending.append({
            "username": username,
            "queued_at": datetime.now()
        })

    async def process_queue(self):
        """Process up to 300 additions per 15 minutes."""
        window_start = datetime.now() - timedelta(minutes=15)
        recent_adds = self.get_adds_since(window_start)

        available = 300 - len(recent_adds)
        to_process = self.pending[:available]

        for item in to_process:
            await self.manager.add_member(item["username"])
            self.pending.remove(item)
```

### Timeline Fetching

List timeline has same rate limit as user timeline (900/15min), but:
- 1 list call = ALL members' tweets
- vs 100 user calls = 100 API calls

**Effective rate limit improvement**: 100x for 100 sources

## Migration Plan

### Phase 1: List Infrastructure
- Create XListManager service
- Add list creation/member management
- Add sync status tracking

### Phase 2: Adapter Integration
- Update XAdapter to prefer lists
- Add fallback to individual timelines
- Add feature flag for rollback

### Phase 3: Background Sync
- Add periodic sync job
- Handle rate limit queuing
- Add manual sync endpoint

### Phase 4: Dashboard Integration
- Show list sync status
- Show pending/synced indicators on sources
- Add manual sync button

## Rollback Plan

Feature flag `USE_X_LISTS=false` reverts to individual timeline fetching.

## Files to Create/Modify

**New**:
- `src/briefly/services/x_lists.py` - List management service

**Modify**:
- `src/briefly/adapters/x.py` - Use list timeline
- `src/briefly/api/routes/sources.py` - Sync endpoints
- `.cache/sources.json` - Add sync status fields

## Success Metrics

- API calls reduced by 90%+ for X sources
- No more rate limit errors during normal operation
- Briefing generation time reduced significantly
- Source additions don't block on rate limits

## Security Considerations

- Bot account credentials in .env (already there)
- Private list not visible to public
- User IDs cached to reduce lookups
- Rate limit state persisted across restarts
