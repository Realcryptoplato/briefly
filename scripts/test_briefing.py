#!/usr/bin/env python3
"""
CLI test script for Briefly 3000 curation pipeline.

Usage:
    uv run python scripts/test_briefing.py elonmusk naval paulg
    uv run python scripts/test_briefing.py --hours 48 elonmusk
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from briefly.services.curation import CurationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


async def main():
    parser = argparse.ArgumentParser(
        description="Test Briefly 3000 curation pipeline"
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="X usernames to curate from (without @)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours back to look for content (default: 24)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("BRIEFLY 3000 - Test Briefing")
    print(f"{'='*60}")
    print(f"Sources: {', '.join(args.sources)}")
    print(f"Time range: Last {args.hours} hours")
    print(f"{'='*60}\n")

    service = CurationService()

    try:
        result = await service.create_briefing(
            x_sources=args.sources,
            hours_back=args.hours,
        )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print_formatted_result(result)

    except Exception as e:
        logging.exception("Briefing failed")
        print(f"\nError: {e}")
        sys.exit(1)


def print_formatted_result(result: dict):
    """Pretty print the briefing result."""
    print("\n📋 BRIEFING SUMMARY")
    print("-" * 40)
    print(result["summary"])

    print(f"\n\n📊 STATS")
    print("-" * 40)
    stats = result["stats"]
    print(f"  Sources: {stats.get('sources', {})}")
    print(f"  Items fetched: {stats.get('items_fetched', 0)}")
    print(f"  Time range: {stats.get('time_range_hours', 0)} hours")

    if result.get("items"):
        print(f"\n\n🔝 TOP POSTS ({len(result['items'])})")
        print("-" * 40)
        for i, item in enumerate(result["items"][:10], 1):
            metrics = item.get("metrics", {})
            print(f"\n{i}. @{item['source']} (score: {item['score']:.0f})")
            print(f"   {item['content'][:200]}...")
            print(f"   ❤️ {metrics.get('like_count', 0)} | 🔁 {metrics.get('retweet_count', 0)}")
            print(f"   🔗 {item['url']}")

    if result.get("recommendations"):
        print(f"\n\n💡 RECOMMENDED ACCOUNTS")
        print("-" * 40)
        for rec in result["recommendations"]:
            print(f"  @{rec['username']}: {rec['reason']}")

    print("\n")


if __name__ == "__main__":
    asyncio.run(main())
