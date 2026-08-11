"""The decision engine, tested as pure functions.

This is the module the whole application exists to produce, so it is exercised
directly rather than only through HTTP: the arithmetic that tells someone to
spend £54 should be checkable without a database.

The tests are written against the spec's core principle — expected,
risk-adjusted, *realisable* profit — so several of them are deliberately about
what the engine refuses to say.
"""

from __future__ import annotations

import pytest

from app.enums import Confidence, Decision, RiskTolerance, TrendDirection
from app.money import to_minor
from app.services import decision
from app.services.decision import DecisionInputs, Thresholds


def LABEL(code: str, grade: float) -> str:
    return f"{code} {grade:g}"


def inputs(**overrides) -> DecisionInputs:
    defaults = {
        "raw_net_minor": to_minor(180),
        "raw_value_minor": to_minor(200),
        "liquidity_score": 8.0,
        "trend_direction": TrendDirection.STABLE.value,
        "trend_confidence": Confidence.MEDIUM.value,
        "market_confidence": Confidence.HIGH.value,
        "grade_confidence": Confidence.HIGH.value,
        "sales_by_label": {"PSA 10": 12, "PSA 9": 8},
        "market_recognition": {"PSA": 9.5, "CGC": 7.5, "ACE": 5.5},
    }
    defaults.update(overrides)
    return DecisionInputs(**defaults)


def route(
    *,
    code: str = "PSA",
    cost: float = 25.0,
    probabilities: dict[float, float] | None = None,
    nets: dict[str, float] | None = None,
    thresholds: Thresholds | None = None,
    data: DecisionInputs | None = None,
    tier: str = "Value",
):
    return decision.evaluate_route(
        company_id=f"{code}-id",
        company_code=code,
        tier_id=f"{code}-tier",
        tier_name=tier,
        cost_minor=to_minor(cost) or 0,
        probabilities=probabilities or {10.0: 0.5, 9.0: 0.5},
        net_by_label={key: to_minor(value) for key, value in (nets or {}).items()},
        label_for=LABEL,
        inputs=data or inputs(),
        thresholds=thresholds or Thresholds(),
    )


# --- Expected value ----------------------------------------------------------


def test_expected_value_is_probability_weighted_not_the_best_case():
    """A card that is a 10 one time in ten is not a 10."""
    result = route(
        probabilities={10.0: 0.1, 9.0: 0.9},
        nets={"PSA 10": 900, "PSA 9": 300},
    )
    # 0.1 * 900 + 0.9 * 300 = 360
    assert result.expected_net_minor == to_minor(360)
    assert result.expected_net_minor < to_minor(900)


def test_profit_is_measured_against_selling_it_raw():
    """Grading to gain £5 over this afternoon's raw sale is a £5 win, not £400."""
    result = route(cost=25.0, probabilities={10.0: 1.0}, nets={"PSA 10": 400})
    # 400 net, less the 25 fee, less the 180 you would have got raw
    assert result.expected_profit_minor == to_minor(195)


def test_a_slab_worth_less_than_the_raw_card_is_a_loss():
    result = route(cost=25.0, probabilities={6.0: 1.0}, nets={"PSA 6": 150})
    assert result.expected_profit_minor == to_minor(-55)
    assert result.probability_of_profit == 0.0


def test_grades_with_no_sales_are_unknown_not_worthless():
    """No PSA 8 price must not drag the expectation toward zero."""
    result = route(
        probabilities={10.0: 0.5, 8.0: 0.5},
        nets={"PSA 10": 900},
    )
    assert result.coverage == 0.5
    assert result.expected_net_minor == to_minor(900), "renormalised over the covered half"
    assert any("50%" in note for note in result.notes)


def test_a_route_with_nothing_priced_produces_nothing():
    result = route(probabilities={10.0: 1.0}, nets={})
    assert result.expected_profit_minor is None
    assert result.opportunity_score is None
    assert "nothing to expect" in result.notes[0]


def test_roi_is_measured_on_the_fee_you_choose_to_spend():
    result = route(cost=25.0, probabilities={10.0: 1.0}, nets={"PSA 10": 280})
    # £75 profit on a £25 fee
    assert result.expected_profit_minor == to_minor(75)
    assert result.roi_pct == 300.0


# --- Minimum profitable grade and probabilities ------------------------------


def test_the_minimum_profitable_grade_is_the_lowest_that_beats_raw():
    result = route(
        cost=25.0,
        probabilities={10.0: 0.3, 9.0: 0.4, 8.0: 0.2, 7.0: 0.1},
        nets={"PSA 10": 900, "PSA 9": 400, "PSA 8": 200, "PSA 7": 150},
    )
    assert result.minimum_profitable_grade == 9.0
    assert result.probability_at_or_above_minimum == pytest.approx(0.7)
    assert result.probability_of_profit == pytest.approx(0.7)


