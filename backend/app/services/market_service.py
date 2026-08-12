"""Valuation, liquidity and trend, computed from stored sales (spec sections 16-20).

Everything here reads ``market_sales`` and writes ``market_prices`` and
``price_snapshots``. Nothing reaches the network: providers import into those
tables, and this module is what turns rows into numbers.

Three principles run through it.

**Never a bare average.** The median is the baseline because one absurd sale
should not move a valuation. Around it sit a recency-weighted median (a sale
from last week says more than one from March), quartiles for the range, and a
realistic figure that is what the user could actually get.

**Every number carries its evidence.** Sample size, window and confidence travel
with each valuation, because "£420 from 37 sales in 90 days" and "£420 from two
sales in nine months" are not the same claim (spec section 36).

**Too little data is an answer.** With three sales there is no trend, and saying
so is more useful than drawing a line through three points.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import Confidence, LiquidityBand, TrendDirection
from app.models import MarketListing, MarketPrice, MarketSale, PriceSnapshot
from app.money import apply_pct

# --- Tunables resolved from settings ----------------------------------------


@dataclass
class MarketParameters:
    window_days: int = 90
    half_life_days: int = 45
    outlier_iqr_multiplier: float = 1.5
    min_sales_high: int = 20
    min_sales_medium: int = 8
    quick_sale_discount_pct: float = 10.0
    # Below this many sales the IQR is meaningless, so outlier filtering is off.
    min_sales_for_outliers: int = 8

    @classmethod
    def from_settings(cls, values: dict) -> MarketParameters:
        return cls(
            window_days=int(values.get("market_window_days", 90)),
            half_life_days=int(values.get("recency_half_life_days", 45)),
            outlier_iqr_multiplier=float(values.get("outlier_iqr_multiplier", 1.5)),
            min_sales_high=int(values.get("min_sales_high_confidence", 20)),
            min_sales_medium=int(values.get("min_sales_medium_confidence", 8)),
            quick_sale_discount_pct=float(values.get("quick_sale_discount_pct", 10.0)),
        )


# --- Statistics on integer minor units --------------------------------------


def percentile(values: list[int], fraction: float) -> int | None:
    """Linear-interpolated percentile of a sorted-able list of minor units."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight)


def median(values: list[int]) -> int | None:
    return percentile(values, 0.5)


def weighted_median(pairs: list[tuple[int, float]]) -> int | None:
    """Median where each value carries a weight.

    Used for recency weighting: a sale from last week counts for more than one
    from March, without discarding the older sale entirely.
    """
    usable = [(value, weight) for value, weight in pairs if weight > 0]
    if not usable:
        return None
    usable.sort(key=lambda item: item[0])
    total = sum(weight for _, weight in usable)
    if total <= 0:
        return median([value for value, _ in usable])

    cumulative = 0.0
    for value, weight in usable:
        cumulative += weight
        if cumulative >= total / 2:
            return value
    return usable[-1][0]


def recency_weight(sale_date: date, today: date, half_life_days: int) -> float:
    age = max((today - sale_date).days, 0)
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age / half_life_days)


def iqr_bounds(values: list[int], multiplier: float) -> tuple[int, int] | None:
    """Tukey fences. ``None`` when there is too little data to mean anything."""
    if len(values) < 4:
        return None
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    if q1 is None or q3 is None:
        return None
    spread = q3 - q1
    if spread <= 0:
        return None
    return (
        int(q1 - multiplier * spread),
        int(q3 + multiplier * spread),
    )


# --- Valuation ---------------------------------------------------------------


@dataclass
class Valuation:
    grade_label: str
    company_id: str | None
    grade: float | None
    currency: str
    median_minor: int | None = None
    weighted_median_minor: int | None = None
    mean_minor: int | None = None
    low_quartile_minor: int | None = None
    high_quartile_minor: int | None = None
    last_sale_minor: int | None = None
    realistic_minor: int | None = None
    quick_minor: int | None = None
    sample_size: int = 0
    window_days: int = 90
    last_sale_at: date | None = None
    confidence: str = Confidence.NONE.value


