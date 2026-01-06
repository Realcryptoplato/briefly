# PRD: Search & Prompting Improvements

## One-Liner for Cloud Agent

```
Implement source search (X/YouTube/podcasts) with typeahead UI, fix Grok prompting to extract actionable alpha/takeaways instead of just topics, and collapse engagement stats into notable posts section. See docs/PRD-search-and-prompting.md for full spec.
```

---

## Overview

Three improvements to Briefly 3000:
1. **Source Search**: Add search functionality for discovering X accounts, YouTube channels, and podcasts
2. **Better X Prompting**: Extract actionable insights ("alpha") not just topic summaries
3. **Cleaner Output Format**: Collapse engagement stats into notable posts

---

## 1. Source Search System

### Requirements

Add search capability to discover and add sources without knowing exact handles/URLs.

### API Endpoints

```
GET /api/search/x?q=<query>
  - Search X for accounts matching query
  - Returns: [{username, name, bio, followers, verified}]

GET /api/search/youtube?q=<query>
  - Search YouTube for channels
  - Returns: [{channel_id, name, description, subscribers, thumbnail}]

GET /api/search/podcasts?q=<query>
  - Search podcasts via iTunes Search API (free, no key needed)
  - Returns: [{name, author, feed_url, artwork, description}]
```

### Implementation

**X Account Search** (via Grok):
```python
# In grok.py - add method
async def search_accounts(self, query: str, limit: int = 10) -> list[dict]:
    """Search for X accounts matching query."""
    prompt = f"""Search X for accounts matching "{query}".
    Return up to {limit} accounts as a JSON array with fields:
    - username (without @)
    - name (display name)
    - bio (short description)
    - approximate_followers (e.g., "1.2M", "50K")
    - verified (true/false)

    Return ONLY valid JSON array, no other text."""

    # Use x_search tool with no handle filter
    ...
```

**YouTube Channel Search** (via YouTube Data API):
```python
# In youtube.py - add method
async def search_channels(self, query: str, limit: int = 10) -> list[dict]:
    """Search for YouTube channels."""
    # Use YouTube Data API search endpoint
    # GET https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={query}
    ...
```

**Podcast Search** (via iTunes):
```python
# New file: adapters/podcast_search.py
import httpx

async def search_podcasts(query: str, limit: int = 10) -> list[dict]:
    """Search podcasts via iTunes Search API (free, no auth)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "media": "podcast",
                "limit": limit,
            }
        )
        data = resp.json()
        return [
            {
                "name": r["collectionName"],
                "author": r["artistName"],
                "feed_url": r["feedUrl"],
                "artwork": r["artworkUrl600"],
                "description": r.get("description", ""),
            }
            for r in data.get("results", [])
        ]
```

### Dashboard UI

Add search modal with typeahead:

```html
<!-- Search Modal -->
<div x-show="showSearchModal" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
    <div class="bg-gray-800 rounded-lg p-6 w-full max-w-lg">
        <h3 class="text-xl font-bold mb-4">Search Sources</h3>

        <!-- Platform tabs -->
        <div class="flex gap-2 mb-4">
            <button @click="searchPlatform = 'x'"
                    :class="searchPlatform === 'x' ? 'bg-cyan-600' : 'bg-gray-700'"
                    class="px-3 py-1 rounded">X</button>
            <button @click="searchPlatform = 'youtube'"
                    :class="searchPlatform === 'youtube' ? 'bg-cyan-600' : 'bg-gray-700'"
                    class="px-3 py-1 rounded">YouTube</button>
            <button @click="searchPlatform = 'podcasts'"
                    :class="searchPlatform === 'podcasts' ? 'bg-cyan-600' : 'bg-gray-700'"
                    class="px-3 py-1 rounded">Podcasts</button>
        </div>

        <!-- Search input with debounce -->
        <input type="text"
               x-model="searchQuery"
               @input.debounce.300ms="performSearch()"
               placeholder="Search..."
               class="w-full bg-gray-700 rounded px-4 py-2 mb-4">

        <!-- Results list -->
        <div class="max-h-64 overflow-y-auto space-y-2">
            <template x-for="result in searchResults">
                <div class="flex items-center justify-between p-3 bg-gray-700 rounded hover:bg-gray-600 cursor-pointer"
                     @click="addFromSearch(result)">
                    <div>
                        <div class="font-medium" x-text="result.name"></div>
                        <div class="text-sm text-gray-400" x-text="result.description || result.bio"></div>
                    </div>
                    <button class="text-cyan-400 hover:text-cyan-300">+ Add</button>
                </div>
            </template>
        </div>

        <button @click="showSearchModal = false" class="mt-4 text-gray-400">Close</button>
    </div>
</div>
```

---

## 2. Improved X Prompting

### Problem

Current prompt extracts topics but not actionable insights. Example of bad output:
```
1. Key topics/themes
   - Government fraud and welfare abuse
   - AI and tech advancements
```

This tells us WHAT was discussed but not the actual takeaway/alpha.

### Solution

New prompt that extracts the "so what" - the actual insight or news:

```python
# In grok.py - update summarize prompts

ACCOUNT_SUMMARY_PROMPT = """Search X for posts from @{username} in the last {hours} hours.{focus_clause}

For each significant topic they discussed, extract:

**KEY INSIGHTS & ALPHA** (most important - what's the actual news/takeaway?)
For each topic, provide:
- The specific claim, announcement, or insight (not just "discussed AI")
- Why it matters / implications
- Include specific numbers, names, or details when available

Example of GOOD extraction:
- "Tesla FSD will surpass human safety by Q2 2026" - claims 10x improvement in edge case handling
- "xAI purchased 380MW of gas turbines from Doosan" - scaling compute infrastructure aggressively

Example of BAD extraction (too vague):
- "Discussed Tesla and AI progress"
- "Talked about company growth"

**NOTABLE POSTS** (with engagement stats inline)
List 2-3 most significant posts with format:
- [Key quote or summary] (XXK likes, X.XM views) [link]

If they haven't posted in that time period, say "No posts found" - do NOT guess or use old data."""
```

### Batch Summary Prompt

```python
BATCH_SUMMARY_PROMPT = """Search X for posts from these accounts in the last {hours} hours: {accounts_str}{focus_clause}

For EACH account, extract:

## @username

**KEY ALPHA & TAKEAWAYS**
- [Specific insight/claim] - [why it matters]
- [Another specific insight] - [implications]
(Focus on actionable info, not just "discussed topic X")

**NOTABLE POSTS** (2-3 max, with inline stats)
- "[Quote or summary]" (XXK likes) [link]

If an account has no posts in this period, state "No posts in last {hours}h" - do not fabricate."""
```

### Validation

Add check to detect when Grok might be hallucinating "no posts":

```python
async def summarize_account(self, username: str, hours: int = 24, ...):
    result = await self._call_responses_api(...)

    # If says "no posts", do a verification search
    if "no posts" in result.lower() or "hasn't posted" in result.lower():
        # Try a more direct search query
        verify_result = await self._verify_no_posts(username, hours)
        if verify_result.get("has_posts"):
            # Re-run with stricter prompt
            result = await self._call_responses_api(
                prompt=f"Search X for ANY posts from @{username} since {start_date}. "
                       f"List them even if they seem minor. If truly none, confirm explicitly.",
                tools=[self._get_x_search_tool([username], hours)],
            )

    return result
```

---

## 3. Output Format Changes

### Current Format (verbose)

```
### @elonmusk

1. Key topics/themes
   - Government fraud...
   - AI advancements...

2. Notable posts or announcements
   - Post about X...
   - Post about Y...

3. Engagement highlights
   - Viral: Post A (161K likes, 35K reposts)
   - High controversy: Post B...
```

### New Format (compact, actionable)

```
### @elonmusk

**KEY ALPHA**
- Tesla FSD approaching human-level safety by Q2 - "easy to get to 99%, super hard to solve long tail"
- xAI scaling aggressively: bought 380MW gas turbines for GPU clusters
- Grok hit 30M MAU (+20% MoM), now #1 in Spain

**NOTABLE**
- "Half a million dollars a month stolen by a single fake store" - SNAP fraud exposé (161K likes, 8.4M views) [link]
- Defended X Chat encryption against critics (24K likes) [link]
```

### Prompt Update

Add formatting instruction:

```python
FORMAT_INSTRUCTION = """
Format your response as:

### @username

**KEY ALPHA**
- [Specific insight] - [brief context/implication]
- [Another insight] - [why it matters]

**NOTABLE**
- "[Quote]" (XXK likes, X.XM views) [link]
- "[Quote]" (XXK likes) [link]

Keep it dense and actionable. No fluff, no "engagement highlights" section.
"""
```

---

## 4. Files to Modify

| File | Changes |
|------|---------|
| `src/briefly/adapters/grok.py` | Update prompts, add `search_accounts()`, add no-post verification |
| `src/briefly/adapters/youtube.py` | Add `search_channels()` method |
| `src/briefly/adapters/podcast_search.py` | NEW - iTunes search integration |
| `src/briefly/api/routes/search.py` | NEW - Search API endpoints |
| `src/briefly/api/main.py` | Register search router |
| `src/briefly/api/templates/dashboard.html` | Add search modal UI |

---

## 5. Implementation Order

1. **Prompting fixes** (highest impact, fastest)
   - Update `ACCOUNT_SUMMARY_PROMPT` and `BATCH_SUMMARY_PROMPT` in grok.py
   - Add no-post verification logic
   - Test with @sama and @elonmusk

2. **Search APIs**
   - Create `podcast_search.py` with iTunes integration
   - Add `search_accounts()` to grok.py
   - Add `search_channels()` to youtube.py
   - Create `/api/search/*` routes

3. **Dashboard UI**
   - Add search modal component
   - Wire up typeahead with debounce
   - Add "Search" button to each source panel

---

## 6. Success Criteria

- [ ] Searching "AI podcasts" returns relevant results from iTunes
- [ ] Searching "tech influencers" on X returns accounts like @elonmusk, @sama
- [ ] Searching "tech review" on YouTube returns channels like MKBHD
- [ ] X summaries contain specific claims/numbers, not just topics
- [ ] @sama (or any active account) correctly shows posts, not "no posts"
- [ ] Output format is compact with inline engagement stats

---

## Appendix: iTunes Search API

Free, no auth required:

```bash
curl "https://itunes.apple.com/search?term=tech+podcast&media=podcast&limit=5"
```

Returns:
```json
{
  "results": [
    {
      "collectionName": "Lex Fridman Podcast",
      "artistName": "Lex Fridman",
      "feedUrl": "https://lexfridman.com/feed/podcast/",
      "artworkUrl600": "https://...",
      "trackCount": 400
    }
  ]
}
```
