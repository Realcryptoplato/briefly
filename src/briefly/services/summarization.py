"""AI summarization service using Grok."""

import logging
from openai import AsyncOpenAI

from briefly.adapters.base import ContentItem
from briefly.core.config import get_settings

logger = logging.getLogger(__name__)


class SummarizationService:
    """Generate briefing summaries using Grok (xAI)."""

    def __init__(self) -> None:
        settings = get_settings()
        # Grok uses OpenAI-compatible API
        self._client = AsyncOpenAI(
            api_key=settings.xai_api_key,
            base_url=settings.xai_base_url,
        )
        self._model = settings.xai_model

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

        # Take top items
        top_items = items[:max_items]

        # Format content for the prompt
        content_text = self._format_items_for_prompt(top_items)

        prompt = f"""You are Briefly 3000, an AI executive assistant that creates personalized media briefings.

Analyze the following posts from X (Twitter) and create a concise, scannable daily briefing.

Guidelines:
- Lead with the most important/trending topics
- Group related posts by theme
- Highlight key quotes or insights
- Note any breaking news or time-sensitive content
- Keep it professional but conversational
- Use bullet points for scannability
- Include the source username when quoting

Content to summarize:
{content_text}

Create a briefing that a busy professional can scan in 2 minutes."""

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "You are Briefly 3000, an AI executive assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=1500,
            )

            summary = response.choices[0].message.content
            logger.info(f"Generated summary ({len(summary)} chars) from {len(top_items)} items")
            return summary

        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"Summary generation failed: {str(e)}"

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

    def _format_items_for_prompt(self, items: list[ContentItem]) -> str:
        """Format content items for LLM prompt."""
        lines = []
        for i, item in enumerate(items, 1):
            metrics_str = f"(likes: {item.metrics.get('like_count', 0)}, RTs: {item.metrics.get('retweet_count', 0)})"
            lines.append(
                f"{i}. @{item.source_identifier}: {item.content} {metrics_str}"
            )
        return "\n\n".join(lines)
