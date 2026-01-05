# PRD: Phase 2 - Additional Media Sources

**Status**: Planning
**Priority**: High
**Dependencies**: Phase 1 pgvector memory (complete)

---

## Overview

Extend Briefly 3000 to ingest content from additional media sources beyond X and YouTube. This enables more comprehensive briefings by covering podcasts, blogs, news sites, and emerging social platforms.

## Research Summary

### 1. Podcasts

**Recommended Approach**: Taddy API + Whisper Fallback

#### Taddy API (Primary)
- **What**: Podcast search and metadata API with 4M+ podcasts, 180M+ episodes
- **URL**: https://taddy.org/developers
- **Pricing**:
  - Free: Basic API access, podcast-provided transcripts only
  - Pro ($75/mo): 100k requests, auto-transcripts for top 5000 podcasts
  - Business ($150/mo): 350k requests, on-demand transcription
- **Transcript Sources**:
  1. Podcast-provided transcripts (free, but <1% of podcasts have these)
  2. Auto-generated for top 5000 podcasts (Pro+)
  3. On-demand transcription ~10s per hour of audio (Pro+)
- **Features**:
  - Search podcasts/episodes with `filterForHasTranscript:true`
  - Transcript formats: plain text, with speakers, with timecodes
  - Episode metadata (title, description, duration, publish date)
  - RSS feed discovery
- **Integration**:
  ```python
  # Example: Fetch podcast episodes with transcripts
  async def fetch_podcast_episodes(podcast_id: str) -> list[ContentItem]:
      response = await taddy_client.get_episodes(podcast_id)
      for episode in response.episodes:
          if episode.transcript:
              yield ContentItem(
                  platform="podcast",
                  content=episode.transcript,
                  ...
              )
  ```

#### OpenAI Whisper (Fallback)
- **When**: Podcast doesn't have transcript in Taddy
- **Cost**: ~$0.006/minute of audio
- **Implementation**:
  1. Download audio file from RSS feed
  2. Chunk into segments (Whisper has 25MB limit)
  3. Transcribe with Whisper API
  4. Cache transcript for future use
- **Considerations**:
  - Only process high-engagement podcasts to control costs
  - Cache aggressively - podcasts don't change
  - Consider running Whisper locally for cost savings

#### Data Model
```python
class PodcastSource:
    podcast_id: str  # Taddy or RSS URL
    name: str
    author: str
    rss_url: str
    category: str  # e.g., "tech", "finance", "news"
```

---

### 2. RSS/Blog Feeds

**Recommended Approach**: feedparser + newspaper3k

#### Implementation
- **feedparser**: Parse RSS/Atom feeds, get article URLs
- **newspaper3k**: Extract article text from URLs (handles paywalls, ads)
- **readability-lxml**: Alternative for cleaner text extraction

#### Popular Tech/Finance Feeds
| Feed | URL | Category |
|------|-----|----------|
| Hacker News | https://news.ycombinator.com/rss | Tech |
| TechCrunch | https://techcrunch.com/feed/ | Tech |
| The Verge | https://www.theverge.com/rss/index.xml | Tech |
| Bloomberg | https://www.bloomberg.com/feed | Finance |
| CoinDesk | https://www.coindesk.com/arc/outboundfeeds/rss/ | Crypto |
| a]6z Blog | https://a16z.com/feed/ | VC/Tech |

#### Data Model
```python
class RSSSource:
    feed_url: str
    name: str
    category: str
    fetch_full_text: bool = True  # Use newspaper3k
    max_items: int = 10
```

---

### 3. Hacker News

**Recommended Approach**: Official HN API

#### Implementation
- **API**: https://hacker-news.firebaseio.com/v0/
- **Endpoints**:
  - `/topstories.json` - Top 500 story IDs
  - `/item/{id}.json` - Story details
  - `/user/{id}.json` - User info
- **Rate Limits**: None specified, be respectful

#### Fetching Strategy
1. Get top 100 stories from past 24h
2. Filter by score (>50 points) or comments (>20)
3. Fetch article text using newspaper3k
4. Store with HN metadata (score, comments, author)

#### Data Model
```python
class HNItem:
    hn_id: int
    title: str
    url: str | None
    text: str | None  # For "Ask HN" posts
    score: int
    comments: int
    author: str
    posted_at: datetime
```

---

### 4. Bluesky (AT Protocol)

**Recommended Approach**: atproto Python SDK

#### Implementation
- **SDK**: `atproto` package
- **Features**:
  - Follow specific accounts
  - Search posts by hashtag
  - Get feed algorithms
- **Authentication**: App password (not main password)

