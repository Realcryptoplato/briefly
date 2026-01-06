"""Grok adapter for X content via xAI API.

Uses Grok's built-in X search capability to summarize account activity,
bypassing X API rate limits entirely.

IMPORTANT: Must use the Responses API with x_search tool to get real-time data.
Without tools, Grok will hallucinate based on training data.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from openai import OpenAI

from briefly.adapters.base import BaseAdapter, ContentItem
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)


class GrokAdapter(BaseAdapter):
    """
    Grok-powered X content adapter.

    Instead of using the X API directly (which has severe rate limits on Free tier),
    this adapter asks Grok to summarize X accounts using its built-in x_search tool.

    CRITICAL: Uses the xAI Responses API (/v1/responses) with x_search tool.
    The standard chat completions API does NOT support x_search.

    Benefits:
    - No X API rate limits
    - Returns summarized content (better for briefings)
    - Can handle many accounts in a single request
    """

    platform_name = "x"

    def __init__(self) -> None:
        self._settings = get_settings()
        # OpenAI client for simple queries (no search needed)
        self._client = OpenAI(
            api_key=self._settings.xai_api_key,
            base_url=self._settings.xai_base_url,
        )
        # HTTP client for Responses API (search tools)
        self._http_client = httpx.AsyncClient(
            base_url=self._settings.xai_base_url,
            headers={
                "Authorization": f"Bearer {self._settings.xai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    async def _call_responses_api(
        self,
        prompt: str,
        tools: list[dict],
        model: str = "grok-4-1-fast",  # Only grok-4 family supports server-side tools
    ) -> str:
        """
        Call xAI Responses API with search tools.

        This is the correct API for x_search - chat completions doesn't support it.
        """
        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
            "tools": tools,
        }

        response = await self._http_client.post("/responses", json=payload)
        response.raise_for_status()

        data = response.json()

        # Extract text from the response output
        # Response structure: output is a list with tool calls and messages
        # We want the message item with type="message" containing output_text
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                content = item.get("content", [])
                for content_item in content:
                    if content_item.get("type") == "output_text":
                        return content_item.get("text", "")

        # Fallback: try to find any text content in old format
        for item in output:
            if item.get("type") == "text":
                return item.get("text", "")

        # Last resort
        logger.warning(f"Could not parse xAI response: {data}")
        return "No response text found"

    async def lookup_user(self, identifier: str) -> dict[str, Any] | None:
        """
        Look up X user via Grok.

        Note: This is a simplified lookup - Grok confirms if user exists
        but doesn't return detailed metadata like the X API would.
        """
        username = identifier.lstrip("@")
        try:
            response = self._client.chat.completions.create(
                model="grok-3-latest",
                messages=[
                    {
                        "role": "user",
                        "content": f"Does the X/Twitter account @{username} exist? Reply with just 'yes' or 'no' followed by the account's display name if it exists.",
                    }
                ],
            )
            result = response.choices[0].message.content.lower()
            if result.startswith("yes"):
                # Extract name if provided
                name = result.replace("yes", "").strip(" -,:")
                return {
                    "id": username,  # We don't get real ID from Grok
                    "username": username,
                    "name": name or username,
                }
            return None
        except Exception as e:
            logger.error(f"Grok lookup failed for {identifier}: {e}")
            return None

    def _get_x_search_tool(
        self,
        usernames: list[str] | None = None,
        hours: int = 24,
    ) -> dict:
        """Build x_search tool configuration for Responses API."""
        # Calculate date range
        # Note: to_date is exclusive in X search, so add 1 day to include today
        end_date = datetime.now() + timedelta(days=1)
        start_date = datetime.now() - timedelta(hours=hours)

        tool = {
            "type": "x_search",
            "from_date": start_date.strftime("%Y-%m-%d"),
            "to_date": end_date.strftime("%Y-%m-%d"),
        }

        # Filter to specific handles if provided (max 10)
        if usernames:
            clean_usernames = [u.lstrip("@") for u in usernames[:10]]
            tool["allowed_x_handles"] = clean_usernames

        return tool

    async def _verify_no_posts(self, username: str, hours: int) -> dict[str, Any]:
        """
        Verify if an account truly has no posts in the given time period.

        When Grok says "no posts", this does a more direct search to confirm.
        """
        start_date = datetime.now() - timedelta(hours=hours)
        prompt = f"""Search X for ANY posts from @{username} since {start_date.strftime('%Y-%m-%d')}.
