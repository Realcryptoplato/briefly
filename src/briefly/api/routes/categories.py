"""Category management for source grouping."""

import json
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pathlib import Path

router = APIRouter()

CATEGORIES_FILE = Path(__file__).parent.parent.parent.parent.parent / ".cache" / "categories.json"


def _load_categories() -> list:
    """Load categories from file."""
    if CATEGORIES_FILE.exists():
        return json.loads(CATEGORIES_FILE.read_text())
    return []


def _save_categories(categories: list):
    """Save categories to file."""
    CATEGORIES_FILE.parent.mkdir(exist_ok=True)
    CATEGORIES_FILE.write_text(json.dumps(categories, indent=2))


class CreateCategoryRequest(BaseModel):
    name: str
    description: str | None = None
    color: str = "#6366f1"  # Default indigo
    sources: dict = {}  # {"x": ["user1"], "youtube": ["channel1"]}
    tags: list[str] = []  # For AI-driven categorization hints


class UpdateCategoryRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    sources: dict | None = None
    tags: list[str] | None = None


@router.get("")
async def list_categories() -> list:
    """List all categories."""
    return _load_categories()


@router.post("")
async def create_category(req: CreateCategoryRequest) -> dict:
    """Create a new category."""
    categories = _load_categories()

    # Check for duplicate name
    if any(c["name"].lower() == req.name.lower() for c in categories):
        raise HTTPException(400, f"Category '{req.name}' already exists")

    category = {
        "id": str(uuid4())[:8],
        "name": req.name,
        "description": req.description,
        "color": req.color,
        "sources": req.sources,
        "tags": req.tags,
    }

    categories.append(category)
    _save_categories(categories)

    return category


@router.get("/{category_id}")
async def get_category(category_id: str) -> dict:
    """Get a specific category."""
    categories = _load_categories()
    for cat in categories:
        if cat["id"] == category_id:
            return cat
    raise HTTPException(404, "Category not found")


@router.put("/{category_id}")
async def update_category(category_id: str, req: UpdateCategoryRequest) -> dict:
    """Update a category."""
    categories = _load_categories()

    for i, cat in enumerate(categories):
        if cat["id"] == category_id:
            if req.name is not None:
                cat["name"] = req.name
            if req.description is not None:
                cat["description"] = req.description
            if req.color is not None:
                cat["color"] = req.color
            if req.sources is not None:
                cat["sources"] = req.sources
            if req.tags is not None:
                cat["tags"] = req.tags

            categories[i] = cat
            _save_categories(categories)
            return cat

    raise HTTPException(404, "Category not found")


@router.delete("/{category_id}")
async def delete_category(category_id: str) -> dict:
    """Delete a category."""
    categories = _load_categories()

    for i, cat in enumerate(categories):
        if cat["id"] == category_id:
            deleted = categories.pop(i)
            _save_categories(categories)
            return {"status": "deleted", "category": deleted}

    raise HTTPException(404, "Category not found")


@router.post("/{category_id}/sources")
async def add_source_to_category(category_id: str, platform: str, identifier: str) -> dict:
    """Add a source to a category."""
    categories = _load_categories()

    for i, cat in enumerate(categories):
        if cat["id"] == category_id:
            if platform not in cat["sources"]:
                cat["sources"][platform] = []

            if identifier not in cat["sources"][platform]:
                cat["sources"][platform].append(identifier)
                categories[i] = cat
                _save_categories(categories)

            return cat

    raise HTTPException(404, "Category not found")


@router.delete("/{category_id}/sources/{platform}/{identifier}")
async def remove_source_from_category(category_id: str, platform: str, identifier: str) -> dict:
    """Remove a source from a category."""
    categories = _load_categories()

    for i, cat in enumerate(categories):
        if cat["id"] == category_id:
            if platform in cat["sources"] and identifier in cat["sources"][platform]:
                cat["sources"][platform].remove(identifier)
                categories[i] = cat
                _save_categories(categories)
            return cat

    raise HTTPException(404, "Category not found")


# Preset categories that can be created
PRESET_CATEGORIES = [
    {
        "name": "Mainstream News",
        "description": "Major news outlets and mainstream media",
        "color": "#3b82f6",  # Blue
        "tags": ["news", "mainstream", "breaking"],
    },
    {
        "name": "Crypto & Web3",
        "description": "Cryptocurrency, blockchain, and Web3 content",
        "color": "#f59e0b",  # Amber
        "tags": ["crypto", "bitcoin", "ethereum", "web3", "defi"],
    },
    {
        "name": "Tech & AI",
        "description": "Technology, artificial intelligence, and innovation",
        "color": "#8b5cf6",  # Purple
        "tags": ["tech", "ai", "ml", "startups", "innovation"],
    },
    {
        "name": "Finance & Markets",
        "description": "Financial news, stock markets, and economic analysis",
        "color": "#10b981",  # Emerald
        "tags": ["finance", "stocks", "markets", "economy", "investing"],
    },
    {
        "name": "Politics",
        "description": "Political commentary and news",
        "color": "#ef4444",  # Red
        "tags": ["politics", "policy", "government"],
    },
]


@router.get("/presets/list")
async def list_preset_categories() -> list:
    """List available preset category templates."""
    return PRESET_CATEGORIES


@router.post("/presets/create/{preset_name}")
async def create_from_preset(preset_name: str) -> dict:
    """Create a category from a preset template."""
    preset = next((p for p in PRESET_CATEGORIES if p["name"].lower() == preset_name.lower()), None)

    if not preset:
        raise HTTPException(404, f"Preset '{preset_name}' not found")

    # Check if already exists
    categories = _load_categories()
    if any(c["name"].lower() == preset["name"].lower() for c in categories):
        raise HTTPException(400, f"Category '{preset['name']}' already exists")

    category = {
        "id": str(uuid4())[:8],
        "name": preset["name"],
        "description": preset["description"],
        "color": preset["color"],
        "sources": {"x": [], "youtube": []},
        "tags": preset["tags"],
    }

    categories.append(category)
    _save_categories(categories)

    return category
