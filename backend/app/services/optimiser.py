"""Pack the cards worth grading into submissions that are actually sendable.

The decision engine answers *whether* a card is worth grading. This answers
*how to send it* — and those two questions are entangled in a way that is easy
to get quietly wrong.

Grading costs depend on the batch. A card that clears your bar at twenty-five
may not clear it at six, because six cards carry the postage twenty-five would
have shared. So an optimiser that groups cards by "what the engine recommended"
and stops there is proposing submissions it has never actually costed.

This one does three passes:

1. **Route.** Evaluate every analysable card at a batch big enough for the bulk
   tiers to be on the table, and take the route the engine recommends. That is
   the card's opinion of where it wants to go *if* a batch exists.

2. **Pack.** Group by (company, tier). Each group is a candidate submission, and
   each is checked against that tier's own minimum — which is a minimum at that
   tier, not in the parcel.

3. **Re-verify.** Cost every card again at the size its batch actually came out
   at. Cards that no longer pay are reported, with the reason and the number
   that changed. They are never silently shipped and never silently dropped.

Step 3 is the one that matters. Without it the optimiser produces a plan that
looks profitable and is not, which is precisely the failure the whole
application exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import BlockStatus, Decision
from app.models import Card, ConditionAssessment, GradingCompany, GradingTier, MarketPrice
from app.money import format_money, to_minor
from app.services import evaluation, settings_service

__all__ = ["OptimiserResult", "PlacedCard", "ProposedBatch", "optimise"]

#: Evaluating a card costs about 20ms and the optimiser evaluates each one
#: twice — once to route it, once to re-verify it at its real batch size. Above
#: this many candidates the sweep is cut, and the cut is always reported.
DEFAULT_LIMIT = 150

#: Decisions that mean the card belongs in a submission at all.
_WORTH_GRADING = {Decision.GRADE.value, Decision.GRADE_IF_BATCH_FILLED.value}


@dataclass
class PlacedCard:
    """One card in a proposed batch, costed at that batch's real size."""

    card_id: str
    name: str
    set_label: str | None = None
    company_code: str | None = None
    tier_id: str | None = None
    tier_name: str | None = None
    declared_value: float | None = None

    #: What the engine said when the card was routed, at the hopeful batch size.
    decision_when_routed: str = Decision.INSUFFICIENT_DATA.value
    #: What it says at the size this batch actually came out at.
    decision_in_batch: str = Decision.INSUFFICIENT_DATA.value
    expected_profit: float | None = None
    grading_cost: float | None = None
    opportunity_score: float | None = None

    #: False when the card stopped paying once the real batch size was known.
    still_pays: bool = True
    #: Why it stopped, in the user's terms.
    reason: str | None = None
    #: A cheaper tier for this card in this same batch, when one exists.
    cheaper_tier_name: str | None = None
    cheaper_tier_saving: float | None = None


@dataclass
class ProposedBatch:
    """A submission the optimiser thinks you should build.

    Two tiers, because a batch short of its minimum has two answers and the
    difference between them is the whole point of filling it: ``tier_name`` is
    what these cards were routed to and what they would be graded at once the
    batch is full; ``effective_tier_name`` is what they would actually be
    graded at *today*, at the count the batch currently has.
    """

    company_id: str
    company_code: str
    tier_id: str | None
    tier_name: str | None
    #: What the cards actually land on at the current count. Equal to
    #: ``tier_name`` once the batch is viable.
    effective_tier_name: str | None = None
    card_count: int = 0
    minimum_cards: int = 1
    maximum_cards: int | None = None
    short_by: int = 0

    expected_profit: float | None = None
    grading_cost: float | None = None
    #: What this batch would be worth once filled to the tier's minimum, so
    #: "add sixteen more cards" carries a number rather than an instruction.
    expected_profit_if_filled: float | None = None

    viable: bool = True
    reason: str | None = None
    cards: list[PlacedCard] = field(default_factory=list)


@dataclass
class UnplacedCard:
    """A card worth grading that no proposed batch could take."""

    card_id: str
    name: str
    set_label: str | None = None
    company_code: str | None = None
    tier_name: str | None = None
    expected_profit: float | None = None
    reason: str = ""