def _confidence(sample_size: int, last_sale_at: date | None, today: date, params: MarketParameters) -> str:
    """How much to trust a valuation: how many sales, and how recent."""
    if sample_size == 0:
        return Confidence.NONE.value
    stale_days = (today - last_sale_at).days if last_sale_at else 10_000

    if sample_size >= params.min_sales_high and stale_days <= 45:
        return Confidence.HIGH.value
    if sample_size >= params.min_sales_medium and stale_days <= 120:
        return Confidence.MEDIUM.value
    if sample_size >= 3:
        return Confidence.LOW.value
    return Confidence.NONE.value


def value_sales(
    sales: list[MarketSale],
    *,
    params: MarketParameters,
    today: date,
    currency: str,
    grade_label: str,
    company_id: str | None = None,
    grade: float | None = None,
) -> Valuation:
    """Turn a set of comparable sales into a valuation.

    ``sales`` must already be filtered to one identity and grade, and to sales
    the user has not excluded.
    """
    valuation = Valuation(
        grade_label=grade_label,
        company_id=company_id,
        grade=grade,
        currency=currency,
        window_days=params.window_days,
    )
    if not sales:
        return valuation

    cutoff = today - timedelta(days=params.window_days)
    in_window = [sale for sale in sales if sale.sale_date >= cutoff]
    # Falling back to everything is deliberate: a card that sells twice a year
    # should still get a valuation, with a low confidence saying why.
    considered = in_window or sales
    if not in_window:
        # ``window_days`` is evidence, not configuration. Reporting 90 while
        # valuing sales that span nine months would be exactly the false
        # precision the rest of this module exists to avoid.
        oldest = min(sale.sale_date for sale in considered)
        valuation.window_days = max((today - oldest).days, params.window_days)

    prices = [sale.sale_price_minor for sale in considered]
    valuation.mean_minor = round(sum(prices) / len(prices))
    valuation.median_minor = median(prices)
    valuation.low_quartile_minor = percentile(prices, 0.25)
    valuation.high_quartile_minor = percentile(prices, 0.75)
    valuation.sample_size = len(considered)

    latest = max(considered, key=lambda sale: sale.sale_date)
    valuation.last_sale_minor = latest.sale_price_minor
    valuation.last_sale_at = latest.sale_date

    valuation.weighted_median_minor = weighted_median(
        [
            (sale.sale_price_minor, recency_weight(sale.sale_date, today, params.half_life_days))
            for sale in considered
        ]
    )

    # What the user could actually get: the recency-weighted figure, since that
    # is what the market is doing now rather than what it averaged over a
    # quarter.
    valuation.realistic_minor = valuation.weighted_median_minor or valuation.median_minor

    if valuation.realistic_minor is not None:
        discounted = valuation.realistic_minor - apply_pct(
            valuation.realistic_minor, params.quick_sale_discount_pct
        )
        # A quick sale should not price below the bottom of the observed range
        # unless the discount genuinely takes it there.
        valuation.quick_minor = min(discounted, valuation.low_quartile_minor or discounted)

    valuation.confidence = _confidence(
        valuation.sample_size, valuation.last_sale_at, today, params
    )
    return valuation


# --- Liquidity (spec section 17) ---------------------------------------------


def _interpolate(anchors: list[tuple[float, float]], value: float) -> float:
    if value <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in itertools.pairwise(anchors):
        if value <= x1:
            if x1 == x0:
                return y1
            return y0 + ((value - x0) / (x1 - x0)) * (y1 - y0)
    return anchors[-1][1]


# How often it trades, how recently, and how many are sitting unsold. Our
# estimates, calibrated so a card selling weekly scores well and one selling
# twice a year does not.
FREQUENCY_ANCHORS = [(0.0, 0.0), (1, 2.0), (3, 4.0), (6, 5.5), (12, 7.0), (25, 8.5), (50, 10.0)]
RECENCY_ANCHORS = [(0.0, 10.0), (7, 9.0), (14, 8.0), (30, 6.0), (60, 4.0), (120, 2.0), (365, 0.0)]
DEPTH_ANCHORS = [(0.0, 0.0), (0.25, 4.0), (0.5, 6.0), (1.0, 8.0), (2.0, 10.0)]


