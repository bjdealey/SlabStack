"""The valuation, liquidity and trend engines, tested without a database.

These are pure functions over a list of sales, which is deliberate: the maths
that decides what a card is worth should be checkable without an HTTP client or
a schema.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.enums import Confidence, LiquidityBand, TrendDirection
from app.models import MarketSale
from app.services import market_service
from app.services.market_service import MarketParameters

TODAY = date(2026, 6, 1)
PARAMS = MarketParameters()


def sale(days_ago: int, price_pounds: float, *, label: str = "raw") -> MarketSale:
    return MarketSale(
        catalog_key="k",
        grade_label=label,
        sale_date=TODAY - timedelta(days=days_ago),
        sale_price_minor=round(price_pounds * 100),
        currency="GBP",
    )


def series(prices: list[tuple[int, float]], label: str = "raw") -> list[MarketSale]:
    return [sale(days, price, label=label) for days, price in prices]


# --- Statistics --------------------------------------------------------------


def test_percentile_interpolates():
    values = [100, 200, 300, 400]
    assert market_service.percentile(values, 0.0) == 100
    assert market_service.percentile(values, 1.0) == 400
    assert market_service.percentile(values, 0.5) == 250
    assert market_service.percentile(values, 0.25) == 175


def test_percentile_of_nothing_is_none_not_zero():
    """Zero reads as 'worthless'; None reads as 'not calculated'."""
    assert market_service.percentile([], 0.5) is None
    assert market_service.median([]) is None


def test_median_ignores_one_absurd_sale():
    ordinary = [1000, 1100, 1200, 1150, 1050]
    assert market_service.median(ordinary) == 1100
    # The mean of this set is over 17,000. The median barely moves.
    assert market_service.median([*ordinary, 99_000]) == 1125


def test_recency_weight_halves_at_the_half_life():
    assert market_service.recency_weight(TODAY, TODAY, 45) == pytest.approx(1.0)
    assert market_service.recency_weight(TODAY - timedelta(days=45), TODAY, 45) == pytest.approx(0.5)
    assert market_service.recency_weight(TODAY - timedelta(days=90), TODAY, 45) == pytest.approx(0.25)


def test_weighted_median_leans_toward_recent_sales():
    pairs = [(1000, 0.1), (1000, 0.1), (2000, 1.0)]
    assert market_service.weighted_median(pairs) == 2000


def test_iqr_needs_a_sample_to_draw_a_fence():
    assert market_service.iqr_bounds([100, 200, 300], 1.5) is None
    bounds = market_service.iqr_bounds([100, 110, 120, 130, 1000], 1.5)
    assert bounds is not None
    low, high = bounds
    assert low < 100
    assert high < 1000


# --- Valuation ---------------------------------------------------------------


def _value(sales: list[MarketSale], label: str = "raw"):
    return market_service.value_sales(
        sales, params=PARAMS, today=TODAY, currency="GBP", grade_label=label
    )


def test_valuation_reports_its_evidence():
    sales = series([(5, 100), (12, 110), (30, 105), (44, 108)])
    result = _value(sales)

    assert result.sample_size == 4
    assert result.window_days == 90
    assert result.last_sale_at == TODAY - timedelta(days=5)
    assert result.median_minor == 10650
    assert result.confidence == Confidence.LOW.value  # four sales is not confidence


def test_confidence_climbs_with_sample_size():
    thin = _value(series([(5, 100), (10, 100), (20, 100)]))
    medium = _value(series([(day, 100) for day in range(1, 12)]))
    thick = _value(series([(day, 100) for day in range(1, 40)]))

    assert thin.confidence == Confidence.LOW.value
    assert medium.confidence == Confidence.MEDIUM.value
    assert thick.confidence == Confidence.HIGH.value


def test_confidence_falls_when_the_data_is_stale():
    """Thirty sales that all happened last year are not high confidence today."""
    stale = _value(series([(300 + day, 100) for day in range(30)]))
    assert stale.confidence in {Confidence.LOW.value, Confidence.MEDIUM.value}
    assert stale.confidence != Confidence.HIGH.value


def test_two_sales_still_produce_a_number_and_say_it_is_weak():
    result = _value(series([(3, 100), (40, 120)]))
    assert result.median_minor is not None
    assert result.confidence == Confidence.NONE.value
    assert result.sample_size == 2


def test_no_sales_produces_no_numbers():
    result = _value([])
    assert result.median_minor is None
    assert result.realistic_minor is None
    assert result.sample_size == 0
    assert result.confidence == Confidence.NONE.value


def test_falls_back_outside_the_window_rather_than_reporting_nothing():
    """A card whose only sales are six months old still has a value."""
    result = _value(series([(200, 100), (240, 96), (260, 104)]))
    assert result.sample_size == 3
    assert result.median_minor == 10000
    assert result.window_days > PARAMS.window_days
    assert result.confidence == Confidence.LOW.value


def test_quick_sale_is_below_the_realistic_figure():
    result = _value(series([(day, 100 + day) for day in range(1, 20)]))
    assert result.quick_minor is not None
    assert result.realistic_minor is not None
    assert result.quick_minor < result.realistic_minor


def test_recency_weighting_moves_the_realistic_figure_toward_recent_sales():
    rising = series([(80, 100), (75, 100), (70, 100), (5, 200), (3, 200), (1, 200)])
    result = _value(rising)
    assert result.weighted_median_minor is not None
    assert result.weighted_median_minor >= result.median_minor


# --- Liquidity ---------------------------------------------------------------


def test_liquidity_unknown_without_sales():
    result = market_service.measure_liquidity([], today=TODAY)
    assert result.score is None
    assert result.band == LiquidityBand.UNKNOWN.value


def test_a_card_that_trades_weekly_scores_higher_than_one_that_does_not():
    liquid = market_service.measure_liquidity(
        series([(day, 100) for day in range(1, 90, 3)]), today=TODAY
    )
    illiquid = market_service.measure_liquidity(series([(200, 100), (330, 100)]), today=TODAY)

    assert liquid.score > illiquid.score
    assert liquid.band in {LiquidityBand.LIQUID.value, LiquidityBand.VERY_LIQUID.value}
    assert illiquid.band in {LiquidityBand.ILLIQUID.value, LiquidityBand.VERY_ILLIQUID.value}


def test_liquidity_counts_each_window_separately():
    result = market_service.measure_liquidity(
        series([(2, 100), (5, 100), (20, 100), (60, 100), (200, 100)]), today=TODAY
    )
    assert result.sales_7d == 2
    assert result.sales_30d == 3
    assert result.sales_90d == 4
    assert result.sales_365d == 5
    assert result.days_since_last_sale == 2


def test_unsold_listings_drag_the_score_down():
    sales = series([(day, 100) for day in range(1, 60, 6)])
    healthy = market_service.measure_liquidity(sales, today=TODAY, active_listings=2)
    flooded = market_service.measure_liquidity(sales, today=TODAY, active_listings=200)

    assert flooded.score < healthy.score
    assert flooded.sold_to_active_ratio < healthy.sold_to_active_ratio


# --- Trend -------------------------------------------------------------------


def test_trend_refuses_to_draw_a_line_through_two_points():
    result = market_service.measure_trend(series([(5, 100), (200, 80)]), today=TODAY, params=PARAMS)
    assert result.direction == TrendDirection.INSUFFICIENT_DATA.value
    assert result.confidence == Confidence.NONE.value


def test_a_rising_market_reads_as_rising():
    rising = series(
        [(170, 100), (160, 102), (150, 98), (20, 140), (10, 145), (5, 150), (2, 148)]
    )
    result = market_service.measure_trend(rising, today=TODAY, params=PARAMS)
    assert result.direction in {TrendDirection.UP.value, TrendDirection.STRONG_UP.value}
    assert result.changes[90] is not None and result.changes[90] > 0
    # Nothing sold in the 180-360 day window, so there is nothing to compare
    # the last 180 days against and the horizon reports None rather than 0%.
    assert result.changes[180] is None


def test_a_falling_market_reads_as_falling():
    falling = series([(170, 200), (160, 195), (150, 205), (20, 120), (10, 118), (5, 122)])
    result = market_service.measure_trend(falling, today=TODAY, params=PARAMS)
    assert result.direction in {TrendDirection.DOWN.value, TrendDirection.STRONG_DOWN.value}


def test_a_flat_market_reads_as_stable():
    flat = series([(day, 100) for day in range(1, 180, 6)])
    result = market_service.measure_trend(flat, today=TODAY, params=PARAMS)
    assert result.direction == TrendDirection.STABLE.value


def test_trend_confidence_reflects_how_many_sales_it_saw():
    thin = market_service.measure_trend(
        series([(150, 100), (140, 100), (10, 150), (5, 150)]), today=TODAY, params=PARAMS
    )
    thick = market_service.measure_trend(
        series([(120 + day, 100) for day in range(20)] + [(day, 150) for day in range(1, 21)]),
        today=TODAY,
        params=PARAMS,
    )
    assert _rank(thin.confidence) < _rank(thick.confidence)


def _rank(confidence: str) -> int:
    return [
        Confidence.NONE.value,
        Confidence.LOW.value,
        Confidence.MEDIUM.value,
        Confidence.HIGH.value,
    ].index(confidence)