List them even if they seem minor or unimportant.
If there are truly no posts, confirm explicitly with "CONFIRMED: No posts found"."""

        try:
            result = await self._call_responses_api(
                prompt=prompt,
                tools=[self._get_x_search_tool([username], hours)],
            )
            has_posts = "confirmed: no posts" not in result.lower()
            return {"has_posts": has_posts, "verification_result": result}
        except Exception as e:
            logger.warning(f"No-post verification failed for @{username}: {e}")
            return {"has_posts": False, "error": str(e)}

    async def summarize_account(
        self,
        username: str,
        hours: int = 24,
        focus: str | None = None,
    ) -> dict[str, Any]:
        """
        Get a summary of an X account's recent activity.

        Uses x_search tool via Responses API to fetch REAL posts from X.

        Args:
            username: X username (with or without @)
            hours: How many hours back to look (default 24)
            focus: Optional focus area (e.g., "AI news", "crypto", "tech")

        Returns:
            Dict with summary, key_posts, and metadata
        """
        username = username.lstrip("@")
        focus_clause = f" Focus on {focus}." if focus else ""

        prompt = f"""Search X for posts from @{username} in the last {hours} hours.{focus_clause}

For each significant topic they discussed, extract:

**KEY ALPHA & TAKEAWAYS** (most important - what's the actual news/insight?)
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
- [Key quote or summary] (XXK likes, X.XM views) [link if available]

Format your response as:

### @{username}

**KEY ALPHA**
- [Specific insight] - [brief context/implication]
- [Another insight] - [why it matters]

**NOTABLE**
- "[Quote]" (XXK likes, X.XM views) [link]
- "[Quote]" (XXK likes) [link]

Keep it dense and actionable. No fluff, no separate "engagement highlights" section.

If they haven't posted in that time period, say "No posts found in last {hours}h" - do NOT guess or use old data."""

        try:
            summary = await self._call_responses_api(
                prompt=prompt,
                tools=[self._get_x_search_tool([username], hours)],
            )

            # Verify if Grok claims "no posts"
            if "no posts" in summary.lower() or "hasn't posted" in summary.lower():
                verify_result = await self._verify_no_posts(username, hours)
                if verify_result.get("has_posts"):
                    # Re-run with stricter prompt
                    logger.info(f"Re-running summary for @{username} after verification found posts")
                    summary = await self._call_responses_api(
                        prompt=f"Search X for ANY posts from @{username} in the last {hours} hours. "
                               f"List them with their key points and engagement stats, even if minor.",
                        tools=[self._get_x_search_tool([username], hours)],
                    )

            return {
                "username": username,
                "summary": summary,
                "hours": hours,
                "focus": focus,
                "model": "grok-4-1-fast",
                "fetched_at": datetime.now().isoformat(),
                "used_x_search": True,
            }
        except Exception as e:
            logger.error(f"Grok summarize failed for @{username}: {e}")
            return {
                "username": username,
                "summary": f"Failed to fetch summary: {e}",
                "error": str(e),
            }

    async def summarize_accounts_batch(
        self,
        usernames: list[str],
        hours: int = 24,
        focus: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Summarize multiple X accounts in a single request.

        More efficient than individual calls - Grok can summarize
        multiple accounts in one response.
        """
        if not usernames:
            return []

        clean_usernames = [u.lstrip("@") for u in usernames]
        accounts_str = ", ".join(f"@{u}" for u in clean_usernames)
        focus_clause = f" Focus on {focus}." if focus else ""

        prompt = f"""Search X for posts from these accounts in the last {hours} hours: {accounts_str}{focus_clause}

For EACH account, extract:

## @username

**KEY ALPHA & TAKEAWAYS**
- [Specific insight/claim] - [why it matters]
- [Another specific insight] - [implications]
(Focus on actionable info, not just "discussed topic X")

**NOTABLE POSTS** (2-3 max, with inline stats)
- "[Quote or summary]" (XXK likes) [link if available]

Example of GOOD extraction:
- "Tesla FSD will surpass human safety by Q2 2026" - claims 10x improvement in edge case handling
- "xAI purchased 380MW of gas turbines" - scaling compute infrastructure aggressively

Example of BAD extraction (too vague):
- "Discussed Tesla and AI progress"
- "Talked about company growth"

If an account has no posts in this period, state "No posts in last {hours}h" - do not fabricate.

Keep each account summary dense and actionable. No separate "engagement highlights" section - stats go inline with notable posts."""

        try:
            content = await self._call_responses_api(
                prompt=prompt,
                tools=[self._get_x_search_tool(clean_usernames, hours)],
            )

            return [{
                "usernames": clean_usernames,
                "combined_summary": content,
                "hours": hours,
                "focus": focus,
                "model": "grok-4-1-fast",
                "fetched_at": datetime.now().isoformat(),
                "used_x_search": True,
            }]
        except Exception as e:
            logger.error(f"Grok batch summarize failed: {e}")
            return [{
                "usernames": clean_usernames,
                "combined_summary": f"Failed to fetch summaries: {e}",
                "error": str(e),
            }]

    async def search_topic(
        self,
        topic: str,
        hours: int = 24,
        accounts: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Search X for a topic, optionally filtered to specific accounts.

        Args:
            topic: Topic or keywords to search
            hours: How many hours back to search
            accounts: Optional list of accounts to filter to

        Returns:
            Dict with summary and key posts
        """
        accounts_clause = ""
        if accounts:
            clean = [u.lstrip("@") for u in accounts]
            accounts_clause = f" from accounts: {', '.join(f'@{u}' for u in clean)}"

        prompt = f"""Search X for posts about "{topic}" from the last {hours} hours{accounts_clause}.

Provide:
1. Key themes and perspectives
2. Notable posts and who posted them
3. Any trending discussions or debates
4. Overall sentiment around this topic"""

        try:
            summary = await self._call_responses_api(
                prompt=prompt,
                tools=[self._get_x_search_tool(accounts, hours)],
            )

            return {
                "topic": topic,
                "summary": summary,
                "hours": hours,
                "accounts": accounts,
                "model": "grok-4-1-fast",
                "fetched_at": datetime.now().isoformat(),
                "used_x_search": True,
            }
        except Exception as e:
            logger.error(f"Grok topic search failed: {e}")
            return {
                "topic": topic,
                "summary": f"Failed to search topic: {e}",
                "error": str(e),
            }

    async def search_accounts(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for X accounts matching a query.

        Uses Grok's x_search tool to find relevant accounts.

        Args:
            query: Search query (e.g., "AI researchers", "crypto influencers")
            limit: Maximum number of accounts to return (default 10)

        Returns:
            List of account dicts with username, name, bio, followers, verified
        """
        prompt = f"""Search X for accounts matching "{query}".
Return up to {limit} accounts as a JSON array with fields:
- username (without @)
- name (display name)
- bio (short description, max 100 chars)
- approximate_followers (e.g., "1.2M", "50K", "10K")
- verified (true/false)

Return ONLY a valid JSON array, no other text. Example format:
[
  {{"username": "elonmusk", "name": "Elon Musk", "bio": "CEO of Tesla, SpaceX, xAI", "approximate_followers": "200M", "verified": true}},
  {{"username": "sama", "name": "Sam Altman", "bio": "CEO of OpenAI", "approximate_followers": "3.5M", "verified": true}}
]

Focus on influential accounts with significant followings that are relevant to "{query}"."""

        try:
            result = await self._call_responses_api(
                prompt=prompt,
                tools=[self._get_x_search_tool(None, 24)],  # No handle filter for discovery
            )

            # Parse JSON from response
            import json
            import re

            # Try to extract JSON array from response
            json_match = re.search(r'\[[\s\S]*\]', result)
            if json_match:
                try:
                    accounts = json.loads(json_match.group())
                    return accounts[:limit]
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse JSON from Grok response: {result[:200]}")

            # Fallback: return empty list if JSON parsing fails
            logger.warning(f"Could not extract account list from Grok response")
            return []

        except Exception as e:
            logger.error(f"Grok account search failed for '{query}': {e}")
            return []

    async def fetch_content(
        self,
        identifiers: list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> list[ContentItem]:
        """
        Fetch summarized content from X accounts.

        Note: This returns summarized content, not individual tweets.
        The ContentItem will contain a summary rather than raw tweet text.
        For briefing generation, this is actually more useful.
        """
        if not identifiers:
            return []

        hours = max(1, int((end_time - start_time).total_seconds() / 3600))

        # Batch summarize all accounts
        results = await self.summarize_accounts_batch(identifiers, hours=hours)

        items = []
        for result in results:
            if "error" not in result:
                # Create a single ContentItem with the combined summary
                items.append(
                    ContentItem(
                        platform="x",
                        platform_id=f"grok-summary-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        source_identifier=",".join(result.get("usernames", [])),
                        source_name="Grok Summary",
                        content=result.get("combined_summary", ""),
                        url=None,
                        metrics={"accounts_count": len(result.get("usernames", []))},
                        posted_at=datetime.now(),
                    )
                )

        return items


# Singleton instance
_grok_adapter: GrokAdapter | None = None


def get_grok_adapter() -> GrokAdapter:
    """Get the Grok adapter singleton."""
    global _grok_adapter
    if _grok_adapter is None:
        _grok_adapter = GrokAdapter()
    return _grok_adapter
