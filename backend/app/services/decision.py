"""The decision engine (spec sections 24-31, 33).

Everything before this phase measured something. This module is the one that
answers the question the application exists for: *should I grade this card, sell
it raw, or hold it?*

The spec's core principle governs every line of it:

    Don't optimise for theoretical card value. Optimise for expected,
    risk-adjusted, realistically realisable profit after grading, selling,
    submission and liquidity costs.

Four things follow from that, and they are the reason this is not simply
``max(expected_value)``:

**Expected, not best.** A card that grades a 10 one time in ten and an 8 the
other nine is an 8. Every figure here is probability-weighted across the whole
grade distribution, and the best case is reported separately and labelled.

**Realisable, not theoretical.** Profit is measured against *selling it raw*,
because that is the alternative you actually have. Grading a card to gain £5
over what you could get for it this afternoon is not a £200 win, it is a £5 win
that ties up your money for six weeks.

**Risk-adjusted.** The downside is a real percentile of the outcome
distribution, not the worst grade on the ladder — a 1% chance of a 3 should not
define a card. Risk tolerance shifts the thresholds rather than the arithmetic.

**After liquidity costs.** A slab you cannot sell is not profit. When one
grader shows more paper profit but another's slabs actually trade, the engine
recommends the one that sells and says so, with the richer route surfaced as
the alternative rather than quietly dropped (spec section 26).

Nothing here invents a number. Grades with no sales behind them are *unknown*
rather than worthless: the expectation is taken over the outcomes that can be
priced, and the share of the distribution that covers is reported with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.enums import Confidence, Decision, RiskTolerance, TrendDirection
from app.money import to_minor

__all__ = [
    "DecisionInputs",
    "DecisionResult",
    "OutcomeDistribution",
    "RouteOutcome",
    "Thresholds",
    "decide",
    "evaluate_route",
]

#: Profit levels the spec asks for explicitly (section 25), in major units.
PROFIT_LADDER: tuple[float, ...] = (25.0, 50.0, 100.0)


# --- Thresholds and risk tolerance -------------------------------------------


@dataclass
class Thresholds:
    """What the user requires before grading is worth it.

    Risk tolerance shifts these rather than changing any arithmetic: a
    conservative user needs the grade to land profitably more often and
    penalises an illiquid slab harder, but the expected value is the same
    number either way.
    """

    minimum_roi_pct: float = 25.0
    minimum_absolute_profit_minor: int = 2500
    minimum_probability_of_profit: float = 0.60
    minimum_liquidity_score: float = 3.0
    grading_value_floor_minor: int = 1500
    hold_recheck_days: int = 30
    risk_tolerance: str = RiskTolerance.BALANCED.value
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "profitability": 35.0,
            "grade_probability": 25.0,
            "liquidity": 20.0,
            "trend": 10.0,
            "risk": 10.0,
        }
    )

    @classmethod
    def from_settings(cls, values: dict) -> Thresholds:
        risk = values.get("risk_tolerance", RiskTolerance.BALANCED.value)
        base = cls(
            minimum_roi_pct=float(values.get("minimum_roi_pct", 25.0)),
            minimum_absolute_profit_minor=to_minor(values.get("minimum_absolute_profit", 25.0)) or 0,
            minimum_probability_of_profit=float(values.get("minimum_probability_of_profit", 60.0))
            / 100,
            minimum_liquidity_score=float(values.get("minimum_liquidity_score", 3.0)),
            grading_value_floor_minor=to_minor(values.get("grading_value_floor", 15.0)) or 0,
            hold_recheck_days=int(values.get("hold_recheck_days", 30)),
            risk_tolerance=risk,
            weights=dict(values.get("decision_score_weights") or {}) or cls().weights,
        )
        return base.for_risk(risk)

    def for_risk(self, risk: str) -> Thresholds:
        """Shift the bar, not the maths."""
        if risk == RiskTolerance.CONSERVATIVE.value:
            self.minimum_probability_of_profit = min(0.95, self.minimum_probability_of_profit + 0.15)
            self.minimum_liquidity_score = self.minimum_liquidity_score + 1.5
        elif risk == RiskTolerance.AGGRESSIVE.value:
            self.minimum_probability_of_profit = max(0.05, self.minimum_probability_of_profit - 0.15)
            self.minimum_liquidity_score = max(0.0, self.minimum_liquidity_score - 1.0)
        return self

    @property
    def downside_percentile(self) -> float:
        """Which percentile counts as "the worst reasonable outcome".

        A *lower* percentile reaches further into the bad tail, so the
        conservative reading is the lower number: someone who wants to be
        careful should be shown how bad it can plausibly get, not a milder bad
        case. Aggressive is the reverse — willing to wear the tail, so only the
        more likely disappointments count.
        """
        return {
            RiskTolerance.CONSERVATIVE.value: 0.05,
            RiskTolerance.AGGRESSIVE.value: 0.20,
        }.get(self.risk_tolerance, 0.10)


# --- The outcome distribution ------------------------------------------------


@dataclass
class Outcome:
    """One grade, its probability, and what it would actually leave you with."""

    grade: float
    label: str
    probability: float
    gross_minor: int | None = None
    net_minor: int | None = None
    #: Profit against selling the card raw today, after the grading cost.
    profit_minor: int | None = None


@dataclass
class OutcomeDistribution:
    outcomes: list[Outcome] = field(default_factory=list)
    #: Share of the grade distribution we have a price for. The rest is
    #: unknown, not worthless.
    coverage: float = 0.0

    @property
    def priced(self) -> list[Outcome]:
        return [item for item in self.outcomes if item.net_minor is not None]

    def expectation(self, pick) -> float | None:
        """Probability-weighted mean over the priced outcomes only."""
        priced = self.priced
        if not priced or self.coverage <= 0:
            return None
        total = sum(pick(item) * item.probability for item in priced)
        return total / self.coverage

    def probability_where(self, predicate) -> float | None:
        """Probability that an outcome satisfies ``predicate``, **unconditionally**.

        Deliberately *not* renormalised over the priced outcomes, unlike the
        expectations. "This is profitable 100% of the time" is a very different
        claim from "100% of the 40% of outcomes we can price are profitable",
        and the first one is what a reader hears.

        So the grades with no sales behind them count against the probability
        rather than being assumed to behave. Unknown is not good news.
        """
        priced = self.priced
        if not priced:
            return None
        return sum(item.probability for item in priced if predicate(item))

    def probability_of_grade(self, predicate) -> float | None:
        """Probability over *every* outcome, priced or not.

        For questions about the grade itself — "how often does it come back a 9
        or better?" — which we can answer whether or not a 9 has ever sold.
        """
        if not self.outcomes:
            return None
        return sum(item.probability for item in self.outcomes if predicate(item))

    def percentile_profit(self, fraction: float) -> int | None:
        """Profit at a percentile of the outcome distribution.

        Used for downside and upside instead of the worst and best grades on
        the ladder: a one-per-cent chance of a 3 is a tail, not a forecast.
        """
        priced = sorted(self.priced, key=lambda item: item.profit_minor or 0)
        if not priced or self.coverage <= 0:
            return None
        target = fraction * self.coverage
        running = 0.0
        for item in priced:
            running += item.probability
            if running >= target:
                return item.profit_minor
        return priced[-1].profit_minor


def build_distribution(
    probabilities: dict[float, float],
    net_by_label: dict[str, int],
    *,
    company_code: str,
    label_for,
    cost_minor: int,
    raw_net_minor: int,
    gross_by_label: dict[str, int] | None = None,
) -> OutcomeDistribution:
    """Turn P(grade) and net-per-slab into what each outcome is actually worth.

    ``profit`` is measured against selling the card raw today, because that is
    the alternative the user actually has. A slab worth £400 when the raw card
    fetches £380 and grading costs £25 is a loss, however good the grade looks.

    The gross is carried alongside the net so the working can be shown: seeing
    the sale price next to what you keep is how the selling costs stop being
    invisible.
    """
    distribution = OutcomeDistribution()
    for grade, probability in sorted(probabilities.items(), reverse=True):
        label = label_for(company_code, float(grade))
        net = net_by_label.get(label)
        outcome = Outcome(grade=float(grade), label=label, probability=float(probability))
        outcome.gross_minor = (gross_by_label or {}).get(label)
        if net is not None:
            outcome.net_minor = net
            outcome.profit_minor = net - cost_minor - raw_net_minor
            distribution.coverage += float(probability)
        distribution.outcomes.append(outcome)
    return distribution


# --- One grading route -------------------------------------------------------


@dataclass
class RouteOutcome:
    """What one (company, tier) route is expected to produce."""

    company_id: str
    company_code: str
    tier_id: str | None = None
    tier_name: str | None = None
    cost_minor: int = 0
    #: The submission this costing assumes. A route is not just a company and a
    #: tier: the same tier costs different money in a batch of 1 and a batch of
    #: 20, so the number has to travel with the batch it came from.
    batch_size: int = 1

    expected_net_minor: int | None = None
    expected_profit_minor: int | None = None
    roi_pct: float | None = None
    probability_of_profit: float | None = None
    probability_of_target: dict[str, float] = field(default_factory=dict)
    minimum_profitable_grade: float | None = None
    probability_at_or_above_minimum: float | None = None
    downside_minor: int | None = None
    upside_minor: int | None = None

    coverage: float = 0.0
    slab_liquidity: float | None = None
    slab_sales: int = 0
    opportunity_score: float | None = None
    score_parts: dict[str, float] = field(default_factory=dict)
    confidence: str = Confidence.NONE.value
    distribution: OutcomeDistribution = field(default_factory=OutcomeDistribution)
    notes: list[str] = field(default_factory=list)

    @property
    def unpriced_labels(self) -> list[str]:
        """Grades this card might get that have no sales behind them."""
        return [
            item.label
            for item in sorted(
                self.distribution.outcomes, key=lambda row: row.probability, reverse=True
            )
            if item.net_minor is None
        ]


@dataclass
class DecisionInputs:
    """Everything the engine needs, already computed by earlier phases."""

    raw_net_minor: int | None
    raw_value_minor: int | None
    liquidity_score: float | None
    trend_direction: str
    trend_confidence: str
    market_confidence: str
    grade_confidence: str
    #: Sales counted per grade label, for per-company slab liquidity.
    sales_by_label: dict[str, int] = field(default_factory=dict)
    market_recognition: dict[str, float] = field(default_factory=dict)


def evaluate_route(
    *,
    company_id: str,
    company_code: str,
    tier_id: str | None,
    tier_name: str | None,
    cost_minor: int,
    probabilities: dict[float, float],
    net_by_label: dict[str, int],
    label_for,
    inputs: DecisionInputs,
    thresholds: Thresholds,
    batch_size: int = 1,
    gross_by_label: dict[str, int] | None = None,
) -> RouteOutcome:
    """Expected value and risk for one grading route."""
    raw_net = inputs.raw_net_minor or 0
    distribution = build_distribution(
        probabilities,
        net_by_label,
        company_code=company_code,
        label_for=label_for,
        cost_minor=cost_minor,
        raw_net_minor=raw_net,
        gross_by_label=gross_by_label,
    )

    route = RouteOutcome(
        company_id=company_id,
        company_code=company_code,
        tier_id=tier_id,
        tier_name=tier_name,
        cost_minor=cost_minor,
        batch_size=batch_size,
        coverage=round(distribution.coverage, 4),
        distribution=distribution,
    )

    if not distribution.priced:
        route.notes.append(
            f"No {company_code} sales stored for any grade this card might get, so there is "
            "nothing to expect."
        )
        return route

    expected_net = distribution.expectation(lambda item: item.net_minor or 0)
    expected_profit = distribution.expectation(lambda item: item.profit_minor or 0)
    route.expected_net_minor = round(expected_net) if expected_net is not None else None
    route.expected_profit_minor = round(expected_profit) if expected_profit is not None else None

    # ROI is measured on the grading fee — the money you are choosing to spend.
    # The card's own value is not "returned" by grading, it is carried through.
    if route.expected_profit_minor is not None and cost_minor > 0:
        route.roi_pct = round(route.expected_profit_minor / cost_minor * 100, 1)

    route.probability_of_profit = distribution.probability_where(
        lambda item: (item.profit_minor or 0) > 0
    )
    for target in PROFIT_LADDER:
        target_minor = to_minor(target) or 0
        probability = distribution.probability_where(
            lambda item, threshold=target_minor: (item.profit_minor or 0) >= threshold
        )
        if probability is not None:
            route.probability_of_target[f"{target:g}"] = round(probability, 4)

    profitable = [item for item in distribution.priced if (item.profit_minor or 0) > 0]
    if profitable:
        floor = min(profitable, key=lambda item: item.grade)
        route.minimum_profitable_grade = floor.grade
        # A question about the grade, not the price: we know how often it comes
        # back a 9 whether or not a 9 has ever sold.
        route.probability_at_or_above_minimum = distribution.probability_of_grade(
            lambda item, cut=floor.grade: item.grade >= cut
        )

    route.downside_minor = distribution.percentile_profit(thresholds.downside_percentile)
    route.upside_minor = distribution.percentile_profit(0.90)

    # How readily *this grader's* slabs actually trade. The card's overall
    # liquidity says people want the card; this says they want it in this slab.
    route.slab_sales = sum(
        count
        for label, count in inputs.sales_by_label.items()
        if label.split(" ")[0].upper() == company_code.upper()
    )
    route.slab_liquidity = _slab_liquidity(
        route.slab_sales,
        inputs.market_recognition.get(company_code, 5.0),
        inputs.liquidity_score,
    )

    route.score_parts, route.opportunity_score = _score(route, inputs, thresholds)
    route.confidence = _route_confidence(route, inputs)

    if route.coverage < 0.999:
        route.notes.append(
            f"Priced against {route.coverage:.0%} of the likely grades — the rest have no "
            f"{company_code} sales stored, so they are left out rather than counted as zero."
        )
    return route


def _slab_liquidity(sales: int, recognition: float, card_liquidity: float | None) -> float | None:
    """0-10: how readily this grader's slab of this card would sell.

    Blends what the market has actually done (sales of this grader's slabs) with
    how widely the grader is accepted, which is the only signal available before
    any of its slabs have traded. The card's own liquidity caps it: a grader's
    reputation cannot make an untraded card liquid.
    """
    if card_liquidity is None and sales == 0:
        return None
    observed = min(10.0, sales * 1.2) if sales else None
    # With no observed sales the recognition score is all there is, and it is a
    # weaker claim, so it is discounted rather than taken at face value.
    blended = observed if observed is not None else recognition * 0.6
    if observed is not None and sales < 5:
        blended = (observed + recognition * 0.6) / 2
    if card_liquidity is not None:
        blended = min(blended, card_liquidity)
    return round(max(0.0, min(10.0, blended)), 1)


# --- The composite score (spec section 27) -----------------------------------

_TREND_POINTS = {
    TrendDirection.STRONG_UP.value: 10.0,
    TrendDirection.UP.value: 7.5,
    TrendDirection.STABLE.value: 5.0,
    TrendDirection.DOWN.value: 2.5,
    TrendDirection.STRONG_DOWN.value: 0.0,
    TrendDirection.INSUFFICIENT_DATA.value: 5.0,
}

_CONFIDENCE_POINTS = {
    Confidence.HIGH.value: 10.0,
    Confidence.MEDIUM.value: 7.0,
    Confidence.LOW.value: 4.0,
    Confidence.NONE.value: 1.0,
}


def _score(
    route: RouteOutcome, inputs: DecisionInputs, thresholds: Thresholds
) -> tuple[dict[str, float], float]:
    """The Grading Opportunity Score: five 0-10 components, user-weighted, out of 100."""
    parts: dict[str, float] = {}

    # Profitability, measured against the user's own bar rather than an
    # absolute scale — "twice what I asked for" is the meaningful statement.
    profit = route.expected_profit_minor or 0
    bar = max(thresholds.minimum_absolute_profit_minor, 1)
    ratio = profit / bar
    parts["profitability"] = max(0.0, min(10.0, 5.0 * ratio)) if ratio > 0 else 0.0

    parts["grade_probability"] = round((route.probability_of_profit or 0.0) * 10, 2)

    parts["liquidity"] = route.slab_liquidity if route.slab_liquidity is not None else 0.0
    parts["trend"] = _TREND_POINTS.get(inputs.trend_direction, 5.0)

    # Risk: how much of the answer rests on evidence, and how far the downside
    # falls. A wide, thinly-priced distribution scores badly even when its
    # expectation is good.
    evidence = (
        _CONFIDENCE_POINTS.get(inputs.market_confidence, 1.0) * 0.5
        + _CONFIDENCE_POINTS.get(inputs.grade_confidence, 1.0) * 0.5
    )
    downside_penalty = 0.0
    if route.downside_minor is not None and route.downside_minor < 0:
        loss = abs(route.downside_minor)
        downside_penalty = min(5.0, loss / max(route.cost_minor, 1) * 2.5)
    parts["risk"] = max(0.0, round(evidence * route.coverage - downside_penalty, 2))

    weights = thresholds.weights
    total_weight = sum(weights.values()) or 100.0
    score = sum(parts.get(key, 0.0) * weight for key, weight in weights.items()) / total_weight
    return {key: round(value, 2) for key, value in parts.items()}, round(score * 10, 1)


def _route_confidence(route: RouteOutcome, inputs: DecisionInputs) -> str:
    """The weakest link: a perfect grade model priced off two sales is a two-sale answer."""
    order = [
        Confidence.NONE.value,
        Confidence.LOW.value,
        Confidence.MEDIUM.value,
        Confidence.HIGH.value,
    ]
    weakest = min(
        (inputs.market_confidence, inputs.grade_confidence), key=order.index
    )
    if route.coverage < 0.5:
        return Confidence.NONE.value if weakest == Confidence.NONE.value else Confidence.LOW.value
    if route.coverage < 0.8 and weakest == Confidence.HIGH.value:
        return Confidence.MEDIUM.value
    return weakest


# --- The decision (spec sections 24-26, 31, 33) ------------------------------


@dataclass
class DecisionResult:
    decision: str = Decision.INSUFFICIENT_DATA.value
    confidence: str = Confidence.NONE.value
    headline: str = ""
    chosen: RouteOutcome | None = None
    alternative: RouteOutcome | None = None
    alternative_note: str | None = None
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    review_in_days: int | None = None


def decide(
    routes: list[RouteOutcome],
    *,
    inputs: DecisionInputs,
    thresholds: Thresholds,
    batch_size: int,
    routes_if_batched: list[RouteOutcome] | None = None,
) -> DecisionResult:
    """Pick a route and a verdict, and be able to justify both.

    ``routes_if_batched`` lets the engine separate "not worth grading" from
    "not worth grading *on its own*" — the difference between `do_not_grade`
    and `grade_if_batch_filled`, and one of the more useful things it can say.
    """
    result = DecisionResult()

    if inputs.raw_value_minor is None:
        result.headline = "No value known for this card yet."
        result.blockers.append("Add comparable sales or your own raw estimate.")
        return result

    if inputs.raw_value_minor < thresholds.grading_value_floor_minor:
        result.decision = Decision.DO_NOT_GRADE.value
        result.confidence = Confidence.HIGH.value
        result.headline = "Too cheap to be worth grading."
        result.reasons.append(
            "The raw card is below your grading value floor, so the fee would swallow it "
            "whatever grade it came back as."
        )
        return result

    priced = [route for route in routes if route.expected_profit_minor is not None]
    if not priced:
        result.headline = "Not enough data to recommend a decision yet."
        result.blockers.append(
            "Add graded sales for at least one grader, so the outcome can be priced rather "
            "than guessed at."
        )
        return result

    # Rank by the composite score, which already carries liquidity and risk.
    ranked = sorted(
        priced,
        key=lambda route: (route.opportunity_score or 0, route.expected_profit_minor or 0),
        reverse=True,
    )
    best = ranked[0]
    result.chosen = best
    result.confidence = best.confidence

    # Spec section 26: the richest route on paper is not always the one to take.
    richest = max(priced, key=lambda route: route.expected_profit_minor or 0)
    if richest is not best and (richest.expected_profit_minor or 0) > (
        best.expected_profit_minor or 0
    ):
        result.alternative = richest
        result.alternative_note = _why_not(richest, best, thresholds)

    for route in ranked:
        if route.coverage < 0.999 and route.unpriced_labels:
            result.blockers.append(
                f"Add {', '.join(route.unpriced_labels[:3])} sales — "
                f"{1 - route.coverage:.0%} of this card's likely outcomes have no "
                f"{route.company_code} price behind them."
            )
            break

    clears = _clears_thresholds(best, thresholds)
    if clears is None:
        result.decision = Decision.GRADE.value
        result.headline = (
            f"Grade with {best.company_code}"
            + (f" {best.tier_name}" if best.tier_name else "")
            + "."
        )
        result.reasons.append(
            f"Expected profit beats selling raw by enough to clear your bar, and the grade "
            f"lands profitably {(best.probability_of_profit or 0):.0%} of the time."
        )
        return result

    # It failed. Would a fuller submission fix it? That is a different answer
    # from "not worth grading".
    if routes_if_batched and batch_size == 1:
        batched = [
            route for route in routes_if_batched if route.expected_profit_minor is not None
        ]
        if batched:
            best_batched = max(
                batched,
                key=lambda route: (route.opportunity_score or 0, route.expected_profit_minor or 0),
            )
            if _clears_thresholds(best_batched, thresholds) is None:
                result.decision = Decision.GRADE_IF_BATCH_FILLED.value
                result.chosen = best_batched
                result.confidence = best_batched.confidence
                result.headline = "Worth grading, but not on its own."
                result.reasons.append(
                    f"Sending it alone costs {_pounds(best.cost_minor)} and does not clear your "
                    f"bar. In a submission of {best_batched.batch_size} it costs "
                    f"{_pounds(best_batched.cost_minor)} and does."
                )
                return result

    # Grading is out. Sell raw, or hold?
    if inputs.trend_direction in {TrendDirection.STRONG_UP.value, TrendDirection.UP.value}:
        result.decision = Decision.HOLD.value
        result.headline = "Hold — grading does not pay, but the market is rising."
        result.reasons.append(clears)
        result.reasons.append(
            f"Raw prices are {inputs.trend_direction.replace('_', ' ')}, so the picture may look "
            "different in a month."
        )
        result.review_in_days = thresholds.hold_recheck_days
        return result

    liquidity = inputs.liquidity_score
    if liquidity is not None and liquidity < thresholds.minimum_liquidity_score:
        result.decision = Decision.KEEP_RAW.value
        result.headline = "Keep it raw — grading does not pay and it barely trades."
        result.reasons.append(clears)
        result.reasons.append(
            f"Liquidity {liquidity:.1f}/10 is below your minimum, so a quick raw sale is not "
            "realistic either."
        )
        return result

    result.decision = Decision.SELL_RAW.value
    result.headline = "Sell it raw."
    result.reasons.append(clears)
    if inputs.raw_net_minor is not None:
        result.reasons.append(
            f"Selling raw nets {_pounds(inputs.raw_net_minor)} today with no fee and no wait."
        )
    return result


def _clears_thresholds(route: RouteOutcome, thresholds: Thresholds) -> str | None:
    """``None`` when the route clears every bar; otherwise the first one it fails."""
    profit = route.expected_profit_minor or 0
    if profit < thresholds.minimum_absolute_profit_minor:
        return (
            f"Expected profit of {_pounds(profit)} over selling raw is below your minimum of "
            f"{_pounds(thresholds.minimum_absolute_profit_minor)}."
        )
    if route.roi_pct is not None and route.roi_pct < thresholds.minimum_roi_pct:
        return (
            f"A {route.roi_pct:.0f}% return on the grading fee is below your minimum of "
            f"{thresholds.minimum_roi_pct:.0f}%."
        )
    probability = route.probability_of_profit or 0
    if probability < thresholds.minimum_probability_of_profit:
        # Distinguish "this card does not grade well enough" from "we cannot
        # see enough of the outcomes to say". They need different actions, and
        # blaming the card for a gap in the data is the wrong answer.
        if route.coverage < 0.999 and probability >= route.coverage - 1e-9:
            missing = ", ".join(route.unpriced_labels[:4]) or "some grades"
            return (
                f"Every grade with sales behind it is profitable, but only {route.coverage:.0%} "
                f"of the likely outcomes have any — so this cannot be confirmed. Add "
                f"{missing} sales."
            )
        return (
            f"It only lands profitably {probability:.0%} of the time, below your minimum of "
            f"{thresholds.minimum_probability_of_profit:.0%}."
        )
    if (
        route.slab_liquidity is not None
        and route.slab_liquidity < thresholds.minimum_liquidity_score
    ):
        return (
            f"{route.company_code} slabs of this card score {route.slab_liquidity:.1f}/10 for "
            f"liquidity, below your minimum of {thresholds.minimum_liquidity_score:.1f}."
        )
    return None


def _why_not(richest: RouteOutcome, chosen: RouteOutcome, thresholds: Thresholds) -> str:
    """Say why the more profitable route lost. Never hide it (spec section 26)."""
    gap = (richest.expected_profit_minor or 0) - (chosen.expected_profit_minor or 0)
    reasons = []
    if (richest.slab_liquidity or 0) < (chosen.slab_liquidity or 0):
        reasons.append(
            f"{richest.company_code} slabs of this card score "
            f"{(richest.slab_liquidity or 0):.1f}/10 for liquidity against "
            f"{chosen.company_code}'s {(chosen.slab_liquidity or 0):.1f} — profit you cannot "
            "realise is not profit"
        )
    if (richest.probability_of_profit or 0) < (chosen.probability_of_profit or 0):
        reasons.append(
            f"it only lands profitably {(richest.probability_of_profit or 0):.0%} of the time "
            f"against {chosen.company_code}'s {(chosen.probability_of_profit or 0):.0%}"
        )
    if (richest.coverage or 0) < (chosen.coverage or 0):
        reasons.append(
            f"only {(richest.coverage or 0):.0%} of its likely grades have sales behind them"
        )
    if not reasons:
        reasons.append("it scores lower once liquidity, trend and risk are weighed in")

    return (
        f"{richest.company_code}"
        + (f" {richest.tier_name}" if richest.tier_name else "")
        + f" shows {_pounds(gap)} more expected profit, but "
        + "; ".join(reasons)
        + "."
    )


def _pounds(minor: int | None) -> str:
    from app.money import format_money

    return format_money(minor)