@dataclass
class OptimiserResult:
    currency: str = "GBP"
    #: Cards with enough behind them to be decided at all.
    analysable: int = 0
    #: Of those, the ones the engine would grade.
    worth_grading: int = 0
    placed: int = 0
    total_cards: int = 0
    truncated: bool = False
    routed_at_batch_size: int = 1

    expected_profit: float | None = None
    total_grading_cost: float | None = None

    batches: list[ProposedBatch] = field(default_factory=list)
    unplaced: list[UnplacedCard] = field(default_factory=list)
    #: Cards that were worth grading until their batch turned out to be small.
    stopped_paying: list[PlacedCard] = field(default_factory=list)

    status: str = BlockStatus.OK.value
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


def _candidates(db: Session) -> list[Card]:
    """Cards with both a current assessment and a computed price."""
    assessed = select(ConditionAssessment.card_id).where(ConditionAssessment.is_current.is_(True))
    priced = select(MarketPrice.catalog_key)
    return list(
        db.scalars(
            select(Card)
            .where(Card.id.in_(assessed), Card.catalog_key.in_(priced))
            .order_by(Card.updated_at.desc())
        )
    )


def hopeful_batch_size(db: Session) -> int:
    """A batch big enough that every tier is on the table.

    Routing at 1 would hide the bulk tiers entirely and route everything to the
    expensive ones; routing at the largest minimum lets each card say where it
    would go if the batch existed, which is the question this pass is asking.
    """
    minimums = db.scalars(
        select(GradingTier.minimum_cards).where(GradingTier.active.is_(True))
    ).all()
    return max([1, *minimums])


