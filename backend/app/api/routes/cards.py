"""Card CRUD, search and evaluation."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import Field
from sqlalchemy import select

from app.api.deps import CardDep, DbSession, PaginationParams
from app.api.errors import ApiError
from app.enums import CardStatus
from app.models import Card, CardDisposal, ConditionAssessment
from app.money import to_major, to_minor
from app.schemas.card import (
    BulkCardCreate,
    CardCreate,
    CardOut,
    CardSplitRequest,
    CardUpdate,
    apply_card_payload,
)
from app.schemas.common import ApiModel, Page
from app.schemas.evaluation import CardEvaluation
from app.services import (
    cards_service,
    collection_import,
    disposals,
    evaluation,
    market_service,
    sales_import,
    settings_service,
)
from app.services.cards_service import CardFilters

router = APIRouter(prefix="/cards", tags=["cards"])


class ImportedCardOut(ApiModel):
    line_number: int
    name: str
    set_name: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    variant: str | None = None
    printing: str | None = None
    language: str
    rarity: str | None = None
    quantity: int
    raw_condition: str
    purchase_price: float | None = None
    purchase_currency: str | None = None
    purchase_date: date | None = None
    catalog_key: str | None = None
    duplicate_of: str | None = Field(
        default=None, description="Set when this row matches a card already held."
    )
    condition_as_written: str | None = Field(
        default=None, description="What the file said, when we could not make sense of it."
    )


class RowErrorOut(ApiModel):
    line_number: int | None = None
    message: str


class CollectionImportOut(ApiModel):
    dry_run: bool
    status: str
    reason: str | None = None
    imported: int
    duplicates: int
    failed: int
    cards: list[ImportedCardOut] = Field(default_factory=list)
    errors: list[RowErrorOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CollectionImportIn(ApiModel):
    csv: str = Field(description="The file's contents. Delimiter and column order are detected.")


@router.post(
    "/import",
    response_model=CollectionImportOut,
    summary="Import a collection from a CSV export",
    description=(
        "Reads a collection export and adds the cards. **Dry run by default**: four hundred "
        "unwanted rows are far harder to undo than they were to create, so the first thing this "
        "does is read the file, say exactly what it found, and change nothing.\n\n"
        "Column names are matched loosely and only a card name is required. Rows matching a card "
        "already held are reported and, by default, skipped — re-importing last month's file "
        "should not double the collection.\n\n"
        "A `Condition` column is stored as a **label**, in `raw_condition`. It does not become a "
        "condition assessment: spec section 6 rejects NM/LP/MP as the condition model, and "
        "inventing per-corner severities from one word would put fabricated evidence under a "
        "grading decision. Imported cards report 'not assessed' until somebody looks at them."
    ),
)
def import_collection(
    db: DbSession,
    payload: CollectionImportIn,
    dry_run: Annotated[bool, Query(description="Read and report without writing.")] = True,
    skip_duplicates: Annotated[
        bool, Query(description="Skip rows matching a card already held.")
    ] = True,
) -> CollectionImportOut:
    report = collection_import.import_collection(
        db, payload.csv, dry_run=dry_run, skip_duplicates=skip_duplicates
    )
    if not report.dry_run:
        db.commit()
    return CollectionImportOut(
        dry_run=report.dry_run,
        status=report.status,
        reason=report.reason,
        imported=report.imported,
        duplicates=report.duplicates,
        failed=report.failed,
        cards=[
            ImportedCardOut(
                **{
                    key: value
                    for key, value in vars(row).items()
                    if key != "purchase_price_minor"
                },
                purchase_price=to_major(row.purchase_price_minor),
            )
            for row in report.cards
        ],
        errors=[
            RowErrorOut(line_number=error.line_number, message=error.message)
            for error in report.errors
        ],
        notes=report.notes,
    )


# --- What a card actually sold for -------------------------------------------


class DisposalIn(ApiModel):
    """Recording a sale. A price and a date is the whole of the common case."""

    sold_on: date
    gross: float = Field(ge=0, description="What the buyer paid for the card itself.")
    sold_graded: bool = False
    grade_label: str = "raw"
    grade: float | None = None
    company_id: str | None = None
    platform: str | None = Field(
        default=None, description="A selling profile code, used to estimate the costs below."
    )
    shipping_income: float | None = None
    fees: float | None = Field(
        default=None, description="Everything the platform and processor took, as one figure."
    )
    postage_cost: float | None = None
    packaging_cost: float | None = None
    net_proceeds: float | None = Field(
        default=None,
        description=(
            "What actually reached you. Supply it from a payout statement and it wins over "
            "every estimate above, and is recorded as yours rather than derived."
        ),
    )
    grading_cost: float | None = Field(
        default=None,
        description=(
            "What grading this card cost. Null means unrecorded, never free — a realised "
            "profit computed without it would flatter grading."
        ),
    )
    notes: str | None = None


class DisposalOut(ApiModel):
    id: str
    card_id: str | None = None
    card_name: str | None = None
    sold_on: date
    platform: str | None = None
    currency: str = "GBP"
    sold_graded: bool = False
    grade_label: str = "raw"
    grade: float | None = None
    gross: float
    shipping_income: float | None = None
    fees: float | None = None
    postage_cost: float | None = None
    packaging_cost: float | None = None
    net_proceeds: float
    net_is_user_entered: bool = False
    grading_cost: float | None = None
    notes: str | None = None

    @classmethod
    def from_model(cls, row: CardDisposal) -> DisposalOut:
        return cls(
            id=row.id,
            card_id=row.card_id,
            card_name=row.card_name,
            sold_on=row.sold_on,
            platform=row.platform,
            currency=row.currency,
            sold_graded=row.sold_graded,
            grade_label=row.grade_label,
            grade=row.grade,
            gross=to_major(row.gross_minor) or 0.0,
            shipping_income=to_major(row.shipping_income_minor),
            fees=to_major(row.fees_minor),
            postage_cost=to_major(row.postage_cost_minor),
            packaging_cost=to_major(row.packaging_cost_minor),
            net_proceeds=to_major(row.net_proceeds_minor) or 0.0,
            net_is_user_entered=row.net_is_user_entered,
            grading_cost=to_major(row.grading_cost_minor),
            notes=row.notes,
        )


@router.post(
    "/{card_id}/sold",
    response_model=DisposalOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record what this card actually sold for",
    description=(
        "The one figure in this application that is not a projection. Everything else — what a "
        "card is worth, what grading costs, what a sale would net — is an estimate; this is the "
        "money that arrived.\n\n"
        "Only `sold_on` and `gross` are required. The costs are filled in from the selling "
        "profile so that recording a sale is a price and a date, and any of them can be "
        "overridden. A supplied `net_proceeds` wins over all of it and is marked as yours, "
        "because a payout statement is a fact and a fee model is not.\n\n"
        "The card is marked `sold`."
    ),
)
def record_sale(db: DbSession, card: CardDep, payload: DisposalIn) -> DisposalOut:
    existing = db.scalar(select(CardDisposal).where(CardDisposal.card_id == card.id))
    if existing is not None:
        raise ApiError(
            "conflict",
            "This card is already recorded as sold. Correct that record rather than adding a "
            "second one — a card sells once, and two records would double the realised profit.",
            status.HTTP_409_CONFLICT,
        )
    row = disposals.record_disposal(
        db,
        card,
        sold_on=payload.sold_on,
        gross_minor=to_minor(payload.gross) or 0,
        sold_graded=payload.sold_graded,
        grade_label=payload.grade_label,
        grade=payload.grade,
        company_id=payload.company_id,
        platform=payload.platform,
        shipping_income_minor=to_minor(payload.shipping_income),
        fees_minor=to_minor(payload.fees),
        postage_cost_minor=to_minor(payload.postage_cost),
        packaging_cost_minor=to_minor(payload.packaging_cost),
        net_proceeds_minor=to_minor(payload.net_proceeds),
        grading_cost_minor=to_minor(payload.grading_cost),
        notes=payload.notes,
    )
    db.commit()
    db.refresh(row)
    return DisposalOut.from_model(row)


@router.get(
    "/{card_id}/sold",
    response_model=DisposalOut | None,
    summary="What this card sold for, if it has",
)
def get_sale(db: DbSession, card: CardDep) -> DisposalOut | None:
    row = db.scalar(select(CardDisposal).where(CardDisposal.card_id == card.id))
    return DisposalOut.from_model(row) if row else None


@router.delete(
    "/{card_id}/sold",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Undo a recorded sale",
    description="Deletes the record and returns the card to your collection.",
)
def delete_sale(db: DbSession, card: CardDep) -> Response:
    row = db.scalar(select(CardDisposal).where(CardDisposal.card_id == card.id))
    if row is not None:
        db.delete(row)
    card.status = CardStatus.IN_COLLECTION.value
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _assessed_ids(db: DbSession, cards: list[Card]) -> set[str]:
    if not cards:
        return set()
    rows = db.scalars(
        select(ConditionAssessment.card_id).where(
            ConditionAssessment.card_id.in_([card.id for card in cards]),
            ConditionAssessment.is_current.is_(True),
        )
    )
    return set(rows)


@router.get("", response_model=Page[CardOut], summary="Search and list cards")
def list_cards(
    db: DbSession,
    pagination: PaginationParams,
    q: Annotated[str | None, Query(description="Free text over name, set, number, notes.")] = None,
    set_id: str | None = None,
    set_code: str | None = None,
    language: str | None = None,
    variant: str | None = None,
    rarity: str | None = None,
    pokemon: str | None = None,
    status_in: Annotated[list[str] | None, Query(alias="status")] = None,
    is_promo: bool | None = None,
    group_id: str | None = None,
    decision_override: str | None = None,
    has_images: bool | None = None,
    has_condition: bool | None = None,
    sort: str = "created_at",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> Page[CardOut]:
    filters = CardFilters(
        q=q,
        set_id=set_id,
        set_code=set_code,
        language=language,
        variant=variant,
        rarity=rarity,
        pokemon=pokemon,
        status=status_in,
        is_promo=is_promo,
        group_id=group_id,
        decision_override=decision_override,
        has_images=has_images,
        has_condition=has_condition,
    )
    cards, total = cards_service.list_cards(
        db,
        filters,
        sort=sort,
        order=order,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    assessed = _assessed_ids(db, cards)
    items = [
        CardOut.from_model(card, has_condition_assessment=card.id in assessed) for card in cards
    ]
    return Page.build(items, total, pagination.page, pagination.page_size)


@router.post("", response_model=CardOut, status_code=status.HTTP_201_CREATED, summary="Add a card")
def create_card(db: DbSession, payload: CardCreate) -> CardOut:
    card = Card()
    apply_card_payload(card, payload.model_dump())
    cards_service.resolve_references(db, card)
    db.add(card)
    db.flush()
    return CardOut.from_model(card)


@router.post(
    "/bulk",
    response_model=list[CardOut],
    status_code=status.HTTP_201_CREATED,
    summary="Add many cards at once",
)
def create_cards_bulk(db: DbSession, payload: BulkCardCreate) -> list[CardOut]:
    created: list[Card] = []
    for item in payload.cards:
        card = Card()
        apply_card_payload(card, item.model_dump())
        cards_service.resolve_references(db, card)
        db.add(card)
        created.append(card)
    db.flush()
    return [CardOut.from_model(card) for card in created]


@router.get("/{card_id}", response_model=CardOut, summary="Get one card")
def get_card(db: DbSession, card: CardDep) -> CardOut:
    assessed = cards_service.current_condition(db, card.id) is not None
    return CardOut.from_model(card, has_condition_assessment=assessed)


@router.patch(
    "/{card_id}",
    response_model=CardOut,
    summary="Update a card",
    description=(
        "Editing the name, number, set, variant, language or printing changes the card's "
        "identity. Sales recorded against this card follow it and are re-judged against the "
        "new identity, so a corrected language does not leave the market history stranded — "
        "nor quietly count English comparables toward a Japanese card."
    ),
)
def update_card(db: DbSession, card: CardDep, payload: CardUpdate) -> CardOut:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return CardOut.from_model(card)
    previous_key = card.catalog_key
    apply_card_payload(card, changes)
    cards_service.resolve_references(db, card)
    db.flush()
    if previous_key and card.catalog_key and previous_key != card.catalog_key:
        _follow_identity_change(db, card, previous_key)
    assessed = cards_service.current_condition(db, card.id) is not None
    return CardOut.from_model(card, has_condition_assessment=assessed)


def _follow_identity_change(db: DbSession, card: Card, previous_key: str) -> None:
    """Move this card's own market rows onto its new identity and re-filter them."""
    moved = sales_import.migrate_card_key(db, card.id, previous_key, card.catalog_key)
    if not moved:
        return
    context = sales_import.SaleContext(
        catalog_key=card.catalog_key,
        language=card.language,
        variant=card.variant,
        printing=card.printing,
    )
    sales_import.reclassify_key(db, context=context)
    values = settings_service.get_all(db)
    params = market_service.MarketParameters.from_settings(values)
    sales_import.mark_outliers(db, card.catalog_key, params=params)
    market_service.recompute_key(
        db, card.catalog_key, params=params, currency=values.get("currency", "GBP")
    )


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a card")
def delete_card(db: DbSession, card: CardDep) -> Response:
    from app.services import images_service

    # Files first: a card row without its images is recoverable, an orphaned
    # pile of JPEGs on disk is not. The rows go with the cascade.
    images_service.delete_card_media(card)
    db.delete(card)
    db.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{card_id}/split",
    response_model=list[CardOut],
    summary="Split a stack into individual physical cards",
)
def split_card(db: DbSession, card: CardDep, payload: CardSplitRequest | None = None) -> list[CardOut]:
    if card.quantity <= 1:
        raise ApiError(
            "cannot_split",
            "This card is already a single copy. Grading decisions are made per physical card.",
        )
    count = payload.count if payload else None
    cards = cards_service.split_card(db, card, count)
    return [CardOut.from_model(item) for item in cards]


@router.get(
    "/{card_id}/evaluation",
    response_model=CardEvaluation,
    summary="Evaluate a card: condition, market, economics, recommendation",
    description=(
        "The central decision-engine call (spec section 45). Every block carries a status; "
        "blocks whose engine has not been built yet report `not_implemented` or "
        "`insufficient_data` with the phase that delivers them, never a fabricated number.\n\n"
        "`batch_size` is how many cards to assume share a submission's shipping and insurance. "
        "It defaults to 1 — the honest worst case, where one card carries the whole postage — "
        "and changes which tiers are usable as well as what each one costs."
    ),
)
def evaluate(
    db: DbSession,
    card: CardDep,
    batch_size: Annotated[
        int,
        Query(ge=1, le=1000, description="Cards assumed to share one submission."),
    ] = 1,
) -> CardEvaluation:
    return evaluation.evaluate_card(db, card, batch_size=batch_size)
