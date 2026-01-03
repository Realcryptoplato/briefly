"""Briefing model - generated content digests."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from sqlalchemy import String, Text, DateTime, ForeignKey, Enum as SQLEnum, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from briefly.core.database import Base

if TYPE_CHECKING:
    from briefly.models.user import User


class BriefingStatus(str, Enum):
    """Briefing generation status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Briefing(Base):
    """A generated briefing/digest for a user."""

    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    status: Mapped[BriefingStatus] = mapped_column(
        SQLEnum(BriefingStatus), default=BriefingStatus.PENDING
    )

    # Time range for content
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # AI-generated summary
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Embedding for semantic search (1536 dims for OpenAI, adjust for Grok)
    summary_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )

    # Raw stats
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Error message if failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="briefings")
    items: Mapped[list["BriefingItem"]] = relationship(
        back_populates="briefing", cascade="all, delete-orphan"
    )


class BriefingItem(Base):
    """Individual content item in a briefing."""

    __tablename__ = "briefing_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    briefing_id: Mapped[int] = mapped_column(ForeignKey("briefings.id"), index=True)

    # Source info
    platform: Mapped[str] = mapped_column(String(50))
    source_identifier: Mapped[str] = mapped_column(String(255))
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Content
    platform_id: Mapped[str] = mapped_column(String(255))  # Tweet ID, video ID, etc.
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Engagement metrics (platform-specific)
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Ranking score (computed)
    score: Mapped[float] = mapped_column(default=0.0)

    # Content embedding for semantic filtering
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1536), nullable=True
    )

    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Relationships
    briefing: Mapped["Briefing"] = relationship(back_populates="items")