def optimise(db: Session, *, limit: int = DEFAULT_LIMIT) -> OptimiserResult:
    """Propose submissions, then check they still pay at the size they came out."""
    settings_values = settings_service.get_all(db)
    result = OptimiserResult(currency=settings_values.get("currency", "GBP"))
    result.total_cards = _count_cards(db)

    candidates = _candidates(db)
    result.analysable = len(candidates)
    if len(candidates) > limit:
        result.truncated = True
        candidates = candidates[:limit]

    routing_size = hopeful_batch_size(db)
    result.routed_at_batch_size = routing_size

    # --- Pass 1: route ------------------------------------------------------
    routed: dict[tuple[str, str | None], list[tuple[Card, object]]] = {}
    for card in candidates:
        evaluated = evaluation.evaluate_card(db, card, batch_size=routing_size)
        recommendation = evaluated.recommendation
        if recommendation.decision not in _WORTH_GRADING:
            continue
        result.worth_grading += 1
        company_code = recommendation.company_code
        if company_code is None:
            result.unplaced.append(
                UnplacedCard(
                    card_id=card.id,
                    name=evaluated.raw.display_name,
                    set_label=evaluated.raw.set_label,
                    expected_profit=recommendation.expected_profit,
                    reason="The engine recommends grading it but named no grader to send it to.",
                )
            )
            continue
        key = (company_code, recommendation.tier_name)
        routed.setdefault(key, []).append((card, evaluated))

    if not routed:
        result.status = BlockStatus.INSUFFICIENT_DATA.value
        result.reason = (
            "Nothing in your collection clears the bar for grading right now, so there is "
            "no submission to build."
            if result.analysable
            else "No card has both a condition assessment and comparable sales yet, so nothing "
            "can be decided — assess a card and add its sales."
        )
        _finish(result, limit)
        return result

    # --- Pass 2 and 3: pack, then re-verify at the real size ----------------
    companies = {company.code: company for company in db.scalars(select(GradingCompany))}
    for (company_code, tier_name), members in sorted(
        routed.items(), key=lambda item: -len(item[1])
    ):
        company = companies.get(company_code)
        if company is None:  # pragma: no cover - routed codes come from the same table
            continue
        tier = _tier_named(company, tier_name)
        batch = ProposedBatch(
            company_id=company.id,
            company_code=company_code,
            tier_id=tier.id if tier else None,
            tier_name=tier_name,
            card_count=len(members),
            minimum_cards=tier.minimum_cards if tier else 1,
            maximum_cards=tier.maximum_cards if tier else None,
        )

        real_size = len(members)
        short = tier is not None and real_size < tier.minimum_cards
        if short:
            batch.short_by = tier.minimum_cards - real_size
            batch.viable = False

        profit_minor = 0
        cost_minor = 0
        for card, routed_eval in members:
            placed = _reverify(db, card, routed_eval, batch, real_size)
            batch.cards.append(placed)
            if placed.still_pays:
                profit_minor += to_minor(placed.expected_profit) or 0
                cost_minor += to_minor(placed.grading_cost) or 0
            else:
                result.stopped_paying.append(placed)

        # Where the cards actually land at this count. They agree with the
        # routed tier once the batch is full, and differ while it is short.
        landed = {card.tier_name for card in batch.cards if card.still_pays}
        batch.effective_tier_name = landed.pop() if len(landed) == 1 else tier_name

        if short:
            filled = sum(
                to_minor(routed_eval.recommendation.expected_profit) or 0
                for _, routed_eval in members
            )
            batch.expected_profit_if_filled = round(filled / 100, 2)
            uplift = format_money(filled - profit_minor)
            same_tier = batch.effective_tier_name == tier_name
            batch.reason = (
                f"{company_code} {tier_name} needs {tier.minimum_cards} cards and this batch has "
                f"{real_size}. "
                + (
                    ""
                    if same_tier
                    else f"As it stands these would be graded at {batch.effective_tier_name}. "
                )
                + f"Adding {batch.short_by} more card(s) at this tier is worth {uplift} across "
                "the cards already in it."
            )

        batch.expected_profit = round(profit_minor / 100, 2)
        batch.grading_cost = round(cost_minor / 100, 2)
        result.batches.append(batch)

    result.batches.sort(key=lambda item: (item.expected_profit or 0), reverse=True)
    result.placed = sum(
        1 for batch in result.batches for card in batch.cards if card.still_pays
    )
    paying = [
        card for batch in result.batches for card in batch.cards if card.still_pays
    ]
    if paying:
        result.expected_profit = round(
            sum(to_minor(card.expected_profit) or 0 for card in paying) / 100, 2
        )
        result.total_grading_cost = round(
            sum(to_minor(card.grading_cost) or 0 for card in paying) / 100, 2
        )

    _finish(result, limit)
    return result


def _count_cards(db: Session) -> int:
    from sqlalchemy import func

    return db.scalar(select(func.count()).select_from(Card)) or 0


def _tier_named(company: GradingCompany, tier_name: str | None) -> GradingTier | None:
    if tier_name is None:
        return None
    for tier in company.tiers:
        if tier.tier_name == tier_name and tier.active:
            return tier
    return None


