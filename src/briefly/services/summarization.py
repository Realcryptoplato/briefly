"""AI summarization service using Grok or local LLM."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from openai import AsyncOpenAI

from briefly.adapters.base import ContentItem
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)

# User settings file for runtime config
USER_SETTINGS_FILE = Path(__file__).parent.parent.parent.parent / ".cache" / "user_settings.json"


def _get_local_llm_config() -> dict | None:
    """Get local LLM config from user settings if enabled."""
    if USER_SETTINGS_FILE.exists():
        try:
            settings = json.loads(USER_SETTINGS_FILE.read_text())
            local_llm = settings.get("local_llm", {})
            if local_llm.get("enabled"):
                return local_llm
        except Exception:
            pass
    return None


class SummarizationService:
    """Generate briefing summaries using Grok (xAI) or local LLM."""

    def __init__(self) -> None:
        env_settings = get_settings()

        # Check user settings for local LLM config (runtime configurable)
        local_llm = _get_local_llm_config()

        # Use local LLM if enabled in user settings or env
        if local_llm:
            logger.info(f"Using local LLM at {local_llm['base_url']}")
            self._client = AsyncOpenAI(
                api_key=local_llm.get("api_key", "not-needed"),
                base_url=local_llm["base_url"],
            )
            self._model = local_llm.get("model", "local-model")
            self._provider = "local"
        elif env_settings.local_llm_enabled:
            logger.info(f"Using local LLM at {env_settings.local_llm_base_url}")
            self._client = AsyncOpenAI(
                api_key=env_settings.local_llm_api_key,
                base_url=env_settings.local_llm_base_url,
            )
            self._model = env_settings.local_llm_model
            self._provider = "local"
        else:
            logger.info("Using xAI/Grok for summarization")
            self._client = AsyncOpenAI(
                api_key=env_settings.xai_api_key,
                base_url=env_settings.xai_base_url,
            )
            self._model = env_settings.xai_model
            self._provider = "xai"

    async def summarize_content(
        self,
        items: list[ContentItem],
        max_items: int = 20,
    ) -> str:
        """
        Generate a natural language summary of content items.

        Args:
            items: List of content items (should be pre-sorted by score)
            max_items: Maximum items to include in summary

        Returns:
            AI-generated summary text
        """
        if not items:
            return "No new content to summarize."

        # Adjust limits based on provider (local LLMs have smaller context)
        if self._provider == "local":
            # Local LLMs typically have 4-8k context, be conservative
            effective_max_items = min(max_items, 10)
            max_chars_per_item = 500
            max_total_chars = 8000
        else:
            # Cloud LLMs (Grok, GPT-4) have much larger context
            effective_max_items = max_items
            max_chars_per_item = 2000
            max_total_chars = 40000

        # Take top items
        top_items = items[:effective_max_items]

        # Format content for the prompt with character limits
        content_text = self._format_items_for_prompt(
            top_items,
            max_chars_per_item=max_chars_per_item,
            max_total_chars=max_total_chars,
        )

        # Count items by platform for context
        platform_counts = {}
        for item in top_items:
            platform_counts[item.platform] = platform_counts.get(item.platform, 0) + 1

        platform_summary = ", ".join(f"{count} {p}" for p, count in platform_counts.items())

        now = datetime.now()
        date_header = now.strftime("%A, %B %d, %Y at %I:%M %p")

        # Identify breaking/new content (posted in last 6 hours)
        breaking_cutoff = now - timedelta(hours=6)
        breaking_items = [item for item in top_items if item.posted_at and item.posted_at.replace(tzinfo=None) > breaking_cutoff]
        breaking_note = f"\n\nNOTE: {len(breaking_items)} items are from the last 6 hours - highlight these as BREAKING/NEW." if breaking_items else ""

        prompt = f"""You are Briefly 3000, an AI executive assistant that creates personalized media briefings.

Today's Date: {date_header}
Sources: {platform_summary} items{breaking_note}

Analyze the following content from X posts, YouTube videos, and podcast episodes. Create a comprehensive briefing.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:

# Daily Briefing - {now.strftime("%B %d, %Y")}

## Breaking / Just In (if any recent items)
[Content posted in last 6 hours - mark with time like "2h ago". Skip section if nothing recent]

## Top Stories
[2-3 most important/trending topics with key insights]

