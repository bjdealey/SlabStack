"""Fetching from a provider, and deciding what of it to keep.

The rule from Phase 1 holds and is the reason this module exists at all:
**providers only ever write into the local tables**, and nothing downstream of
the database calls a provider directly. An adapter returns records; this decides
what to persist. So a provider going away, changing its shape, or returning
nonsense costs the user future updates and never their history.

Four things it will not do:

**It will not overwrite your own numbers.** A price you set by hand, and a
valuation computed from sales you recorded, both outrank anything fetched. The
provider's row is separate and only used when there is nothing better.

**It will not invent an exchange rate.** Providers quote USD and EUR; this app
reports one currency. Where the rate to convert is not configured, the price is
*skipped and reported* rather than written as though the numbers were already in
your currency. That is the difference between a missing figure and a wrong one.

**It will not delete on failure.** A sync that errors halfway leaves everything
it had already written and records why it stopped. Nothing is cleared first and
refilled after.

**It will not pretend a partial sync was a whole one.** Every run returns what
it fetched, what it skipped, and why, per card.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Confidence
from app.models import Card, DataSource, MarketPrice, PriceSnapshot
from app.services import settings_service
from app.services.market_data.base import MarketDataProvider, MarketKey
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider

__all__ = [
    "CardSyncOutcome",
    "SyncReport",
    "convert_minor",
    "sync_cards",
    "sync_source",
]


@dataclass
class CardSyncOutcome:
    """What happened to one card in a sync run."""

    card_id: str
    name: str
    status: str
    #: Populated on success, in the app's currency.
    value: float | None = None
    currency: str | None = None
    #: What the provider quoted, before conversion, so the number is auditable.
    source_value: float | None = None
    source_currency: str | None = None
    fx_rate: float | None = None
    reason: str | None = None


@dataclass
class SyncReport:
    source_code: str
    source_name: str
    started_at: str
    finished_at: str | None = None
    requested: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    status: str = "ok"
    reason: str | None = None
    cards: list[CardSyncOutcome] = field(default_factory=list)
    #: Things true of the run as a whole rather than of one card.
    notes: list[str] = field(default_factory=list)


# --- Currency ----------------------------------------------------------------


def convert_minor(
    amount_minor: int, *, from_currency: str, to_currency: str, rates: dict
) -> tuple[int | None, float | None]:
    """Convert, or refuse and say nothing rather than something wrong.

    Returns ``(None, None)`` when no rate is configured for the pair. There is
    no live FX feed in this build and there is no sensible default: a wrong rate
    silently rescales every price a provider supplies, and the mistake would be
    invisible precisely because the numbers still look like money.
    """
    if from_currency == to_currency:
        return amount_minor, 1.0

    rate = _rate_for(rates, from_currency, to_currency)
    if rate is None:
        return None, None
    return round(amount_minor * rate), rate


def _rate_for(rates: dict, from_currency: str, to_currency: str) -> float | None:
    """Direct rate, else the inverse of the opposite pair, else nothing."""
    direct = rates.get(f"{from_currency}_{to_currency}")
    if isinstance(direct, int | float) and direct > 0:
        return float(direct)

    inverse = rates.get(f"{to_currency}_{from_currency}")
    if isinstance(inverse, int | float) and inverse > 0:
        return round(1 / float(inverse), 6)
    return None


# --- Syncing -----------------------------------------------------------------


def sync_source(
    db: Session,
    source: DataSource,
    *,
    card_ids: list[str] | None = None,
    limit: int = 200,
) -> SyncReport:
    """Refresh prices for a set of cards from one source."""
    report = SyncReport(
        source_code=source.code,
        source_name=source.name,
        started_at=datetime.now(UTC).isoformat(),
    )

    try:
        provider = load_provider(source)
    except ProviderUnavailableError as exc:
        return _abort(db, source, report, str(exc))

    if not provider.capabilities().current_price:
        return _abort(
            db,
            source,
            report,
            f"{source.name} does not supply prices, so there is nothing to sync from it.",
        )

    cards = _cards_to_sync(db, source, card_ids=card_ids, limit=limit)
    report.requested = len(cards)
    if not cards:
        report.status = "insufficient_data"
        report.reason = (
            "No card is linked to this source yet. Look a card up in the catalogue and confirm "
            "the match — the link is stored, and future syncs use it."
        )
        _record(db, source, report)
        return report

    values = settings_service.get_all(db)
    currency = values.get("currency", "GBP")
    rates = values.get("fx_rates") or {}

    for card in cards:
        outcome = _sync_one(db, provider, card, source=source, currency=currency, rates=rates)
        report.cards.append(outcome)

        if outcome.status == "updated":
            report.updated += 1
        elif outcome.status == "failed":
            report.failed += 1
        else:
            report.skipped += 1

    _summarise(report, currency=currency, rates=rates)
    _record(db, source, report)
    return report


def _sync_one(
    db: Session,
    provider: MarketDataProvider,
    card: Card,
    *,
    source: DataSource,
    currency: str,
    rates: dict,
) -> CardSyncOutcome:
    outcome = CardSyncOutcome(card_id=card.id, name=_display(card), status="skipped")
    external_id = (card.external_ids or {}).get(source.code)

    if not external_id:
        outcome.reason = "Not linked to this source yet."
        return outcome
    if not card.catalog_key:
        outcome.reason = "No catalog key, so a price could not be attached to anything."
        return outcome

    key = MarketKey(
        catalog_key=card.catalog_key,
        grade_label="raw",
        currency=currency,
        external_id=external_id,
        variant=card.variant,
    )

    try:
        point = provider.get_current_price(key)
    except ProviderRequestError as exc:
        outcome.status = "failed"
        outcome.reason = str(exc)
        return outcome
    except Exception as exc:  # One bad adapter must not abort the whole run.
        outcome.status = "failed"
        outcome.reason = f"{source.name} adapter raised {type(exc).__name__}: {exc}"
        return outcome

    if point is None:
        outcome.reason = f"{source.name} has no current price for this card."
        return outcome

    outcome.source_value = round(point.value_minor / 100, 2)
    outcome.source_currency = point.currency

    converted, rate = convert_minor(
        point.value_minor, from_currency=point.currency, to_currency=currency, rates=rates
    )
    if converted is None:
        outcome.reason = (
            f"{point.currency} price, but no {point.currency}→{currency} rate is set. Add one in "
            "Settings → Market and run this again — nothing is lost, the price is simply not "
            "written until it can be stated in your currency."
        )
        return outcome

    outcome.status = "updated"
    outcome.value = round(converted / 100, 2)
    outcome.currency = currency
    outcome.fx_rate = rate
    _write_price(
        db, card=card, source=source, point=point, value_minor=converted, currency=currency
    )
    return outcome


def _write_price(
    db: Session, *, card: Card, source: DataSource, point, value_minor: int, currency: str
) -> None:
    """Upsert this source's own price row, and snapshot it for the trend.

    The snapshot matters more than it looks. This source has no price history to
    import, so the only way a trend ever appears for a provider-priced card is
    by accruing one a day at a time from here. ``recompute_key`` cannot do it:
    it walks the grades that have *sales*, and a provider-only card has none.
    """
    row = db.scalars(
        select(MarketPrice).where(
            MarketPrice.catalog_key == card.catalog_key,
            MarketPrice.grade_label == "raw",
            MarketPrice.source_id == source.id,
        )
    ).first()
    if row is None:
        row = MarketPrice(
            catalog_key=card.catalog_key, grade_label="raw", source_id=source.id
        )
        db.add(row)

    row.currency = currency
    row.median_minor = value_minor
    row.realistic_sale_minor = value_minor
    # Left null on purpose. An aggregate index has no quartiles, no last sale
    # and no window, and filling them from the one number available would
    # manufacture a spread that does not exist.
    row.weighted_median_minor = None
    row.low_quartile_minor = None
    row.high_quartile_minor = None
    row.last_sale_minor = None
    row.quick_sale_minor = None
    row.window_days = None
    row.last_sale_at = point.as_of
    # Zero sales, and the confidence says so: this is somebody's index, not
    # evidence of what you could have sold it for.
    row.sample_size = 0
    row.confidence = Confidence.LOW.value
    row.computed_at = datetime.now(UTC)

    today = date.today()
    snapshot = db.scalars(
        select(PriceSnapshot).where(
            PriceSnapshot.catalog_key == card.catalog_key,
            PriceSnapshot.grade_label == "raw",
            PriceSnapshot.snapshot_date == today,
            PriceSnapshot.source_id == source.id,
        )
    ).first()
    if snapshot is None:
        db.add(
            PriceSnapshot(
                catalog_key=card.catalog_key,
                grade_label="raw",
                snapshot_date=today,
                currency=currency,
                value_minor=value_minor,
                sample_size=0,
                source_id=source.id,
            )
        )
    else:
        # One row per source per day: syncing twice is not two data points.
        snapshot.value_minor = value_minor
        snapshot.currency = currency


def sync_cards(db: Session, *, card_ids: list[str] | None = None, limit: int = 200) -> list[SyncReport]:
    """Run every enabled price source, best priority first."""
    reports: list[SyncReport] = []
    for source in _price_sources(db):
        reports.append(sync_source(db, source, card_ids=card_ids, limit=limit))
    return reports


def _price_sources(db: Session) -> list[DataSource]:
    """Enabled sources that could actually return a price.

    Manual and CSV are enabled and are not network sources — asking them to
    sync would be asking the user's own data to refresh itself.
    """
    return [
        source
        for source in db.scalars(
            select(DataSource)
            .where(DataSource.enabled.is_(True), DataSource.provider_class.is_not(None))
            .order_by(DataSource.priority)
        )
        if source.code not in {"manual", "csv"}
    ]


def _cards_to_sync(
    db: Session, source: DataSource, *, card_ids: list[str] | None, limit: int
) -> list[Card]:
    stmt = select(Card).where(Card.catalog_key.is_not(None))
    if card_ids:
        stmt = stmt.where(Card.id.in_(card_ids))
    cards = list(db.scalars(stmt.order_by(Card.updated_at.desc())))

    linked = [card for card in cards if (card.external_ids or {}).get(source.code)]
    return linked[:limit]


def _summarise(report: SyncReport, *, currency: str, rates: dict) -> None:
    unconvertible = [row for row in report.cards if row.reason and "rate is set" in row.reason]
    if unconvertible:
        missing = sorted({row.source_currency for row in unconvertible if row.source_currency})
        report.notes.append(
            f"{len(unconvertible)} price(s) fetched but not written: no "
            f"{'/'.join(missing)}→{currency} rate is configured. Set one in Settings → Market."
        )

    unlinked = [row for row in report.cards if row.reason == "Not linked to this source yet."]
    if unlinked:
        report.notes.append(
            f"{len(unlinked)} card(s) are not linked to this source. Look each one up in the "
            "catalogue and confirm the match."
        )

    if report.failed:
        report.status = "partial" if report.updated else "error"
        report.reason = f"{report.failed} card(s) failed. The rest were left as they were."
    elif report.skipped and report.updated:
        report.status = "partial"
        report.reason = f"Updated {report.updated}, skipped {report.skipped}."
    elif not report.updated:
        report.status = "insufficient_data"
        report.reason = report.notes[0] if report.notes else "Nothing to update."
    if not rates and any(row.source_currency for row in report.cards):
        report.notes.append(
            "No exchange rates are configured at all, so no foreign-currency price can be "
            "written. This is deliberate: guessing a rate would rescale every price silently."
        )


def _abort(db: Session, source: DataSource, report: SyncReport, reason: str) -> SyncReport:
    report.status = "error"
    report.reason = reason
    _record(db, source, report)
    return report


def _record(db: Session, source: DataSource, report: SyncReport) -> None:
    """Write the outcome onto the source, so the UI can show it without a run."""
    report.finished_at = datetime.now(UTC).isoformat()
    source.last_sync_at = datetime.now(UTC)
    source.last_sync_status = report.status
    source.last_sync_error = report.reason if report.status in {"error", "partial"} else None
    db.flush()


def _display(card: Card) -> str:
    return f"{card.name} {card.card_number}".strip() if card.card_number else card.name
