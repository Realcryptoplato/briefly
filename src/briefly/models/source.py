"""Source model - accounts/channels users want to follow."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from sqlalchemy import String, DateTime, ForeignKey, Enum as SQLEnum, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from briefly.core.database import Base

if TYPE_CHECKING:
    from briefly.models.user import User


class Platform(str, Enum):
    """Supported platforms."""
    X = "x"
    YOUTUBE = "youtube"
    REDDIT = "reddit"
    EMAIL = "email"


class Source(Base):
    """A source the user wants to monitor (X account, YouTube channel, etc.)."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    platform: Mapped[Platform] = mapped_column(SQLEnum(Platform))

    # Platform-specific identifier
    # X: username (e.g., "elonmusk")
    # YouTube: channel ID or handle
    # Reddit: subreddit name
    # Email: sender email or newsletter name
    identifier: Mapped[str] = mapped_column(String(255), index=True)

    # Cached platform ID (e.g., X user ID)
    platform_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Display name (fetched from platform)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Is this source active?
    is_active: Mapped[bool] = mapped_column(default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="sources")
