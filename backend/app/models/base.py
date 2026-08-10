"""Declarative base, shared column types and mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.enums import StrEnum

# Predictable constraint/index names keep Alembic migrations stable on SQLite,
# which cannot ALTER a constraint it cannot name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def as_dict(self) -> dict[str, Any]:  # pragma: no cover - debugging helper
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def pk_column() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def enum_check(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """A CHECK constraint restricting ``column`` to the enum's values."""
    allowed = ", ".join(f"'{value}'" for value in enum.values())
    return CheckConstraint(f"{column} IN ({allowed})", name=name or f"{column}_valid")


def money_column(nullable: bool = True, default: int | None = None) -> Mapped[int | None]:
    """An integer count of minor units. See ``app.money`` for the rationale."""
    return mapped_column(Integer, nullable=nullable, default=default)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