#### Fetching Strategy
1. Create list of Bluesky handles to follow
2. Fetch recent posts from followed accounts
3. Score by engagement (likes, reposts, replies)

#### Data Model
```python
class BlueskySource:
    handle: str  # e.g., "user.bsky.social"
    display_name: str
    category: str
```

---

### 5. Reddit

**Recommended Approach**: PRAW (Python Reddit API Wrapper)

#### Implementation
- **SDK**: `praw` package
- **Authentication**: OAuth2 app credentials
- **Rate Limits**: 60 requests/minute

#### Fetching Strategy
1. Subscribe to relevant subreddits
2. Fetch top/hot posts from past 24h
3. Include top comments for context
4. Filter by score threshold

#### Subreddit Categories
| Category | Subreddits |
|----------|------------|
| Tech | r/technology, r/programming, r/MachineLearning |
| Crypto | r/CryptoCurrency, r/Bitcoin, r/ethereum |
| Finance | r/wallstreetbets, r/stocks, r/investing |
| News | r/worldnews, r/news |

#### Data Model
```python
class RedditSource:
    subreddit: str
    category: str
    min_score: int = 100
    include_comments: bool = True
    max_comments: int = 5
```

---

## Implementation Plan

### Phase 2A: RSS Feeds (Week 1)
1. Create `RSSAdapter` with feedparser
2. Add article extraction with newspaper3k
3. Add RSS sources to dashboard UI
4. Update curation pipeline

### Phase 2B: Hacker News (Week 1)
1. Create `HNAdapter` with Firebase API
2. Implement scoring/filtering logic
3. Add HN as default "Tech News" category

### Phase 2C: Podcasts (Week 2)
1. Integrate Taddy API
2. Implement Whisper fallback for transcripts
3. Add podcast search to dashboard
4. Cache transcripts in vector store

### Phase 2D: Social Platforms (Week 3)
1. Create `BlueskyAdapter`
2. Create `RedditAdapter`
3. Add authentication flow for both
4. Update dashboard with new sources

---

## Configuration

### Environment Variables
```bash
# Podcasts
TADDY_API_KEY=xxx
OPENAI_API_KEY=xxx  # For Whisper (already set)

# Reddit
REDDIT_CLIENT_ID=xxx
REDDIT_CLIENT_SECRET=xxx
REDDIT_USER_AGENT="Briefly3000/1.0"

# Bluesky
BLUESKY_HANDLE=briefly.bsky.social
BLUESKY_APP_PASSWORD=xxx
```

### Settings UI
Add to settings panel:
- Toggle each platform on/off
- Configure default subreddits
- Set podcast search preferences
- Manage Bluesky follows

---

## API Design

### New Endpoints

```
# RSS
POST /api/sources/rss          - Add RSS feed
GET  /api/sources/rss          - List RSS sources
DELETE /api/sources/rss/{id}   - Remove RSS feed

# Podcasts
POST /api/sources/podcasts/search    - Search podcasts
POST /api/sources/podcasts           - Add podcast
GET  /api/sources/podcasts           - List podcasts
DELETE /api/sources/podcasts/{id}    - Remove podcast

# Hacker News
GET  /api/sources/hn/settings        - Get HN settings
PUT  /api/sources/hn/settings        - Update HN settings

# Bluesky
POST /api/sources/bluesky/connect    - Connect Bluesky account
POST /api/sources/bluesky/follow     - Add follow
GET  /api/sources/bluesky            - List follows

# Reddit
POST /api/sources/reddit/connect     - Connect Reddit
POST /api/sources/reddit/subscribe   - Add subreddit
GET  /api/sources/reddit             - List subreddits
```

---

## Success Metrics

- [ ] RSS feeds integrated with text extraction
- [ ] Hacker News top stories in briefings
- [ ] Podcast transcripts searchable
- [ ] Bluesky posts included
- [ ] Reddit threads summarized
- [ ] All sources in semantic search
- [ ] Dashboard UI for managing all sources

---

## Dependencies

```toml
# Add to pyproject.toml
feedparser = "^6.0"
newspaper3k = "^0.2"
praw = "^7.7"
atproto = "^0.0.46"
# taddy-api-client (or custom implementation)
```

---

## Notes

### Cost Considerations
- Whisper transcription: ~$0.006/min - budget for high-value podcasts only
- All other APIs are free or have generous free tiers
- Cache aggressively to minimize API calls

### Content Quality
- Implement engagement thresholds for all sources
- Use categories to filter relevant content
- AI-driven relevance scoring in summarization

### Privacy/ToS
- Respect rate limits
- Don't scrape private content
- Follow platform ToS for each source

---

*Created: 2026-01-03*
*Author: Claude Code*