def _reverify(
    db: Session,
    card: Card,
    routed_eval,
    batch: ProposedBatch,
    real_size: int,
) -> PlacedCard:
    """Cost this card again at the size its batch actually came out at.

    A card routed at twenty-five and placed in a batch of six is a different
    proposition: the postage it was going to share is now split six ways. If
    that flips the decision, the card says so rather than riding along inside a
    total that no longer holds.
    """
    routed_recommendation = routed_eval.recommendation
    placed = PlacedCard(
        card_id=card.id,
        name=routed_eval.raw.display_name,
        set_label=routed_eval.raw.set_label,
        company_code=batch.company_code,
        tier_id=batch.tier_id,
        tier_name=batch.tier_name,
        declared_value=routed_eval.grading_options.declared_value,
        decision_when_routed=routed_recommendation.decision,
        decision_in_batch=routed_recommendation.decision,
        expected_profit=routed_recommendation.expected_profit,
        grading_cost=routed_recommendation.grading_cost,
        opportunity_score=routed_recommendation.opportunity_score,
    )

    if real_size == routed_recommendation.assumed_batch_size:
        _suggest_cheaper_tier(placed, routed_eval)
        return placed

    actual = evaluation.evaluate_card(db, card, batch_size=real_size)
    recommendation = actual.recommendation
    placed.decision_in_batch = recommendation.decision
    placed.opportunity_score = recommendation.opportunity_score

    # The tier the card actually lands on at this count, which is not always the
    # one it was routed to: Bulk needs twenty-five, so in a batch of nine these
    # cards are Economy cards. Reporting them as Bulk — at Economy's price —
    # would quote a route that does not exist at this size.
    placed.tier_name = recommendation.tier_name or batch.tier_name
    placed.tier_id = None if placed.tier_name != batch.tier_name else batch.tier_id

    # The figures for that route at that size. The recommendation itself can be
    # describing a fuller submission (`grade_if_batch_filled`), so prefer the
    # outcome row, which is always costed at the size asked for.
    at_this_size = next(
        (
            outcome
            for outcome in actual.expected_outcomes.outcomes
            if outcome.company_code == batch.company_code
            and outcome.tier_name == placed.tier_name
        ),
        None,
    )
    if at_this_size is not None:
        placed.expected_profit = at_this_size.expected_profit
        placed.grading_cost = at_this_size.grading_cost
    else:
        placed.expected_profit = recommendation.expected_profit
        placed.grading_cost = recommendation.grading_cost

    # `grade_if_batch_filled` was a fair answer while the batch was hypothetical.
    # Here the size is a known fact, so it means "not in the batch you have" —
    # accepting it would let the optimiser bless a plan on the strength of a
    # fuller submission that does not exist.
    if recommendation.decision != Decision.GRADE.value:
        placed.still_pays = False
        was = routed_recommendation.expected_profit
        now = placed.expected_profit
        moved = (
            f" Expected profit falls from {format_money(to_minor(was))} to "
            f"{format_money(to_minor(now))}."
            if was is not None and now is not None and to_minor(was) != to_minor(now)
            else ""
        )
        placed.reason = (
            f"Worth grading in a batch of {routed_recommendation.assumed_batch_size}, but this "
            f"batch has {real_size} — so it carries a bigger share of the postage.{moved}"
        )
    else:
        _suggest_cheaper_tier(placed, actual)
    return placed


def _suggest_cheaper_tier(placed: PlacedCard, evaluated) -> None:
    """"Move it to Bulk and save £2.20" — but only where the move is legal.

    Reads the options block the evaluation already computed, so this is a lookup
    over numbers the user can see rather than a second costing they cannot.
    """
    current = next(
        (
            option
            for option in evaluated.grading_options.options
            if option.company_code == placed.company_code
            and option.tier_name == placed.tier_name
        ),
        None,
    )
    if current is None or current.total_cost is None:
        return

    cheaper = [
        option
        for option in evaluated.grading_options.options
        if option.company_code == placed.company_code
        and option.available
        and option.total_cost is not None
        and option.total_cost < current.total_cost
    ]
    if not cheaper:
        return

    best = min(cheaper, key=lambda option: option.total_cost)
    placed.cheaper_tier_name = best.tier_name
    placed.cheaper_tier_saving = round(current.total_cost - best.total_cost, 2)


def _finish(result: OptimiserResult, limit: int) -> None:
    """Set the status and say anything the numbers above do not."""
    if result.truncated:
        result.notes.append(
            f"Only the {limit} most recently updated ready cards were considered. "
            "Raise the limit to include the rest."
        )
    if result.stopped_paying:
        result.notes.append(
            f"{len(result.stopped_paying)} card(s) were worth grading in a full batch but not "
            "in the batch they ended up in. They are listed against their batch, and are not "
            "counted in any total."
        )
    short = [batch for batch in result.batches if not batch.viable]
    if short:
        result.notes.append(
            f"{len(short)} proposed batch(es) are short of their tier's minimum. They are "
            "costed as they stand, so you can see what filling them would be worth."
        )

    if result.status == BlockStatus.INSUFFICIENT_DATA.value:
        return
    if result.truncated or short or result.stopped_paying or result.unplaced:
        result.status = BlockStatus.PARTIAL.value
        result.reason = result.notes[0] if result.notes else None
    else:
        result.status = BlockStatus.OK.value