@dataclass
class Liquidity:
    score: float | None = None
    band: str = LiquidityBand.UNKNOWN.value
    sales_7d: int = 0
    sales_30d: int = 0
    sales_90d: int = 0
    sales_365d: int = 0
    days_since_last_sale: int | None = None
    active_listings: int | None = None
    sold_to_active_ratio: float | None = None
    median_days_between_sales: float | None = None
    sales_per_month: float | None = None

    #: Which evidence the score rests on: ``sales`` for your own records,
    #: ``reported_volume`` for a source's annual count. Worth stating, because
    #: the two answer different halves of the question and the second cannot
    #: answer recency at all.
    basis: str | None = None
    #: Yearly units sold, as reported. Shown alongside your own sales when both
    #: exist, because they are independent readings and disagreement is
    #: information.
    annual_volume: int | None = None


def _band(score: float) -> str:
    if score >= 9:
        return LiquidityBand.VERY_LIQUID.value
    if score >= 7:
        return LiquidityBand.LIQUID.value
    if score >= 5:
        return LiquidityBand.MODERATE.value
    if score >= 3:
        return LiquidityBand.ILLIQUID.value
    return LiquidityBand.VERY_ILLIQUID.value


def measure_liquidity(
    sales: list[MarketSale],
    *,
    today: date,
    active_listings: int | None = None,
    annual_volume: int | None = None,
) -> Liquidity:
    """How readily this actually trades.

    The point of this number is to stop "PSA 10 = £600" from being read as "you
    can get £600" (spec section 17).

    Two kinds of evidence, and your own sales win — the same precedence prices
    follow, for the same reason. Sales you recorded carry a date each, so they
    answer both halves of the question: how *often* this trades and whether it
    traded *recently*.

    ``annual_volume`` is a source's count of yearly units sold. It answers the
    first half better than a thin local sample ever could — a year of the whole
    market against your handful of rows — and the second half not at all. So it
    is used only where there are no sales to use, and the reading says so.
    """
    result = Liquidity(active_listings=active_listings, annual_volume=annual_volume)
    if not sales:
        return _from_reported_volume(result, annual_volume)
    result.basis = "sales"

    def count_since(days: int) -> int:
        cutoff = today - timedelta(days=days)
        return sum(1 for sale in sales if sale.sale_date >= cutoff)

    result.sales_7d = count_since(7)
    result.sales_30d = count_since(30)
    result.sales_90d = count_since(90)
    result.sales_365d = count_since(365)

    dates = sorted(sale.sale_date for sale in sales)
    result.days_since_last_sale = (today - dates[-1]).days
    result.sales_per_month = round(result.sales_365d / 12, 2) if result.sales_365d else 0.0

    if len(dates) > 1:
        gaps = [(later - earlier).days for earlier, later in itertools.pairwise(dates)]
        result.median_days_between_sales = float(percentile(gaps, 0.5) or 0)

    frequency = _interpolate(FREQUENCY_ANCHORS, result.sales_90d)
    recency = _interpolate(RECENCY_ANCHORS, result.days_since_last_sale)

    components = [(frequency, 0.45), (recency, 0.35)]
    if active_listings is not None and active_listings > 0:
        result.sold_to_active_ratio = round(result.sales_90d / active_listings, 3)
        components.append((_interpolate(DEPTH_ANCHORS, result.sold_to_active_ratio), 0.20))

    total_weight = sum(weight for _, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight
    result.score = round(min(max(score, 0.0), 10.0), 1)
    result.band = _band(result.score)
    return result


#: The most a reading with no recency behind it may score — one notch under
#: ``very_liquid``, which begins at 9.
#:
#: Not timidity. Normalising over the components that exist makes a *partial*
#: reading easier to max out than a complete one: with sales you need frequency
#: and recency both perfect to reach ten, and with a yearly count you need only
#: frequency. Driving the UI produced a confident 10.0 "very liquid" from one
#: number, which is the wrong shape of claim however good that number is.
#:
#: "Very liquid" in this application means you can realise the money now, and
#: that is exactly what recency would evidence and an annual total cannot.
_NO_RECENCY_CEILING = 8.9


def _from_reported_volume(result: Liquidity, annual_volume: int | None) -> Liquidity:
    """Score frequency alone, from a source's yearly count.

    The counts stay at zero and that is deliberate: ``sales_90d`` means "sales
    of this card that you hold records for", and filling it from an annual
    figure would turn a derivation into a claim about your data. What gets
    derived is the *reading*, and it is labelled as derived.

    Recency is simply absent. An annual total cannot distinguish a card selling
    steadily all year from one that sold forty times in January and has not
    moved since — so the score is computed over the components that do exist,
    exactly as it already is when active listings are unknown, and nothing is
    invented to stand in for the missing one.
    """
    if annual_volume is None:
        return result

    result.basis = "reported_volume"
    result.sales_per_month = round(annual_volume / 12, 2)
    result.median_days_between_sales = round(365 / annual_volume, 1) if annual_volume else None

    # The frequency anchors are calibrated on a 90-day count, so the annual
    # figure is put on that scale rather than the anchors being duplicated.
    implied_90d = annual_volume * 90 / 365
    components = [(_interpolate(FREQUENCY_ANCHORS, implied_90d), 0.45)]
    if result.active_listings:
        result.sold_to_active_ratio = round(implied_90d / result.active_listings, 3)
        components.append((_interpolate(DEPTH_ANCHORS, result.sold_to_active_ratio), 0.20))

    total_weight = sum(weight for _, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight
    result.score = round(min(max(score, 0.0), _NO_RECENCY_CEILING), 1)
    result.band = _band(result.score)
    return result


# --- Trend (spec sections 19, 20) --------------------------------------------

HORIZONS: tuple[int, ...] = (7, 30, 90, 180, 365)


@dataclass
class Trend:
    direction: str = TrendDirection.INSUFFICIENT_DATA.value
    confidence: str = Confidence.NONE.value
    changes: dict[int, float | None] = field(default_factory=dict)
    sample_size: int = 0
    # Which grade the direction describes. A trend is only meaningful within one
    # grade — see ``trend_sales``.
    grade_label: str | None = None


def _change_over(
    sales: list[MarketSale], *, horizon: int, today: date, params: MarketParameters
) -> tuple[float | None, int]:
    """Percentage change between the last ``horizon`` days and the ``horizon``
    before it. Returns ``(change, comparable_sample)``."""
    recent_from = today - timedelta(days=horizon)
    prior_from = today - timedelta(days=horizon * 2)

    recent = [sale for sale in sales if sale.sale_date >= recent_from]
    prior = [sale for sale in sales if prior_from <= sale.sale_date < recent_from]
    if len(recent) < 2 or len(prior) < 2:
        return None, min(len(recent), len(prior))

    recent_value = median([sale.sale_price_minor for sale in recent])
    prior_value = median([sale.sale_price_minor for sale in prior])
    if not recent_value or not prior_value:
        return None, min(len(recent), len(prior))

    change = (recent_value - prior_value) / prior_value * 100
    return round(change, 2), min(len(recent), len(prior))


def _direction(change: float) -> str:
    if change >= 15:
        return TrendDirection.STRONG_UP.value
    if change >= 5:
        return TrendDirection.UP.value
    if change > -5:
        return TrendDirection.STABLE.value
    if change > -15:
        return TrendDirection.DOWN.value
    return TrendDirection.STRONG_DOWN.value


def trend_sales(sales: list[MarketSale]) -> tuple[list[MarketSale], str | None]:
    """Pick the one grade a trend can honestly be measured on.

    Pooling grades makes the trend measure *sales mix* rather than price: if
    three PSA 10s sell this month and none sold last month, the pooled median
    leaps and reads as a rising market when nothing moved. So the trend is
    measured within a single grade — raw when it has the depth, otherwise
    whichever grade trades most — and the answer says which grade it describes.
    """
    if not sales:
        return [], None

    by_label: dict[str, list[MarketSale]] = {}
    for sale in sales:
        by_label.setdefault(sale.grade_label, []).append(sale)

    raw = by_label.get("raw", [])
    if len(raw) >= 4:
        return raw, "raw"
    label = max(by_label, key=lambda key: (len(by_label[key]), key != "raw"))
    return by_label[label], label


def measure_trend(sales: list[MarketSale], *, today: date, params: MarketParameters) -> Trend:
    """Price direction, with its own confidence.

    Spec section 20: +25% from three sales is not the same claim as +12% from a
    hundred and fifty, and the number alone cannot tell them apart.

    ``sales`` must be a single grade — use ``trend_sales`` to choose one.
    """
    result = Trend()
    if not sales:
        return result
    result.grade_label = sales[0].grade_label

    comparable = 0
    for horizon in HORIZONS:
        change, sample = _change_over(sales, horizon=horizon, today=today, params=params)
        result.changes[horizon] = change
        if horizon == 90 and change is not None:
            comparable = sample

    # The headline direction comes from the longest horizon with real data, so a
    # quiet fortnight does not read as a crash.
    headline: float | None = None
    for horizon in (90, 180, 30, 365, 7):
        if result.changes.get(horizon) is not None:
            headline = result.changes[horizon]
            comparable = comparable or 0
            break

    result.sample_size = len(sales)
    if headline is None:
        result.direction = TrendDirection.INSUFFICIENT_DATA.value
        result.confidence = Confidence.NONE.value
        return result

    result.direction = _direction(headline)

    if len(sales) >= params.min_sales_high:
        result.confidence = Confidence.HIGH.value
    elif len(sales) >= params.min_sales_medium:
        result.confidence = Confidence.MEDIUM.value
    else:
        result.confidence = Confidence.LOW.value
    return result


# --- Persistence -------------------------------------------------------------


def usable_sales(db: Session, catalog_key: str, grade_label: str | None = None) -> list[MarketSale]:
    """Sales for one identity that count toward a valuation.

    Excluded rows stay in the table so the user can see and reverse the
    decision; they simply do not feed the numbers.
    """
    stmt = select(MarketSale).where(
        MarketSale.catalog_key == catalog_key, MarketSale.is_excluded.is_(False)
    )
    if grade_label is not None:
        stmt = stmt.where(MarketSale.grade_label == grade_label)
    return list(db.scalars(stmt.order_by(MarketSale.sale_date)))


def grade_labels_for(db: Session, catalog_key: str) -> list[str]:
    rows = db.scalars(
        select(MarketSale.grade_label)
        .where(MarketSale.catalog_key == catalog_key, MarketSale.is_excluded.is_(False))
        .distinct()
    )
    labels = list(rows)
    # Raw first, then graded ascending, so the UI reads bottom-up.
    labels.sort(key=lambda label: (label != "raw", label))
    return labels


def active_listing_count(db: Session, catalog_key: str, grade_label: str) -> int | None:
    total = db.scalar(
        select(func.count())
        .select_from(MarketListing)
        .where(
            MarketListing.catalog_key == catalog_key,
            MarketListing.grade_label == grade_label,
            MarketListing.is_active.is_(True),
        )
    )
    return total or None


def recompute_key(
    db: Session,
    catalog_key: str,
    *,
    params: MarketParameters,
    currency: str,
    today: date | None = None,
    snapshot: bool = True,
) -> list[MarketPrice]:
    """Recompute every grade's valuation for one card identity.

    Writes a ``price_snapshots`` row per grade per day as a side effect, so the
    user's own long-run price history accrues regardless of any provider
    (spec section 38).
    """
    today = today or date.today()
    written: list[MarketPrice] = []

    for label in grade_labels_for(db, catalog_key):
        sales = usable_sales(db, catalog_key, label)
        if not sales:
            continue

        first = sales[0]
        valuation = value_sales(
            sales,
            params=params,
            today=today,
            currency=currency,
            grade_label=label,
            company_id=first.company_id,
            grade=first.grade,
        )

        row = db.scalars(
            select(MarketPrice).where(
                MarketPrice.catalog_key == catalog_key,
                MarketPrice.grade_label == label,
                MarketPrice.source_id.is_(None),
            )
        ).first()
        if row is None:
            row = MarketPrice(catalog_key=catalog_key, grade_label=label)
            db.add(row)

        row.company_id = valuation.company_id
        row.grade = valuation.grade
        row.currency = valuation.currency
        row.median_minor = valuation.median_minor
        row.weighted_median_minor = valuation.weighted_median_minor
        row.mean_minor = valuation.mean_minor
        row.low_quartile_minor = valuation.low_quartile_minor
        row.high_quartile_minor = valuation.high_quartile_minor
        row.last_sale_minor = valuation.last_sale_minor
        row.realistic_sale_minor = valuation.realistic_minor
        row.quick_sale_minor = valuation.quick_minor
        row.sample_size = valuation.sample_size
        row.window_days = valuation.window_days
        row.last_sale_at = valuation.last_sale_at
        row.confidence = valuation.confidence
        row.computed_at = datetime.now(UTC)
        written.append(row)

        if snapshot and valuation.realistic_minor is not None:
            existing = db.scalars(
                select(PriceSnapshot).where(
                    PriceSnapshot.catalog_key == catalog_key,
                    PriceSnapshot.grade_label == label,
                    PriceSnapshot.snapshot_date == today,
                    PriceSnapshot.source_id.is_(None),
                )
            ).first()
            if existing is None:
                db.add(
                    PriceSnapshot(
                        catalog_key=catalog_key,
                        grade_label=label,
                        company_id=valuation.company_id,
                        grade=valuation.grade,
                        snapshot_date=today,
                        currency=currency,
                        value_minor=valuation.realistic_minor,
                        sample_size=valuation.sample_size,
                        active_listings=active_listing_count(db, catalog_key, label),
                    )
                )
            else:
                existing.value_minor = valuation.realistic_minor
                existing.sample_size = valuation.sample_size

    db.flush()
    return written


def prices_for(db: Session, catalog_key: str) -> list[MarketPrice]:
    """One price per grade, preferring your own sales over a provider's index.

    ``market_prices`` is unique per ``(catalog_key, grade_label, source_id)``, so
    a valuation computed from your sales (``source_id IS NULL``) and one fetched
    from a provider can sit side by side without either overwriting the other.
    That is the right storage shape and the wrong thing to hand to an engine:
    asked for "the raw price" it would get two, and pick whichever the database
    happened to return first.

    So they are resolved here, and the rule is that **your own sales win**. A
    median computed from twenty-two real sales of this exact card, in your
    currency, is better evidence than a third party's aggregate index — which
    covers a market you may not sell in, blends printings this app keeps apart,
    and cannot tell you how often the card actually trades.

    A provider price is used when there is no sale-derived one for that grade,
    which is the common case on a fresh collection, and it carries its source so
    the card page can say where the number came from.
    """
    rows = list(
        db.scalars(
            select(MarketPrice)
            .where(MarketPrice.catalog_key == catalog_key)
            .order_by(MarketPrice.grade_label)
        )
    )

    resolved: dict[str, MarketPrice] = {}
    for row in rows:
        existing = resolved.get(row.grade_label)
        if existing is None or _outranks(row, existing):
            resolved[row.grade_label] = row
    return sorted(resolved.values(), key=lambda row: (row.grade_label != "raw", row.grade_label))


def snapshots_for(db: Session, catalog_key: str, *, since: date) -> list[PriceSnapshot]:
    """History for one card: **one point per grade per day**, best evidence first.

    ``price_snapshots`` is unique per ``(catalog_key, grade_label, date, source)``
    so a day can hold several rows for one grade — your own valuation and a
    provider's index, written by different code paths on the same afternoon.

    Ungrouped, a chart draws every one of them and produces a vertical spike on
    that date: a price movement that never happened, made of two sources
    disagreeing. So the same precedence prices follow applies here — your own
    sales win, then the larger sample — and what is plotted is one line of best
    evidence rather than every row that exists.
    """
    rows = db.scalars(
        select(PriceSnapshot)
        .where(
            PriceSnapshot.catalog_key == catalog_key,
            PriceSnapshot.snapshot_date >= since,
        )
        .order_by(PriceSnapshot.grade_label, PriceSnapshot.snapshot_date)
    )

    best: dict[tuple[str, date], PriceSnapshot] = {}
    for row in rows:
        slot = (row.grade_label, row.snapshot_date)
        held = best.get(slot)
        if held is None or _snapshot_outranks(row, held):
            best[slot] = row
    return sorted(best.values(), key=lambda row: (row.grade_label, row.snapshot_date))


def _snapshot_outranks(candidate: PriceSnapshot, incumbent: PriceSnapshot) -> bool:
    """Same rule as prices, minus the timestamp a snapshot does not carry."""
    candidate_own = candidate.source_id is None and candidate.sample_size > 0
    incumbent_own = incumbent.source_id is None and incumbent.sample_size > 0
    if candidate_own != incumbent_own:
        return candidate_own
    return candidate.sample_size > incumbent.sample_size


def _outranks(candidate: MarketPrice, incumbent: MarketPrice) -> bool:
    """Whether ``candidate`` is the better evidence of the two.

    Sale-derived beats provider-sourced, but only when it actually has sales
    behind it: a row left over from sales that were all later excluded has a
    zero sample and describes nothing, so a provider's number is better than
    that. Between two provider rows, the larger sample wins, then the fresher.
    """
    candidate_own = candidate.source_id is None and candidate.sample_size > 0
    incumbent_own = incumbent.source_id is None and incumbent.sample_size > 0
    if candidate_own != incumbent_own:
        return candidate_own

    if candidate.sample_size != incumbent.sample_size:
        return candidate.sample_size > incumbent.sample_size

    return _naive(candidate.computed_at) > _naive(incumbent.computed_at)


def _naive(value: datetime | None) -> datetime:
    """Comparable regardless of where the row came from.

    SQLite has no native timezone, so a ``computed_at`` written as aware reads
    back naive. Comparing the two raises, and it would only ever raise once two
    provider rows tied on sample size — a case no test would reach by accident.
    """
    if value is None:
        return _EPOCH
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


_EPOCH = datetime(1970, 1, 1)


def premium_vs_raw_pct(raw: MarketPrice | None, graded: MarketPrice) -> float | None:
    """How much more the slab fetches than the raw card (spec section 21)."""
    if raw is None:
        return None
    base = raw.realistic_sale_minor or raw.median_minor
    value = graded.realistic_sale_minor or graded.median_minor
    if not base or not value:
        return None
    return round((value - base) / base * 100, 1)


def raw_price(prices: list[MarketPrice]) -> MarketPrice | None:
    for row in prices:
        if row.grade_label == "raw":
            return row
    return None


# --- One identity's whole market ---------------------------------------------


@dataclass
class MarketSummary:
    """Prices, liquidity and trend for one identity, computed together.

    ``evaluate_card`` and ``GET /market/{catalog_key}`` both read this, so the
    card page and the market page can never disagree about the same card.
    """

    catalog_key: str
    currency: str
    prices: list[MarketPrice] = field(default_factory=list)
    liquidity: Liquidity = field(default_factory=Liquidity)
    trend: Trend = field(default_factory=Trend)
    sale_count: int = 0
    excluded_count: int = 0
    grade_labels: list[str] = field(default_factory=list)
    computed_at: datetime | None = None

    @property
    def raw(self) -> MarketPrice | None:
        return raw_price(self.prices)

    @property
    def graded(self) -> list[MarketPrice]:
        rows = [row for row in self.prices if row.grade_label != "raw"]
        return sorted(rows, key=lambda row: (row.grade or 0, row.grade_label), reverse=True)


def summarise(
    db: Session,
    catalog_key: str | None,
    *,
    params: MarketParameters,
    currency: str,
    today: date | None = None,
) -> MarketSummary:
    """Read the stored market picture for one identity. Computes nothing new.

    Liquidity and trend are measured on the fly because they are cheap and
    depend on today's date; the valuations are read from ``market_prices``,
    which ``recompute_key`` maintains.
    """
    if not catalog_key:
        return MarketSummary(catalog_key="", currency=currency)

    today = today or date.today()
    prices = prices_for(db, catalog_key)
    # Liquidity is a property of the card trading at all, so it is measured
    # across every grade rather than per slab: a card whose PSA 10s never appear
    # but whose raw copies sell weekly is still a liquid card.
    sales = usable_sales(db, catalog_key)
    excluded = (
        db.scalar(
            select(func.count())
            .select_from(MarketSale)
            .where(MarketSale.catalog_key == catalog_key, MarketSale.is_excluded.is_(True))
        )
        or 0
    )
    listings = db.scalar(
        select(func.count())
        .select_from(MarketListing)
        .where(MarketListing.catalog_key == catalog_key, MarketListing.is_active.is_(True))
    )

    # Reported per product rather than per grade, which is the shape liquidity
    # is measured at. Taken from whichever row carries it; every row a source
    # writes for one card carries the same figure.
    volume = db.scalar(
        select(func.max(MarketPrice.annual_volume)).where(
            MarketPrice.catalog_key == catalog_key
        )
    )

    computed = [row.computed_at for row in prices if row.computed_at is not None]
    for_trend, _label = trend_sales(sales)
    return MarketSummary(
        catalog_key=catalog_key,
        currency=currency,
        prices=prices,
        liquidity=measure_liquidity(
            sales, today=today, active_listings=listings or None, annual_volume=volume
        ),
        trend=measure_trend(for_trend, today=today, params=params),
        sale_count=len(sales),
        excluded_count=excluded,
        grade_labels=grade_labels_for(db, catalog_key),
        computed_at=max(computed) if computed else None,
    )
