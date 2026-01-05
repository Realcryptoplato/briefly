# PRD: Rich UI with Thumbnails and Drill-Down

## Overview

Transform the current primitive markdown-only dashboard into a rich, blog-like interface with thumbnails, graphics, and hierarchical drill-down capabilities powered by vector search.

## Current State

- Plain text/markdown briefings
- No thumbnails or visual hierarchy
- Links go directly to external sources
- No way to drill deeper into topics
- Static display with no interactivity

## Goals

1. **Visual richness**: Thumbnails, platform icons, engagement metrics visualization
2. **Hierarchical navigation**: Drill into topics via vector search breadcrumbs
3. **Blog-like reading experience**: Cards, sections, expandable content
4. **Dynamic exploration**: Click a topic → see related content from all sources

## Features

### 1. Content Cards

Replace plain text items with rich cards:

```
┌─────────────────────────────────────────────────────────┐
│ [Thumbnail] │ Platform Icon │ Source Name    │ 2h ago │
├─────────────────────────────────────────────────────────┤
│ Title: How Maduro's Capture Changes Everything         │
│                                                         │
│ AI Summary (truncated): Venezuela's capture may have   │
│ caught everyone's attention, but we are a world at...  │
│                                                         │
│ [▶ 203K views] [❤️ 11K] [💬 1.2K]  [📝 Transcript]     │
│                                                         │
│ Tags: [Venezuela] [Geopolitics] [Oil] [Trump]          │
│                                                         │
│ [View Original →] [Drill Down ↓] [Related Content 🔍]  │
└─────────────────────────────────────────────────────────┘
```

### 2. Drill-Down Navigation

Clicking "Drill Down" or a tag triggers vector search:

```
Breadcrumb: Home > Venezuela > Oil Markets > Price Impact

┌─────────────────────────────────────────────────────────┐
│ 🔍 Exploring: "Venezuela oil price impact"              │
│                                                         │
│ Found 12 related items across your sources:            │
│                                                         │
│ [Card 1: YouTube - Oil market analysis...]             │
│ [Card 2: X post - @analyst on crude prices...]         │
│ [Card 3: Podcast - Energy sector discussion...]        │
└─────────────────────────────────────────────────────────┘
```

### 3. Briefing Sections

Structure briefings visually:

```
📰 BREAKING (last 6h)
├── [Rich Card 1]
└── [Rich Card 2]

📊 TOP STORIES
├── [Rich Card 3]
├── [Rich Card 4]
└── [Rich Card 5]

🏷️ BY CATEGORY
├── Geopolitics & World Affairs
│   └── [Expandable section with cards]
├── Crypto & Markets
│   └── [Expandable section with cards]
└── Tech & AI
    └── [Expandable section with cards]
```

### 4. Thumbnail Sources

- **YouTube**: Use `https://img.youtube.com/vi/{video_id}/mqdefault.jpg`
- **X/Twitter**: Extract media URLs from tweet data
- **Podcasts**: Use podcast artwork or generate placeholder
- **Fallback**: Platform-specific default images

### 5. Vector Search Integration

**API Endpoint**: `POST /api/search`

**Breadcrumb URL Pattern**:
```
/explore?q=venezuela+oil&context=briefing_123&depth=2
```

Parameters:
- `q`: Search query (from tag or drill-down)
- `context`: Parent briefing/item ID for relevance
- `depth`: Navigation depth for breadcrumb

**Response includes**:
- Semantic search results
- Suggested related queries
- Breadcrumb trail

## Technical Implementation

### Frontend Changes

1. **Component Library**: Use existing Tailwind + Alpine.js
2. **Card Component**: Reusable content card with all states
3. **Drill-Down Modal**: Full-screen or sidebar for exploration
4. **Breadcrumb Component**: Clickable navigation trail
5. **Lazy Loading**: Load thumbnails on scroll

### Backend Changes

1. **Thumbnail Proxy**: `/api/thumbnails/{platform}/{id}` to avoid CORS
2. **Search Enhancement**: Add `context` parameter for relevance boosting
3. **Briefing Structure**: Return structured sections, not just markdown

### Data Model Updates

```python
class BriefingSection:
    title: str
    type: str  # "breaking", "top_stories", "category"
    items: list[ContentItem]

class ContentItem:
    # Existing fields plus:
    thumbnail_url: str | None
    tags: list[str]
    drill_down_query: str  # Pre-computed search query
```

## API Changes

### GET /api/briefings/{id}

Returns structured briefing:

```json
{
  "id": "briefing_123",
  "generated_at": "2026-01-05T...",
  "sections": [
    {
      "title": "Breaking",
      "type": "breaking",
      "items": [...]
    }
  ],
  "summary_html": "<rendered markdown>",
  "tags": ["venezuela", "crypto", "ai"]
}
```

### GET /api/explore

Vector search with context:

```json
{
  "query": "venezuela oil impact",
  "context_id": "briefing_123",
  "results": [...],
  "breadcrumb": ["Home", "Venezuela", "Oil Markets"],
  "suggested_queries": ["crude prices", "opec response"]
}
```

## Success Metrics

- User engagement time increases
- Drill-down usage rate
- Reduced bounce rate from briefings
- Vector search query volume

## Implementation Phases

### Phase 1: Rich Cards
- Thumbnail display for YouTube
- Platform icons and metrics
- Basic card layout

### Phase 2: Structured Sections
- Breaking/Top Stories/Categories layout
- Expandable sections
- Tag display

### Phase 3: Drill-Down
- Vector search integration
- Breadcrumb navigation
- Related content suggestions

### Phase 4: Polish
- Lazy loading
- Animations
- Mobile responsiveness

## Dependencies

- Vector store must be populated (existing)
- Search endpoint must work (existing at `/api/search`)
- Briefing structure needs backend update

## Files to Modify

- `src/briefly/api/templates/dashboard.html` - Major overhaul
- `src/briefly/api/routes/briefings.py` - Structured responses
- `src/briefly/api/routes/search.py` - Context-aware search
- `src/briefly/services/curation.py` - Tag extraction, thumbnail URLs
