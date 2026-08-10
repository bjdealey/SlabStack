"""Money must never lose or invent a penny."""

from __future__ import annotations

import pytest

from app.money import allocate, apply_pct, to_major, to_minor


@pytest.mark.parametrize(
    ("major", "minor"),
    [(18.80, 1880), (0.1, 10), (0.005, 1), (1234.565, 123457), (0, 0), (None, None)],
)
def test_to_minor_rounds_half_up(major, minor):
    assert to_minor(major) == minor


def test_round_trip():
    assert to_major(to_minor(16.80)) == 16.80


def test_float_addition_that_would_drift_stays_exact():
    # 0.1 + 0.2 != 0.3 in floats; in minor units it is exactly 30.
    assert to_minor(0.1) + to_minor(0.2) == to_minor(0.3)


def test_apply_pct():
    assert apply_pct(10000, 12.0) == 1200
    assert apply_pct(1999, 5.0) == 100  # 99.95p rounds to 100


class TestAllocate:
    def test_equal_split_keeps_every_penny(self):
        parts = allocate(1000, [1, 1, 1])
        assert sum(parts) == 1000
        assert sorted(parts) == [333, 333, 334]

    def test_value_weighted(self):
        parts = allocate(5300, [10000, 20000, 35000, 8000])
        assert sum(parts) == 5300
        # The most valuable card absorbs the largest share of shipping/insurance.
        assert parts[2] == max(parts)

    def test_zero_weights_falls_back_to_equal(self):
        parts = allocate(100, [0, 0, 0])
        assert sum(parts) == 100

    def test_no_cards(self):
        assert allocate(500, []) == []