def test_probability_of_clearing_each_profit_level():
    result = route(
        cost=25.0,
        probabilities={10.0: 0.4, 9.0: 0.4, 8.0: 0.2},
        nets={"PSA 10": 500, "PSA 9": 260, "PSA 8": 210},
    )
    # Profits: 295, 55, 5
    assert result.probability_of_target["25"] == pytest.approx(0.8)
    assert result.probability_of_target["50"] == pytest.approx(0.8)
    assert result.probability_of_target["100"] == pytest.approx(0.4)


# --- Downside and upside -----------------------------------------------------


def test_the_downside_is_a_percentile_not_the_worst_grade_on_the_ladder():
    """A six-per-cent chance of a 3 is a tail, not a forecast."""
    result = route(
        cost=25.0,
        probabilities={10.0: 0.5, 9.0: 0.44, 3.0: 0.06},
        nets={"PSA 10": 900, "PSA 9": 400, "PSA 3": 40},
    )
    # The disaster is only 6% likely, so it sits below the 10th percentile and
    # does not define the downside — which is the whole point of using one.
    assert result.distribution.priced[-1].profit_minor == to_minor(-165)
    assert result.downside_minor == to_minor(195)
    assert result.upside_minor == to_minor(695)


def test_a_downside_big_enough_to_be_likely_does_show_up():
    """The tail is ignored for being unlikely, not for being bad."""
    result = route(
        cost=25.0,
        probabilities={10.0: 0.5, 9.0: 0.35, 3.0: 0.15},
        nets={"PSA 10": 900, "PSA 9": 400, "PSA 3": 40},
    )
    assert result.downside_minor == to_minor(-165)


def test_risk_tolerance_moves_the_downside_percentile_not_the_maths():
    probabilities = {10.0: 0.5, 9.0: 0.42, 4.0: 0.08}
    nets = {"PSA 10": 900, "PSA 9": 400, "PSA 4": 60}

    cautious = route(
        probabilities=probabilities, nets=nets,
        thresholds=Thresholds(risk_tolerance=RiskTolerance.CONSERVATIVE.value),
    )
    bold = route(
        probabilities=probabilities, nets=nets,
        thresholds=Thresholds(risk_tolerance=RiskTolerance.AGGRESSIVE.value),
    )
    assert cautious.expected_profit_minor == bold.expected_profit_minor
    # The careful reading reaches further into the tail and so looks worse.
    assert cautious.downside_minor < bold.downside_minor
    assert cautious.downside_minor == to_minor(-145)


# --- Slab liquidity ----------------------------------------------------------


def test_a_grader_whose_slabs_never_trade_scores_low_on_liquidity():
    traded = route(code="PSA", nets={"PSA 10": 900, "PSA 9": 400})
    untraded = route(
        code="ACE",
        probabilities={10.0: 0.5, 9.0: 0.5},
        nets={"ACE 10": 900, "ACE 9": 400},
    )
    assert traded.slab_liquidity > untraded.slab_liquidity


def test_a_graders_reputation_cannot_make_an_untraded_card_liquid():
    illiquid = inputs(liquidity_score=1.5)
    result = route(nets={"PSA 10": 900, "PSA 9": 400}, data=illiquid)
    assert result.slab_liquidity <= 1.5


# --- The decision ------------------------------------------------------------


def clearly_worth_it(**overrides):
    return route(cost=25.0, probabilities={10.0: 0.7, 9.0: 0.3},
                 nets={"PSA 10": 900, "PSA 9": 400}, **overrides)


