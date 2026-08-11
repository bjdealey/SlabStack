"""Talking to a provider: enabling one, looking a card up, refreshing prices.

The only endpoints in this application that cause an outbound request. Two
things follow from that and are enforced here rather than left to convention.

**Nothing reaches out until a source is enabled.** A fresh install makes no
network call at all; enabling a source is a decision the user takes, and these
routes refuse politely until they have.

**A catalogue match is a suggestion.** Looking a card up returns candidates and
writes nothing. Applying one is a second, explicit call, because silently
rewriting somebody's card because an API was confident is precisely the failure
this abstraction was shaped to avoid (spec section 5).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.errors import ApiError, ConflictError, NotFoundError
from app.models import Card, DataSource
from app.schemas.common import ApiModel
from app.services import cards_service, market_sync
from app.services.market_data.base import CardQuery
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider

router = APIRouter(tags=["market data"])


# --- Enabling a source -------------------------------------------------------


class DataSourceUpdate(ApiModel):
    enabled: bool | None = None
    rate_limit_per_minute: int | None = None
    config: dict[str, Any] | None = None


class SourceStateOut(ApiModel):
    code: str
    name: str
    enabled: bool
    has_adapter: bool
    api_key_present: bool
    api_key_env_var: str | None = None
    last_sync_at: str | None = None
    last_sync_status: str | None = None
    last_sync_error: str | None = None
    terms_url: str | None = None
    notes: str | None = None


def _state(source: DataSource) -> SourceStateOut:
    import os

    return SourceStateOut(
        code=source.code,
        name=source.name,
        enabled=source.enabled,
        has_adapter=bool(source.provider_class),
        api_key_present=bool(source.api_key_env_var and os.environ.get(source.api_key_env_var)),
        api_key_env_var=source.api_key_env_var,
        last_sync_at=source.last_sync_at.isoformat() if source.last_sync_at else None,
        last_sync_status=source.last_sync_status,
        last_sync_error=source.last_sync_error,
        terms_url=source.terms_url,
        notes=source.notes,
    )


def _source(db, code: str) -> DataSource:
    row = db.scalar(select(DataSource).where(DataSource.code == code))
    if row is None:
        raise NotFoundError("Data source", code)
    return row


@router.patch(
    "/data-sources/{code}",
    response_model=SourceStateOut,
    summary="Enable or configure a data source",
    description=(
        "Enabling a source is the moment this application first talks to the internet, so it "
        "is off until you say otherwise and never enabled by an update.\n\n"
        "A source with no adapter cannot be enabled — there would be nothing behind it."
    ),
)
def update_source(db: DbSession, code: str, payload: DataSourceUpdate) -> SourceStateOut:
    source = _source(db, code)
    data = payload.model_dump(exclude_unset=True)

    if data.get("enabled") and not source.provider_class:
        raise ConflictError(
            f"'{source.code}' has no adapter, so enabling it would do nothing. "
            "Sources without one are listed to show what is planned, not to be switched on."
        )

    for key, value in data.items():
        if key == "config" and value is not None:
            source.config = {**(source.config or {}), **value}
        elif hasattr(source, key):
            setattr(source, key, value)

    db.commit()
    db.refresh(source)
    return _state(source)


# --- Looking a card up -------------------------------------------------------


class CardMatchOut(ApiModel):
    external_id: str
    name: str
    set_name: str | None = None
    set_code: str | None = None
    card_number: str | None = None
    rarity: str | None = None
    language: str | None = None
    image_url: str | None = None
    confidence: float = Field(
        default=0.0,
        description="How well this candidate matches what was asked. Ordering only — a match "
        "is never applied without you confirming it.",
    )


class LookupOut(ApiModel):
    source_code: str
    source_name: str
    query: str | None = None
    matches: list[CardMatchOut] = Field(default_factory=list)
    status: str
    reason: str | None = None


@router.get(
    "/catalog/lookup",
    response_model=LookupOut,
    summary="Find a card in a provider's catalogue",
    description=(
        "Returns candidates and writes nothing. Confirming one is a separate call, because a "
        "confident API silently rewriting your card is exactly what this abstraction exists to "
        "prevent.\n\n"
        "Narrow beats broad: a name plus a set plus a number finds one card, a bare name finds "
        "two hundred."
    ),
)
def lookup(
    db: DbSession,
    name: Annotated[str | None, Query(description="Card name.")] = None,
    set_code: Annotated[str | None, Query()] = None,
    card_number: Annotated[str | None, Query()] = None,
    source_code: Annotated[str, Query()] = "pokemontcg_io",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LookupOut:
    source = _source(db, source_code)
    try:
        provider = load_provider(source)
    except ProviderUnavailableError as exc:
        return LookupOut(
            source_code=source.code,
            source_name=source.name,
            status="unavailable",
            reason=str(exc),
        )

    if not provider.capabilities().search:
        return LookupOut(
            source_code=source.code,
            source_name=source.name,
            status="unavailable",
            reason=f"{source.name} does not offer card search.",
        )

    query = CardQuery(name=name, set_code=set_code, card_number=card_number, limit=limit)
    try:
        matches = provider.search_card(query)
    except ProviderRequestError as exc:
        raise ApiError(
            "provider_error", str(exc), status.HTTP_502_BAD_GATEWAY
        ) from exc

    return LookupOut(
        source_code=source.code,
        source_name=source.name,
        query=name,
        matches=[
            CardMatchOut(
                external_id=match.external_id,
                name=match.name,
                set_name=match.set_name,
                set_code=match.set_code,
                card_number=match.card_number,
                rarity=match.rarity,
                language=match.language,
                image_url=match.image_url,
                confidence=match.confidence,
            )
            for match in sorted(matches, key=lambda item: item.confidence, reverse=True)
        ],
        status="ok" if matches else "insufficient_data",
        reason=None if matches else "No card in this catalogue matched. Try a broader search.",
    )


class LinkPayload(ApiModel):
    source_code: str = "pokemontcg_io"
    external_id: str
    #: Fields to accept from the catalogue. Anything omitted is left as it is —
    #: a lookup fills gaps, it does not overwrite what you already decided.
    apply_fields: list[str] = Field(default_factory=list)
    set_code: str | None = None
    set_name: str | None = None
    card_number: str | None = None
    rarity: str | None = None


@router.post(
    "/cards/{card_id}/catalog-link",
    summary="Confirm a catalogue match for a card",
    description=(
        "Stores the provider's own id for this card, so future syncs price it directly instead "
        "of re-searching by name and risking a different printing.\n\n"
        "`apply_fields` names which catalogue values to accept. Anything you leave out stays as "
        "you had it: a lookup fills gaps rather than overwriting decisions."
    ),
)
def link_card(db: DbSession, card_id: str, payload: LinkPayload) -> dict:
    card = db.get(Card, card_id)
    if card is None:
        raise NotFoundError("Card", card_id)
    source = _source(db, payload.source_code)

    card.external_ids = {**(card.external_ids or {}), source.code: payload.external_id}

    applied: list[str] = []
    for field_name in payload.apply_fields:
        value = getattr(payload, field_name, None)
        if value is None or not hasattr(card, field_name):
            continue
        setattr(card, field_name, value)
        applied.append(field_name)

    if applied:
        # Identity fields feed `catalog_key`, which is how sales and prices find
        # this card at all — accepting a set code without recomputing it would
        # quietly orphan everything already attached.
        cards_service.resolve_references(db, card)
        cards_service.refresh_derived(card)

    db.commit()
    db.refresh(card)
    return {
        "card_id": card.id,
        "source_code": source.code,
        "external_id": payload.external_id,
        "applied_fields": applied,
        "catalog_key": card.catalog_key,
    }


# --- Refreshing --------------------------------------------------------------


class CardSyncOut(ApiModel):
    card_id: str
    name: str
    status: str
    value: float | None = None
    currency: str | None = None
    source_value: float | None = Field(
        default=None, description="What the provider quoted, before conversion."
    )
    source_currency: str | None = None
    fx_rate: float | None = Field(
        default=None, description="The rate used — yours, from Settings, not a live one."
    )
    reason: str | None = None


class SyncReportOut(ApiModel):
    source_code: str
    source_name: str
    started_at: str
    finished_at: str | None = None
    requested: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    status: str
    reason: str | None = None
    cards: list[CardSyncOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


@router.post(
    "/market/refresh",
    response_model=list[SyncReportOut],
    summary="Fetch prices from every enabled provider",
    description=(
        "Runs each enabled source in priority order and reports, per card, what was fetched, "
        "what was skipped, and why.\n\n"
        "**Providers only ever write their own rows.** A price computed from your sales, and "
        "one you set by hand, both outrank anything fetched — a provider figure is used only "
        "where you have nothing better.\n\n"
        "**A foreign price with no exchange rate configured is fetched and not written.** "
        "Guessing a rate would rescale every price silently, so the run reports it and asks for "
        "the rate instead.\n\n"
        "**A failure costs future updates, never history.** Nothing is cleared before a run."
    ),
)
def refresh_market(
    db: DbSession,
    card_id: Annotated[str | None, Query(description="Refresh one card only.")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[SyncReportOut]:
    card_ids = [card_id] if card_id else None
    if card_id and db.get(Card, card_id) is None:
        raise NotFoundError("Card", card_id)

    reports = market_sync.sync_cards(db, card_ids=card_ids, limit=limit)
    if not reports:
        raise ConflictError(
            "No market-data source is enabled. Turn one on in Settings → Data sources; nothing "
            "here reaches the network until you do."
        )

    db.commit()
    return [
        SyncReportOut(
            **{key: value for key, value in vars(report).items() if key != "cards"},
            cards=[CardSyncOut(**vars(row)) for row in report.cards],
        )
        for report in reports
    ]