## By Category
Group all related content (from ANY platform) into themed sections like:
- **Tech & AI**: [items about technology from X, YouTube, and podcasts together]
- **Business & Markets**: [business news from all sources]
- **Culture & Media**: [entertainment, social trends]
- **Science & Health**: [if relevant]
[Add/remove categories based on actual content. Include source attribution.]

## Source Highlights
Brief notable items by platform:
- **X**: [key tweets with @username]
- **YouTube**: [notable videos with channel names]
- **Podcasts**: [episode highlights with show names]

## Quick Hits
[Bullet points of other noteworthy items not covered above]

---

Guidelines:
- Put BREAKING/NEW content first if posted in last 6 hours
- Group related content by THEME across all platforms (not by platform)
- Highlight key quotes or insights with attribution
- Note any breaking news or time-sensitive content
- Keep it professional but conversational
- Always include the source (@username, channel name, or podcast name)
- For podcasts, mention the show name and episode topic

Content to summarize:
{content_text}

Create a briefing that a busy professional can scan in 2-3 minutes."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are Briefly 3000, an AI executive assistant that creates comprehensive daily media briefings."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=2500,  # Increased for longer structured format
            )

            summary = response.choices[0].message.content
            logger.info(f"Generated summary ({len(summary)} chars) from {len(top_items)} items using {self._provider}")
            return summary

        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            # Provide more context on the error
            error_msg = str(e)
            if "context" in error_msg.lower() or "token" in error_msg.lower():
                return f"Summary generation failed: Content too long for LLM context. Try reducing sources or using a cloud LLM. Error: {error_msg}"
            return f"Summary generation failed: {error_msg}"

    async def generate_recommendations(
        self,
        items: list[ContentItem],
        current_sources: list[str],
        max_recommendations: int = 5,
    ) -> list[dict]:
        """
        Suggest new accounts to follow based on content themes.

        Args:
            items: Content items to analyze
            current_sources: Already-followed sources
            max_recommendations: Max recommendations to return

        Returns:
            List of dicts with 'username' and 'reason' keys
        """
        if not items:
            return []

        content_sample = self._format_items_for_prompt(items[:10])

        prompt = f"""Based on the following X posts, suggest {max_recommendations} new accounts the user might want to follow.

Current sources (already following): {', '.join(current_sources)}

Content themes from current sources:
{content_sample}

For each suggestion, provide:
1. Username (real X accounts that exist and are active)
2. Brief reason why they'd be relevant

Respond in JSON format:
[{{"username": "example", "reason": "Brief explanation"}}]

Only suggest accounts NOT in the current sources list."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=500,
            )

            # Parse JSON from response
            import json
            text = response.choices[0].message.content
            # Extract JSON if wrapped in markdown
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            recommendations = json.loads(text.strip())
            return recommendations[:max_recommendations]

        except Exception as e:
            logger.error(f"Recommendation generation failed: {e}")
            return []

    def _format_items_for_prompt(
        self,
        items: list[ContentItem],
        max_chars_per_item: int = 2000,
        max_total_chars: int = 40000,
    ) -> str:
        """Format content items for LLM prompt with character limits."""
        lines = []
        total_chars = 0

        for i, item in enumerate(items, 1):
            # Get metrics string based on platform
            if item.platform == "youtube":
                metrics_str = f"(views: {item.metrics.get('view_count', 0):,})"
            elif item.platform == "podcast":
                duration = item.metrics.get('duration_seconds', 0)
                duration_str = f"{duration // 60}min" if duration else ""
                has_transcript = "has transcript" if item.metrics.get('has_transcript') else "no transcript"
                metrics_str = f"(podcast episode, {duration_str}, {has_transcript})"
            else:
                metrics_str = f"(likes: {item.metrics.get('like_count', 0)}, RTs: {item.metrics.get('retweet_count', 0)})"

            # Truncate content if needed
            content = item.content or ""
            # Strip out [AI Summary] section for brevity - just use description
            if "[AI Summary]:" in content:
                content = content.split("[AI Summary]:")[0].strip()
            if "[Transcript" in content:
                content = content.split("[Transcript")[0].strip()

            if len(content) > max_chars_per_item:
                content = content[:max_chars_per_item] + "..."

            line = f"{i}. @{item.source_identifier} [{item.platform}]: {content} {metrics_str}"

            # Check if adding this would exceed total limit
            if total_chars + len(line) > max_total_chars:
                lines.append(f"... and {len(items) - i + 1} more items (truncated for context limit)")
                break

            lines.append(line)
            total_chars += len(line) + 2  # +2 for newlines

        return "\n\n".join(lines)