def test_a_clearly_profitable_card_is_a_grade():
    result = decision.decide(
        [clearly_worth_it()], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    assert result.decision == Decision.GRADE.value
    assert result.chosen.company_code == "PSA"
    assert "Grade with PSA" in result.headline


def test_a_cheap_card_is_never_evaluated():
    result = decision.decide(
        [clearly_worth_it()],
        inputs=inputs(raw_value_minor=to_minor(8)),
        thresholds=Thresholds(),
        batch_size=1,
    )
    assert result.decision == Decision.DO_NOT_GRADE.value
    assert "value floor" in result.reasons[0]


def test_a_card_with_no_value_at_all_asks_for_one():
    result = decision.decide(
        [clearly_worth_it()],
        inputs=inputs(raw_value_minor=None),
        thresholds=Thresholds(),
        batch_size=1,
    )
    assert result.decision == Decision.INSUFFICIENT_DATA.value
    assert result.blockers


def test_a_marginal_card_alone_becomes_worth_it_in_a_batch():
    """The difference between 'not worth grading' and 'not worth grading alone'."""
    alone = route(cost=85.0, probabilities={10.0: 0.7, 9.0: 0.3},
                  nets={"PSA 10": 300, "PSA 9": 250})
    batched = route(cost=25.0, probabilities={10.0: 0.7, 9.0: 0.3},
                    nets={"PSA 10": 300, "PSA 9": 250})

    result = decision.decide(
        [alone], inputs=inputs(), thresholds=Thresholds(),
        batch_size=1, routes_if_batched=[batched],
    )
    assert result.decision == Decision.GRADE_IF_BATCH_FILLED.value
    assert "not on its own" in result.headline
    assert result.chosen.cost_minor == to_minor(25)


def test_an_unprofitable_card_in_a_rising_market_is_a_hold():
    thin = route(cost=25.0, probabilities={9.0: 1.0}, nets={"PSA 9": 200})
    result = decision.decide(
        [thin],
        inputs=inputs(trend_direction=TrendDirection.STRONG_UP.value),
        thresholds=Thresholds(),
        batch_size=25,
    )
    assert result.decision == Decision.HOLD.value
    assert result.review_in_days == 30


def test_an_unprofitable_illiquid_card_is_kept_rather_than_listed():
    thin = route(cost=25.0, probabilities={9.0: 1.0}, nets={"PSA 9": 200})
    result = decision.decide(
        [thin],
        inputs=inputs(liquidity_score=1.0),
        thresholds=Thresholds(),
        batch_size=25,
    )
    assert result.decision == Decision.KEEP_RAW.value
    assert "barely trades" in result.headline


def test_an_unprofitable_liquid_card_is_sold_raw():
    thin = route(cost=25.0, probabilities={9.0: 1.0}, nets={"PSA 9": 200})
    result = decision.decide(
        [thin], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    assert result.decision == Decision.SELL_RAW.value


# --- The liquidity-aware tie-break (spec section 26) -------------------------


def test_the_richer_route_loses_to_the_one_that_actually_sells_and_is_told_why():
    """The signature behaviour: profit you cannot realise is not profit."""
    data = inputs(
        sales_by_label={"PSA 10": 30, "PSA 9": 20, "CGC 10": 0},
        market_recognition={"PSA": 9.5, "CGC": 7.5},
    )
    psa = route(code="PSA", cost=25.0, probabilities={10.0: 0.6, 9.0: 0.4},
                nets={"PSA 10": 700, "PSA 9": 380}, data=data)
    cgc = route(code="CGC", cost=25.0, probabilities={10.0: 0.6, 9.0: 0.4},
                nets={"CGC 10": 900, "CGC 9": 460}, data=data)

    assert (cgc.expected_profit_minor or 0) > (psa.expected_profit_minor or 0)

    result = decision.decide([psa, cgc], inputs=data, thresholds=Thresholds(), batch_size=25)
    assert result.chosen.company_code == "PSA", "the liquid slab wins"
    assert result.alternative.company_code == "CGC"
    assert "liquidity" in result.alternative_note
    assert "profit you cannot realise is not profit" in result.alternative_note


def test_the_alternative_is_surfaced_never_hidden():
    data = inputs(sales_by_label={"PSA 10": 30, "CGC 10": 0})
    psa = route(code="PSA", probabilities={10.0: 1.0}, nets={"PSA 10": 700}, data=data)
    cgc = route(code="CGC", probabilities={10.0: 1.0}, nets={"CGC 10": 1200}, data=data)

    result = decision.decide([psa, cgc], inputs=data, thresholds=Thresholds(), batch_size=25)
    assert result.alternative is not None
    assert result.alternative_note.startswith("CGC")
    assert "more expected profit" in result.alternative_note


def test_no_alternative_when_the_best_route_is_also_the_richest():
    data = inputs(sales_by_label={"PSA 10": 30, "CGC 10": 30})
    psa = route(code="PSA", probabilities={10.0: 1.0}, nets={"PSA 10": 900}, data=data)
    cgc = route(code="CGC", probabilities={10.0: 1.0}, nets={"CGC 10": 400}, data=data)

    result = decision.decide([psa, cgc], inputs=data, thresholds=Thresholds(), batch_size=25)
    assert result.chosen.company_code == "PSA"
    assert result.alternative is None


# --- Thresholds and risk -----------------------------------------------------


def test_a_conservative_user_needs_the_grade_to_land_more_often():
    """Same card, same arithmetic, different bar."""
    # Lands profitably 68% of the time: over the balanced bar of 60%, under
    # the 75% a conservative profile asks for.
    coin_flip = route(
        cost=25.0,
        probabilities={10.0: 0.68, 7.0: 0.32},
        nets={"PSA 10": 900, "PSA 7": 100},
    )
    balanced = decision.decide(
        [coin_flip], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    cautious = decision.decide(
        [coin_flip],
        inputs=inputs(),
        thresholds=Thresholds(risk_tolerance=RiskTolerance.CONSERVATIVE.value).for_risk(
            RiskTolerance.CONSERVATIVE.value
        ),
        batch_size=25,
    )
    assert balanced.decision == Decision.GRADE.value
    assert cautious.decision != Decision.GRADE.value
    assert "below your minimum of 75%" in cautious.reasons[0]


def test_the_reason_a_route_failed_names_the_bar_it_missed():
    thin = route(cost=25.0, probabilities={9.0: 1.0}, nets={"PSA 9": 210})
    result = decision.decide(
        [thin], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    assert "below your minimum" in result.reasons[0]


def test_score_components_are_reported_so_the_number_can_be_argued_with():
    result = clearly_worth_it()
    assert set(result.score_parts) == {
        "profitability", "grade_probability", "liquidity", "trend", "risk"
    }
    assert 0 <= result.opportunity_score <= 100


def test_confidence_is_the_weakest_link():
    thin_market = route(
        nets={"PSA 10": 900, "PSA 9": 400},
        data=inputs(market_confidence=Confidence.LOW.value),
    )
    assert thin_market.confidence == Confidence.LOW.value


def test_partial_coverage_drags_confidence_down():
    result = route(
        probabilities={10.0: 0.4, 9.0: 0.3, 8.0: 0.3},
        nets={"PSA 10": 900},
    )
    assert result.coverage == 0.4
    assert result.confidence == Confidence.LOW.value


# --- Coverage honesty --------------------------------------------------------


def test_probability_of_profit_is_not_renormalised_over_what_we_can_price():
    """'Profitable 100% of the time' from 40% coverage is not a claim we can make."""
    result = route(
        cost=25.0,
        probabilities={10.0: 0.4, 9.0: 0.35, 8.0: 0.25},
        nets={"PSA 10": 900},
    )
    assert result.coverage == pytest.approx(0.4)
    # Every priced outcome is profitable, but that is only 40% of the card.
    assert result.probability_of_profit == pytest.approx(0.4)
    assert result.probability_of_profit != 1.0


def test_a_thin_sample_is_reported_as_missing_data_not_a_bad_card():
    """They need different actions: one is 'do not grade', the other is 'go and look'."""
    thin = route(
        cost=25.0,
        probabilities={10.0: 0.4, 9.0: 0.35, 8.0: 0.25},
        nets={"PSA 10": 900},
    )
    result = decision.decide([thin], inputs=inputs(), thresholds=Thresholds(), batch_size=25)

    assert "Every grade with sales behind it is profitable" in result.reasons[0]
    assert "Add PSA 9, PSA 8 sales" in result.reasons[0]
    assert any("60% of this card's likely outcomes" in item for item in result.blockers)


def test_a_genuinely_unprofitable_card_is_not_blamed_on_missing_data():
    fully_priced = route(
        cost=25.0,
        probabilities={10.0: 0.3, 9.0: 0.7},
        nets={"PSA 10": 260, "PSA 9": 190},
    )
    result = decision.decide(
        [fully_priced], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    assert "Every grade with sales behind it" not in result.reasons[0]
    assert result.blockers == []


def test_the_probability_of_reaching_a_grade_ignores_whether_it_has_sold():
    """'How often does it come back a 9?' is answerable without a 9 ever selling."""
    result = route(
        cost=25.0,
        probabilities={10.0: 0.3, 9.0: 0.4, 8.0: 0.3},
        nets={"PSA 10": 900, "PSA 9": 400},
    )
    assert result.minimum_profitable_grade == 9.0
    # 30% land a 10 and 40% a 9 — the unpriced 8 does not reduce that.
    assert result.probability_at_or_above_minimum == pytest.approx(0.7)


def test_two_routes_that_both_max_the_score_are_split_by_profit():
    rich = route(code="PSA", cost=25.0, probabilities={10.0: 1.0}, nets={"PSA 10": 900})
    poor = route(code="PSA", cost=50.0, probabilities={10.0: 1.0}, nets={"PSA 10": 900},
                 tier="Express")
    assert rich.opportunity_score == poor.opportunity_score

    result = decision.decide(
        [poor, rich], inputs=inputs(), thresholds=Thresholds(), batch_size=25
    )
    assert result.chosen.tier_name == "Value", "the cheaper route wins the tie"
