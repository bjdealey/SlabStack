"""Linking a whole collection to a source, without guessing at any of it.

A card is priced by a source only once that source has been told which card it
is, and that link is stored per card per source. Done one dialog at a time it is
the most tedious thing in this application: a hundred cards and two sources is
two hundred searches, each ending in a click on the obvious answer.

The tedium is not the dangerous part, though. A wrong link is silent and
permanent-feeling — every future refresh prices a different printing, the
numbers stay plausible, and nothing ever says so. That is what shapes this
module: it is not "link everything", it is **"link the ones where there is
nothing to decide, and hand the rest back"**.

Three things make a match unambiguous, and all three are required:

1. The card carries enough to search with. A name alone matches a dozen
   Pikachus, so a card with no number is never linked automatically however
   confident a provider sounds about it.
2. The best candidate clears a confidence floor — matched on name *and* number,
   not on name alone.
3. The best candidate is clearly ahead of the runner-up. Two candidates at 0.85
   are a choice, and a choice belongs to the user.

Anything that fails is returned with the candidates attached, so the answer to
"why was this one skipped" is on screen rather than in a log.

**Nothing is applied but the link.** A catalogue's set name and rarity are not
accepted in bulk, even when the match is certain: linking enables price syncing,
while rewriting a card's identity is a separate decision with different
consequences — `catalog_key` is derived from those fields, and changing it
orphans the sales and prices already attached. The per-card dialog offers that;
this deliberately does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Card, DataSource
from app.services.market_data.base import CardMatch, CardQuery
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider

__all__ = ["LinkOutcome", "LinkReport", "link_collection"]

#: How sure the best candidate must be. The scorers award roughly half for an
#: exact name and the rest for the number and set, so this is "the name matched
#: exactly and at least one thing pinned it down" — never a name on its own.
DEFAULT_MIN_CONFIDENCE = 0.7

#: How far ahead of the runner-up. Two candidates within this of each other is a
#: choice between them, and a choice is the user's.
DEFAULT_MIN_MARGIN = 0.2


@dataclass
class LinkOutcome:
    card_id: str
    name: str
    status: str
    reason: str | None = None
    external_id: str | None = None
    matched_name: str | None = None
    confidence: float | None = None
    #: What it was choosing between, when it declined to choose.
    candidates: list[dict] = field(default_factory=list)


@dataclass
class LinkReport:
    source_code: str
    source_name: str
    linked: int = 0
    skipped: int = 0
    ambiguous: int = 0
    failed: int = 0
    dry_run: bool = True
    status: str = "ok"
    reason: str | None = None
    cards: list[LinkOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def link_collection(
    db: Session,
    source: DataSource,
    *,
    dry_run: bool = True,
    limit: int = 100,
    relink: bool = False,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> LinkReport:
    """Search this source for every unlinked card, and link the certain ones.

    ``dry_run`` is the default on purpose. The whole point of a bulk action is
    that nobody watches it card by card, so the first thing it should be able to
    do is show its work and change nothing.
    """
    report = LinkReport(source_code=source.code, source_name=source.name, dry_run=dry_run)

    try:
        provider = load_provider(source)
    except ProviderUnavailableError as exc:
        report.status = "error"
        report.reason = str(exc)
        return report

    if not provider.capabilities().search:
        report.status = "error"
        report.reason = f"{source.name} does not offer card search, so nothing can be linked to it."
        return report

    cards = list(db.scalars(select(Card).order_by(Card.updated_at.desc())))
    eligible = [
        card for card in cards if relink or not (card.external_ids or {}).get(source.code)
    ]
    if len(eligible) > limit:
        # Never a silent cap: a run that quietly covered the first hundred of
        # nine hundred reads exactly like one that covered everything.
        report.notes.append(
            f"{len(eligible)} card(s) are unlinked and this run took the first {limit}, most "
            "recently updated first. Run it again for the rest — sources are rate limited on "
            "purpose, so a whole collection takes several passes."
        )
        eligible = eligible[:limit]

    if not eligible:
        report.status = "insufficient_data"
        report.reason = (
            f"Every card is already linked to {source.name}."
            if cards
            else "There are no cards to link."
        )
        return report

    for card in eligible:
        outcome = _link_one(
            db,
            provider,
            card,
            source=source,
            dry_run=dry_run,
            min_confidence=min_confidence,
            min_margin=min_margin,
        )
        report.cards.append(outcome)
        if outcome.status == "linked":
            report.linked += 1
        elif outcome.status == "ambiguous":
            report.ambiguous += 1
        elif outcome.status == "failed":
            report.failed += 1
        else:
            report.skipped += 1

    _summarise(report)
    return report


def _link_one(
    db: Session,
    provider,
    card: Card,
    *,
    source: DataSource,
    dry_run: bool,
    min_confidence: float,
    min_margin: float,
) -> LinkOutcome:
    outcome = LinkOutcome(card_id=card.id, name=_display(card), status="skipped")

    if not card.card_number and not card.set_code and not card.set_name:
        # A name on its own is not an identity. Twelve cards are called Pikachu
        # and a provider will happily rank one of them first.
        outcome.reason = (
            "Only a name to go on. Add the card number or the set, or link this one by hand."
        )
        return outcome

    try:
        matches = provider.search_card(
            CardQuery(
                name=card.name,
                set_code=card.set_code,
                set_name=card.set_name,
                card_number=card.card_number,
                language=card.language,
                limit=5,
            )
        )
    except ProviderRequestError as exc:
        outcome.status = "failed"
        outcome.reason = str(exc)
        return outcome
    except Exception as exc:  # One bad adapter must not abort the whole run.
        outcome.status = "failed"
        outcome.reason = f"{source.name} adapter raised {type(exc).__name__}: {exc}"
        return outcome

    if not matches:
        outcome.reason = f"Nothing in {source.name} matched this card."
        return outcome

    ranked = sorted(matches, key=lambda match: match.confidence, reverse=True)
    best = ranked[0]
    outcome.external_id = best.external_id
    outcome.matched_name = best.name
    outcome.confidence = best.confidence
    outcome.candidates = [_candidate(match) for match in ranked[:3]]

    if best.confidence < min_confidence:
        outcome.status = "ambiguous"
        outcome.reason = (
            f"Best match is only {best.confidence:.0%} sure. Something has to pin the card "
            "down beyond its name before this will link it for you."
        )
        return outcome

    runner_up = ranked[1].confidence if len(ranked) > 1 else 0.0
    if best.confidence - runner_up < min_margin:
        outcome.status = "ambiguous"
        outcome.reason = (
            f"Two candidates are close ({best.confidence:.0%} and {runner_up:.0%}). That is a "
            "choice, and a wrong one prices a different printing from here on."
        )
        return outcome

    outcome.status = "linked"
    outcome.reason = None
    if not dry_run:
        # The link and nothing else. Set name, rarity and number feed
        # `catalog_key`, and rewriting those would re-key the card away from
        # sales and prices already attached to it.
        card.external_ids = {**(card.external_ids or {}), source.code: best.external_id}
        db.flush()
    return outcome


def _candidate(match: CardMatch) -> dict:
    return {
        "external_id": match.external_id,
        "name": match.name,
        "set_name": match.set_name,
        "card_number": match.card_number,
        "confidence": match.confidence,
    }


def _summarise(report: LinkReport) -> None:
    if report.failed and not report.linked:
        report.status = "error"
        report.reason = f"{report.failed} card(s) failed and nothing was linked."
    elif report.failed:
        report.status = "partial"
        report.reason = f"{report.failed} card(s) failed; the rest were left as they were."
    elif not report.linked:
        report.status = "insufficient_data"
        report.reason = (
            f"Nothing was certain enough to link. {report.ambiguous} card(s) had candidates "
            "worth looking at yourself."
            if report.ambiguous
            else "No card matched anything at this source."
        )

    if report.ambiguous:
        report.notes.append(
            f"{report.ambiguous} card(s) had a plausible match that was not clearly the right "
            "one. They are listed with what they were choosing between — linking those by hand "
            "takes a moment and a wrong link is silent for as long as you keep the card."
        )


def _display(card: Card) -> str:
    return f"{card.name} {card.card_number}".strip() if card.card_number else card.name
