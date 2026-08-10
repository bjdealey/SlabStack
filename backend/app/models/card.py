"""Cards, their images, and collection groupings.

One row = one physical card, because copy A can grade a 10 and copy B an 8
(spec section 4). ``quantity`` exists for stacks of genuinely interchangeable
bulk, but any card entering a grading workflow must be split to quantity 1 —
see ``POST /api/cards/{id}/split``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums import CardStatus, Decision, GroupKind, ImageSide, Language, Printing, RawCondition
from app.models.base import Base, TimestampMixin, enum_check, money_column, pk_column, utcnow


class Card(Base, TimestampMixin):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint("quantity >= 1", name="quantity_positive"),
        enum_check("language", Language),
        enum_check("status", CardStatus),
        CheckConstraint(
            "decision_override IS NULL OR decision_override IN ("
            + ", ".join(f"'{value}'" for value in Decision.values())
            + ")",
            name="decision_override_valid",
        ),
        Index("ix_cards_set_number", "set_code", "card_number"),
        Index("ix_cards_catalog_key", "catalog_key"),
    )

    id: Mapped[str] = pk_column()

    # --- Identity ----------------------------------------------------------
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    set_id: Mapped[str | None] = mapped_column(ForeignKey("sets.id", ondelete="SET NULL"))
    set_name: Mapped[str | None] = mapped_column(String(160), index=True)
    set_code: Mapped[str | None] = mapped_column(String(32), index=True)
    card_number: Mapped[str | None] = mapped_column(String(32), index=True)
    variant_id: Mapped[str | None] = mapped_column(
        ForeignKey("card_variants.id", ondelete="SET NULL")
    )
    variant: Mapped[str | None] = mapped_column(String(80), index=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default=Language.ENGLISH.value)
    printing: Mapped[str | None] = mapped_column(String(48), default=Printing.UNLIMITED.value)
    rarity: Mapped[str | None] = mapped_column(String(64))
    pokemon: Mapped[str | None] = mapped_column(String(120), index=True)
    card_type: Mapped[str | None] = mapped_column(String(64))
    is_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    release_date: Mapped[date | None] = mapped_column(Date)

    # Normalised identity used to share market data between duplicate copies and
    # to match against provider catalogues later. Derived, never user-entered.
    catalog_key: Mapped[str | None] = mapped_column(String(200))

    # --- Ownership ---------------------------------------------------------
    raw_condition: Mapped[str | None] = mapped_column(
        String(32), default=RawCondition.UNKNOWN.value
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    purchase_price_minor: Mapped[int | None] = money_column()
    purchase_currency: Mapped[str | None] = mapped_column(String(3))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CardStatus.IN_COLLECTION.value, index=True
    )

    # --- User overrides (spec section 35: keep system and user values apart) --
    user_raw_value_minor: Mapped[int | None] = money_column()
    decision_override: Mapped[str | None] = mapped_column(String(32))
    decision_override_reason: Mapped[str | None] = mapped_column(Text)
    review_after: Mapped[date | None] = mapped_column(Date)  # spec section 33 "recheck in 30 days"

    notes: Mapped[str | None] = mapped_column(Text)
    external_ids: Mapped[dict | None] = mapped_column(JSON)

    images: Mapped[list[CardImage]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="CardImage.sort_order",
        lazy="selectin",
    )


class CardImage(Base):
    __tablename__ = "card_images"
    __table_args__ = (enum_check("side", ImageSide),)

    id: Mapped[str] = pk_column()
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False, default=ImageSide.FRONT.value)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    caption: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    card: Mapped[Card] = relationship(back_populates="images")


class CollectionGroup(Base, TimestampMixin):
    """User-defined folders/watchlists. ``smart`` groups store a saved filter."""

    __tablename__ = "collection_groups"
    __table_args__ = (enum_check("kind", GroupKind),)

    id: Mapped[str] = pk_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=GroupKind.FOLDER.value)
    filter_json: Mapped[dict | None] = mapped_column(JSON)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class CollectionGroupCard(Base):
    __tablename__ = "collection_group_cards"
    __table_args__ = (UniqueConstraint("group_id", "card_id", name="uq_group_card"),)

    id: Mapped[str] = pk_column()
    group_id: Mapped[str] = mapped_column(
        ForeignKey("collection_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    card_id: Mapped[str] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
