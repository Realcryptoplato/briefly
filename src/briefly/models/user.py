"""User model."""

from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from briefly.core.database import Base

if TYPE_CHECKING:
    from briefly.models.source import Source
    from briefly.models.briefing import Briefing


class User(Base):
    """User account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Preferences
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    briefing_time: Mapped[str] = mapped_column(String(5), default="08:00")  # HH:MM

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    sources: Mapped[list["Source"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    briefings: Mapped[list["Briefing"]] = relationship(back_populates="user", cascade="all, delete-orphan")
