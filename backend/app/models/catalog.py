"""Card catalogue reference data: sets and variants.

These are *reference* tables. ``cards`` keeps a denormalised copy of the set and
variant names so a card is never unreadable if its reference row is deleted, and
so cards can be created from free text before the catalogue is populated.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import JSON, Boolean, Date, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.enums import Language
from app.models.base import Base, TimestampMixin, enum_check, pk_column


class CardSet(Base, TimestampMixin):
    __tablename__ = "sets"
    __table_args__ = (
        UniqueConstraint("code", "language", name="uq_sets_code_language"),
        enum_check("language", Language),
    )

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    series: Mapped[str | None] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(32), nullable=False, default=Language.ENGLISH.value)
    release_date: Mapped[date | None] = mapped_column(Date)
    total_cards: Mapped[int | None] = mapped_column(Integer)
    secret_cards: Mapped[int | None] = mapped_column(Integer)
    external_ids: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)


class CardVariant(Base, TimestampMixin):
    """Suggestion list for ``cards.variant``.

    Variant matters enormously for comparables — an Alt Art and a Reverse Holo of
    the same number are different markets — so it is a real row rather than free
    text, but users can add their own.
    """

    __tablename__ = "card_variants"

    id: Mapped[str] = pk_column()
    code: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
