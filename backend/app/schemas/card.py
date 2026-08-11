"""Card, image, set, variant and group payloads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field, field_validator

from app.enums import CardStatus, Decision, GroupKind, ImageSide, Language, RawCondition
from app.models import Card, CardImage
from app.money import to_major, to_minor
from app.schemas.common import ApiModel

# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class CardImageOut(ApiModel):
    id: str
    card_id: str
    side: str
    url: str
    thumbnail_url: str | None
    original_filename: str | None
    mime_type: str
    width: int | None
    height: int | None
    size_bytes: int | None
    is_primary: bool
    sort_order: int
    caption: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, image: CardImage) -> CardImageOut:
        return cls(
            id=image.id,
            card_id=image.card_id,
            side=image.side,
            url=f"/api/images/{image.id}/file",
            thumbnail_url=f"/api/images/{image.id}/thumbnail" if image.thumbnail_path else None,
            original_filename=image.original_filename,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            size_bytes=image.size_bytes,
            is_primary=image.is_primary,
            sort_order=image.sort_order,
            caption=image.caption,
            created_at=image.created_at,
        )


class CardImageUpdate(ApiModel):
    side: str | None = None
    caption: str | None = None
    is_primary: bool | None = None
    sort_order: int | None = None

    @field_validator("side")
    @classmethod
    def _valid_side(cls, value: str | None) -> str | None:
        if value is not None and value not in ImageSide.values():
            raise ValueError(f"side must be one of {ImageSide.values()}")
        return value


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class CardWriteBase(ApiModel):
    name: str = Field(min_length=1, max_length=160)
    set_id: str | None = None
    set_name: str | None = Field(default=None, max_length=160)
    set_code: str | None = Field(default=None, max_length=32)
    card_number: str | None = Field(default=None, max_length=32)
    variant_id: str | None = None
    variant: str | None = Field(default=None, max_length=80)
    language: str = Language.ENGLISH.value
    printing: str | None = None
    rarity: str | None = Field(default=None, max_length=64)
    pokemon: str | None = Field(default=None, max_length=120)
    card_type: str | None = Field(default=None, max_length=64)
    is_promo: bool = False
    release_date: date | None = None
    raw_condition: str | None = RawCondition.UNKNOWN.value
    quantity: int = Field(default=1, ge=1, le=10_000)
    purchase_price: float | None = Field(default=None, ge=0)
    purchase_currency: str | None = Field(default=None, min_length=3, max_length=3)
    purchase_date: date | None = None
    status: str = CardStatus.IN_COLLECTION.value
    user_raw_value: float | None = Field(default=None, ge=0)
    decision_override: str | None = None
    decision_override_reason: str | None = None
    review_after: date | None = None
    notes: str | None = None
    external_ids: dict[str, Any] | None = None

    @field_validator("language")
    @classmethod
    def _valid_language(cls, value: str) -> str:
        if value not in Language.values():
            raise ValueError(f"language must be one of {Language.values()}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in CardStatus.values():
            raise ValueError(f"status must be one of {CardStatus.values()}")
        return value

    @field_validator("raw_condition")
    @classmethod
    def _valid_condition(cls, value: str | None) -> str | None:
        if value is not None and value not in RawCondition.values():
            raise ValueError(f"raw_condition must be one of {RawCondition.values()}")
        return value

    @field_validator("decision_override")
    @classmethod
    def _valid_decision(cls, value: str | None) -> str | None:
        if value is not None and value not in Decision.values():
            raise ValueError(f"decision_override must be one of {Decision.values()}")
        return value


class CardCreate(CardWriteBase):
    pass


class CardUpdate(ApiModel):
    """Every field optional — PATCH semantics.

    ``None`` and "absent" are different: a field present with ``null`` clears the
    value, an absent field is left alone. Handled by ``model_dump(exclude_unset)``
    in the route.
    """

    name: str | None = Field(default=None, min_length=1, max_length=160)
    set_id: str | None = None
    set_name: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    variant_id: str | None = None
    variant: str | None = None
    language: str | None = None
    printing: str | None = None
    rarity: str | None = None
    pokemon: str | None = None
    card_type: str | None = None
    is_promo: bool | None = None
    release_date: date | None = None
    raw_condition: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=10_000)
    purchase_price: float | None = Field(default=None, ge=0)
    purchase_currency: str | None = None
    purchase_date: date | None = None
    status: str | None = None
    user_raw_value: float | None = Field(default=None, ge=0)
    decision_override: str | None = None
    decision_override_reason: str | None = None
    review_after: date | None = None
    notes: str | None = None
    external_ids: dict[str, Any] | None = None

    @field_validator("language")
    @classmethod
    def _valid_language(cls, value: str | None) -> str | None:
        if value is not None and value not in Language.values():
            raise ValueError(f"language must be one of {Language.values()}")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str | None) -> str | None:
        if value is not None and value not in CardStatus.values():
            raise ValueError(f"status must be one of {CardStatus.values()}")
        return value

    @field_validator("raw_condition")
    @classmethod
    def _valid_condition(cls, value: str | None) -> str | None:
        if value is not None and value not in RawCondition.values():
            raise ValueError(f"raw_condition must be one of {RawCondition.values()}")
        return value

    @field_validator("decision_override")
    @classmethod
    def _valid_decision(cls, value: str | None) -> str | None:
        if value is not None and value not in Decision.values():
            raise ValueError(f"decision_override must be one of {Decision.values()}")
        return value


class CardOut(ApiModel):
    id: str
    name: str
    set_id: str | None
    set_name: str | None
    set_code: str | None
    card_number: str | None
    variant_id: str | None
    variant: str | None
    language: str
    printing: str | None
    rarity: str | None
    pokemon: str | None
    card_type: str | None
    is_promo: bool
    release_date: date | None
    catalog_key: str | None
    raw_condition: str | None
    quantity: int
    purchase_price: float | None
    purchase_currency: str | None
    purchase_date: date | None
    status: str
    user_raw_value: float | None
    decision_override: str | None
    decision_override_reason: str | None
    review_after: date | None
    notes: str | None
    external_ids: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    images: list[CardImageOut] = Field(default_factory=list)
    primary_image_url: str | None = None
    has_condition_assessment: bool = False

    @classmethod
    def from_model(
        cls,
        card: Card,
        *,
        include_images: bool = True,
        has_condition_assessment: bool = False,
    ) -> CardOut:
        images = [CardImageOut.from_model(image) for image in card.images] if include_images else []
        primary = next(
            (image for image in images if image.is_primary and image.side == ImageSide.FRONT.value),
            next((image for image in images if image.is_primary), None),
        )
        return cls(
            id=card.id,
            name=card.name,
            set_id=card.set_id,
            set_name=card.set_name,
            set_code=card.set_code,
            card_number=card.card_number,
            variant_id=card.variant_id,
            variant=card.variant,
            language=card.language,
            printing=card.printing,
            rarity=card.rarity,
            pokemon=card.pokemon,
            card_type=card.card_type,
            is_promo=card.is_promo,
            release_date=card.release_date,
            catalog_key=card.catalog_key,
            raw_condition=card.raw_condition,
            quantity=card.quantity,
            purchase_price=to_major(card.purchase_price_minor),
            purchase_currency=card.purchase_currency,
            purchase_date=card.purchase_date,
            status=card.status,
            user_raw_value=to_major(card.user_raw_value_minor),
            decision_override=card.decision_override,
            decision_override_reason=card.decision_override_reason,
            review_after=card.review_after,
            notes=card.notes,
            external_ids=card.external_ids,
            created_at=card.created_at,
            updated_at=card.updated_at,
            images=images,
            primary_image_url=primary.thumbnail_url or primary.url if primary else None,
            has_condition_assessment=has_condition_assessment,
        )


class CardSplitRequest(ApiModel):
    count: int | None = Field(
        default=None,
        ge=2,
        description="How many copies to break out. Defaults to the full quantity.",
    )


class BulkCardCreate(ApiModel):
    cards: list[CardCreate] = Field(min_length=1, max_length=1000)


# Model field -> DB column for the money fields, applied by the routes.
MONEY_FIELDS: dict[str, str] = {
    "purchase_price": "purchase_price_minor",
    "user_raw_value": "user_raw_value_minor",
}


def apply_card_payload(card: Card, payload: dict[str, Any]) -> Card:
    """Copy an API payload onto a model, converting money to minor units."""
    for field_name, value in payload.items():
        if field_name in MONEY_FIELDS:
            setattr(card, MONEY_FIELDS[field_name], to_minor(value))
        else:
            setattr(card, field_name, value)
    return card


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class CardSetOut(ApiModel):
    id: str
    code: str
    name: str
    series: str | None
    language: str
    release_date: date | None
    total_cards: int | None
    secret_cards: int | None
    notes: str | None


class CardSetWrite(ApiModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    series: str | None = None
    language: str = Language.ENGLISH.value
    release_date: date | None = None
    total_cards: int | None = Field(default=None, ge=0)
    secret_cards: int | None = Field(default=None, ge=0)
    notes: str | None = None


class CardVariantOut(ApiModel):
    id: str
    code: str
    name: str
    description: str | None
    sort_order: int
    is_builtin: bool
    active: bool


class CardVariantWrite(ApiModel):
    code: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    sort_order: int = 100
    active: bool = True


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class GroupOut(ApiModel):
    id: str
    name: str
    description: str | None
    color: str | None
    kind: str
    filter_json: dict[str, Any] | None
    sort_order: int
    card_count: int = 0
    created_at: datetime
    updated_at: datetime


class GroupWrite(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    color: str | None = None
    kind: str = GroupKind.FOLDER.value
    filter_json: dict[str, Any] | None = None
    sort_order: int = 100

    @field_validator("kind")
    @classmethod
    def _valid_kind(cls, value: str) -> str:
        if value not in GroupKind.values():
            raise ValueError(f"kind must be one of {GroupKind.values()}")
        return value


class GroupCardsRequest(ApiModel):
    card_ids: list[str] = Field(min_length=1)
