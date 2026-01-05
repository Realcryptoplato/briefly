# PRD: Atomic Briefing Display

## Overview

Ensure all UI components (summary, top posts, stats, items) are atomic with the selected briefing. Currently, components may show data from different briefings or stale state.

## Problem Statement

When a user:
1. Generates a new briefing
2. Views historical briefings
3. Refreshes the page

The UI components (summary, top posts, stats) may not all correspond to the same briefing, creating a confusing, inconsistent experience.

## Current Issues

1. **Top Posts not tied to briefing**: Shows items that may not match the displayed summary
2. **Stats from wrong briefing**: Item counts don't match visible content
3. **Stale data on refresh**: Components load independently, may get different data
4. **No briefing selector**: Can't view historical briefings

## Goals

1. All displayed data comes from ONE selected briefing
2. Briefing selector to view history
3. Clear indication of which briefing is displayed
4. Atomic state updates when switching briefings

## Solution Design

### 1. Briefing as Single Source of Truth

```javascript
// Current (broken)
latestBriefing: null,  // Summary comes from here
// But top posts might come from somewhere else

// Fixed
selectedBriefing: null,  // ALL data comes from this single object
briefingHistory: [],     // List of available briefings
```

### 2. Briefing Selector Component

```
┌─────────────────────────────────────────────────────────┐
│ 📋 Briefing: Jan 5, 2026 9:05 AM ▼                     │
│ ┌─────────────────────────────────────────────────────┐│
│ │ ● Jan 5, 2026 9:05 AM (current)                    ││
│ │   Jan 5, 2026 2:12 AM                              ││
│ │   Jan 4, 2026 6:30 PM                              ││
│ │   Jan 4, 2026 10:15 AM                             ││
│ └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

### 3. Atomic Data Structure

Each briefing contains ALL its data:

```json
{
  "id": "briefing_123",
  "job_id": "job_456",
  "generated_at": "2026-01-05T09:05:00Z",
  "summary": "# Daily Briefing...",
  "items": [
    { "platform": "youtube", "content": "...", "score": 14481 },
    { "platform": "x", "content": "...", "score": 8932 }
  ],
  "stats": {
    "sources": { "x": 3, "youtube": 100 },
    "items_fetched": { "x": 45, "youtube": 312 }
  },
  "recommendations": []
}
```

### 4. State Management

```javascript
// Alpine.js data
{
  briefings: [],           // All available briefings
  selectedBriefingId: null, // Currently selected

  // Computed - all derived from selected briefing
  get currentBriefing() {
    return this.briefings.find(b => b.id === this.selectedBriefingId);
  },
  get topPosts() {
    return this.currentBriefing?.items?.slice(0, 8) || [];
  },
  get stats() {
    return this.currentBriefing?.stats || {};
  },
  get summary() {
    return this.currentBriefing?.summary || '';
  },

  // Actions
  selectBriefing(id) {
    this.selectedBriefingId = id;
    // Everything updates automatically via computed properties
  }
}
```

## Implementation

### Frontend Changes

#### 1. Update dashboard.html State

```javascript
// Replace
latestBriefing: null,

// With
briefings: [],
selectedBriefingId: null,

async loadBriefings() {
  const resp = await fetch('/api/briefings');
  this.briefings = await resp.json();
  if (this.briefings.length > 0) {
    this.selectedBriefingId = this.briefings[0].id || this.briefings[0].job_id;
  }
},

get currentBriefing() {
  if (!this.selectedBriefingId) return null;
  return this.briefings.find(b =>
    b.id === this.selectedBriefingId || b.job_id === this.selectedBriefingId
  );
}
```

#### 2. Add Briefing Selector

```html
<!-- Add after header -->
<div class="mb-4" x-show="briefings.length > 0">
  <label class="text-sm text-gray-400">Viewing Briefing:</label>
  <select
    x-model="selectedBriefingId"
    class="bg-gray-700 border border-gray-600 rounded px-3 py-1 text-sm"
  >
    <template x-for="b in briefings" :key="b.job_id">
      <option :value="b.job_id" x-text="new Date(b.generated_at).toLocaleString()"></option>
    </template>
  </select>
</div>
```

#### 3. Update All Data Bindings

Replace all `latestBriefing` references with `currentBriefing`:

```html
<!-- Before -->
<template x-if="latestBriefing">
  <span x-text="latestBriefing.stats?.items_fetched?.x"></span>
</template>

<!-- After -->
<template x-if="currentBriefing">
  <span x-text="currentBriefing.stats?.items_fetched?.x"></span>
</template>
```

#### 4. Top Posts from Current Briefing

```html
<!-- Before: Might show items from wrong briefing -->
<template x-for="item in latestBriefing.items.slice(0, 8)">

<!-- After: Always from selected briefing -->
<template x-for="item in (currentBriefing?.items || []).slice(0, 8)">
```

### Backend Changes

#### 1. Ensure Briefing ID Consistency

In `briefings.py`, ensure each briefing has a unique ID:

```python
result["id"] = result.get("job_id") or datetime.now().strftime("%Y%m%d_%H%M%S")
```

#### 2. GET /api/briefings Returns Full Objects

Already returns list of briefings - ensure each has complete data:

```python
@router.get("")
async def list_briefings() -> list:
    """List recent briefings with full data."""
    return _load_briefings()  # Each should have summary, items, stats
```

## Migration

1. No data migration needed - briefings already stored as complete objects
2. Frontend-only changes
3. Backwards compatible

## Testing Checklist

- [ ] Generate new briefing → All components show new data
- [ ] Select old briefing → All components update atomically
- [ ] Refresh page → Shows most recent briefing consistently
- [ ] During generation → Shows "generating" state, then updates all at once
- [ ] Empty state → Shows appropriate message

## Files to Modify

- `src/briefly/api/templates/dashboard.html` - State management overhaul
- `src/briefly/api/routes/briefings.py` - Ensure ID consistency (minor)

## Success Criteria

1. Switching briefings updates ALL components simultaneously
2. No mixed data from different briefings ever displayed
3. Stats always match visible content
4. Top posts always from currently selected briefing
