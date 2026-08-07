"""SQLAlchemy models for the FastAPI backend."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tunnel(Base):
    """Persistent record of a PublicHost tunnel."""

    __tablename__ = "tunnels"

    id: Mapped[int] = mapped_column(primary_key=True)
    subdomain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True, unique=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    frontend_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="opened")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    listener_port: Mapped[int | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_tunnels_subdomain_token", "subdomain", "token"),
        Index("ix_tunnels_status_created", "status", "created_at"),
    )
