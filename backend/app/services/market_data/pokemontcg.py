"""api.pokemontcg.io — the card catalogue, and an aggregate price with it.

Chosen as the first live source because it is the only one a user can try with
no signup, no approval and no payment: it answers anonymously at a lower rate
limit, and an optional key raises it. Everything else worth having needs an
account and, in eBay's and TCGplayer's case, an application review.

**What it gives.** Full card identity — set, number, rarity, printing, artwork —
which is what makes card lookup possible at all, and current aggregate prices
from TCGplayer (USD) and Cardmarket (EUR).

**What it does not give, and this matters more than what it does.** No
individual sales, and no graded prices. Read what each engine here actually
needs and the consequences are specific rather than vague:

* Valuation gets a number, but an aggregate index rather than a median of sales
  you could have made. It is used only when you have no sales of your own.
* Liquidity stays **unknown**. It is measured from how often a card actually
  trades, and an aggregate price cannot say — one price could be twenty sales a
  week or none in a year.
* Trend accrues **forward only**, from the daily ``price_snapshots`` this
  writes. There is no history to import.
* The grading decision stays **unanswerable** from this source alone. It is
  measured against what the slab fetches, and there are no graded prices here at
  all. You still need graded comparables — entered, imported, or from a source
  that has them.

Saying that plainly is the point. A source that fills the raw price and leaves
the grading decision exactly where it was would otherwise look like a fix.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.services.market_data.base import (
    CardMatch,
    CardQuery,
    MarketDataProvider,
    MarketKey,
    PricePoint,
    ProviderCapabilities,
)
from app.services.market_data.http import HttpxTransport, Transport

__all__ = ["PokemonTcgIoProvider"]

DEFAULT_BASE_URL = "https://api.pokemontcg.io/v2"

#: Anonymous callers get a lower ceiling than keyed ones. Both are well under
#: the published limits: this is a background sync, not a race.
ANONYMOUS_RATE_LIMIT = 20
KEYED_RATE_LIMIT = 60

#: TCGplayer quotes USD, Cardmarket EUR. Nothing here is GBP, so a price from
#: this source always needs converting before the app can report it — and the
#: conversion is the user's rate, never one invented here.
MARKETPLACE_CURRENCY = {"tcgplayer": "USD", "cardmarket": "EUR"}

#: Preference order within a marketplace's price block. `market` is TCGplayer's
#: own average of recent sales, which is the closest thing it has to "what it
#: actually goes for"; `trendPrice` is Cardmarket's equivalent.
_TCGPLAYER_FIELDS = ("market", "mid", "directLow", "low")
_CARDMARKET_FIELDS = ("trendPrice", "averageSellPrice", "avg7", "avg30", "lowPrice")

#: TCGplayer splits prices by printing. Matched against the card's variant so a
#: reverse holo is not priced as a normal — the same distinction `catalog_key`
#: makes, honoured here rather than flattened.
_PRINTING_BY_VARIANT = {
    "reverse-holo": ("reverseHolofoil",),
    "reverse holo": ("reverseHolofoil",),
    "holo": ("holofoil", "normal"),
    "1st-edition": ("1stEditionHolofoil", "1stEditionNormal"),
    "first-edition": ("1stEditionHolofoil", "1stEditionNormal"),
}
_DEFAULT_PRINTINGS = ("holofoil", "normal", "reverseHolofoil")


class PokemonTcgIoProvider(MarketDataProvider):
    code = "pokemontcg_io"
    name = "Pokémon TCG API"

    def __init__(
        self,
        config: dict | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        super().__init__(config=config, api_key=api_key)
        self.base_url = (self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.marketplace = self.config.get("marketplace") or "tcgplayer"
        if self.marketplace not in MARKETPLACE_CURRENCY:
            raise ValueError(
                f"'{self.marketplace}' is not a marketplace this provider knows. "
                f"Choose one of: {', '.join(sorted(MARKETPLACE_CURRENCY))}."
            )
        self.transport = transport or HttpxTransport(
            rate_limit_per_minute=self.config.get("rate_limit_per_minute")
            or (KEYED_RATE_LIMIT if api_key else ANONYMOUS_RATE_LIMIT)
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            search=True,
            current_price=True,
            # Deliberately false, and the reason each is false is in the module
            # docstring. Claiming a capability this source does not have would
            # make the sync engine ask for data it can never return.
            sales_history=False,
            active_listings=False,
            graded_prices=False,
            historical_series=False,
        )

    @property
    def price_currency(self) -> str:
        return MARKETPLACE_CURRENCY[self.marketplace]

    # --- Search --------------------------------------------------------------

    def search_card(self, query: CardQuery) -> list[CardMatch]:
        """Find candidate cards. Never applied without the user confirming (§5)."""
        lucene = _build_query(query)
        if not lucene:
            return []

        body = self._get(
            "/cards",
            {"q": lucene, "pageSize": min(max(query.limit, 1), 50), "orderBy": "-set.releaseDate"},
        )
        cards = body.get("data") or []
        return [match for match in (_to_match(card, query) for card in cards) if match]

    # --- Prices --------------------------------------------------------------

    def get_current_price(self, key: MarketKey) -> PricePoint | None:
        """The aggregate price for one card, in the marketplace's own currency.

        ``None`` when the card is not in the catalogue, or is but carries no
        price block — which happens for cards nobody is currently listing. That
        is a real answer and better than a zero.
        """
        if key.grade_label != "raw":
            # No graded prices exist at this source. Returning the raw price for
            # a graded key would be the single most damaging thing this adapter
            # could do: the whole grading decision is raw-versus-slab, and
            # answering it with the same number on both sides makes grading look
            # exactly break-even, every time.
            return None

        if not key.external_id:
            # Nothing to look up. The sync engine matches a card to this
            # catalogue once, with the user confirming, and stores the id.
            return None
        card = self._card_by_id(key.external_id)
        if card is None:
            return None
        return self._price_from_card(card, variant=key.variant)

    def price_for_external_id(
        self, external_id: str, *, variant: str | None = None
    ) -> PricePoint | None:
        """Price a card we have already identified in this catalogue."""
        card = self._card_by_id(external_id)
        return self._price_from_card(card, variant=variant) if card else None

    def _card_by_id(self, external_id: str) -> dict | None:
        body = self._get(f"/cards/{external_id}", {})
        card = body.get("data")
        return card if isinstance(card, dict) else None

    def _price_from_card(self, card: dict, *, variant: str | None) -> PricePoint | None:
        block = card.get(self.marketplace)
        if not isinstance(block, dict):
            return None
        prices = block.get("prices")
        if not isinstance(prices, dict):
            return None

        value, chosen = (
            _pick_tcgplayer(prices, variant)
            if self.marketplace == "tcgplayer"
            else _pick_cardmarket(prices)
        )
        if value is None:
            return None

        return PricePoint(
            value_minor=round(value * 100),
            currency=self.price_currency,
            as_of=_parse_updated(block.get("updatedAt")),
            # Zero, and it must stay zero: this is an index, not a count of
            # sales, and every confidence reading downstream keys off it.
            sample_size=0,
            raw={
                "marketplace": self.marketplace,
                "field": chosen,
                "url": block.get("url"),
                "card_id": card.get("id"),
                "card_name": card.get("name"),
            },
        )

    # --- Plumbing ------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        headers = {"X-Api-Key": self.api_key} if self.api_key else {}
        return self.transport.get_json(f"{self.base_url}{path}", params=params, headers=headers)


# --- Query building ----------------------------------------------------------


def _build_query(query: CardQuery) -> str:
    """A Lucene-ish query string, which is what this API takes.

    Deliberately narrow before broad: a name plus a set plus a number finds one
    card, and a bare name finds two hundred. The caller's own fields are used
    when present rather than dumping everything into a full-text match.
    """
    clauses: list[str] = []
    if query.name:
        clauses.append(f'name:"{_escape(query.name)}"')
    elif query.text:
        clauses.append(f'name:"{_escape(query.text)}"')

    if query.card_number:
        # "215/203" is how a card is written; the API stores just the numerator.
        number = query.card_number.split("/")[0].strip()
        if number:
            clauses.append(f'number:"{_escape(number)}"')

    if query.set_code:
        # ptcgoCode is the printed code (EVS); set.id is the API's own (swsh7).
        code = _escape(query.set_code)
        clauses.append(f'(set.ptcgoCode:"{code}" OR set.id:"{code.lower()}")')
    elif query.set_name:
        clauses.append(f'set.name:"{_escape(query.set_name)}"')

    return " ".join(clauses)


def _escape(value: str) -> str:
    return value.replace('"', "").replace("\\", "").strip()


def _to_match(card: dict, query: CardQuery) -> CardMatch | None:
    if not isinstance(card, dict) or not card.get("id") or not card.get("name"):
        return None
    card_set = card.get("set") or {}
    number = card.get("number")
    printed_total = card_set.get("printedTotal")

    return CardMatch(
        external_id=str(card["id"]),
        name=str(card["name"]),
        set_name=card_set.get("name"),
        set_code=card_set.get("ptcgoCode") or card_set.get("id"),
        card_number=f"{number}/{printed_total}" if number and printed_total else number,
        rarity=card.get("rarity"),
        language="English",  # This catalogue is English-only; saying so is not a guess.
        image_url=(card.get("images") or {}).get("small"),
        confidence=_match_confidence(card, query),
        raw={
            "id": card.get("id"),
            "set_id": card_set.get("id"),
            "release_date": card_set.get("releaseDate"),
            "supertype": card.get("supertype"),
            "images": card.get("images"),
            "has_tcgplayer": bool(card.get("tcgplayer")),
            "has_cardmarket": bool(card.get("cardmarket")),
        },
    )


def _match_confidence(card: dict, query: CardQuery) -> float:
    """How sure we are, so the UI can order candidates and never auto-apply.

    Rewards the fields that actually pin a card down. A name match alone is
    weak — there are a dozen Pikachus — and it is scored that way.
    """
    score = 0.0
    name = str(card.get("name") or "").lower()
    wanted = (query.name or query.text or "").lower().strip()
    if wanted and name == wanted:
        score += 0.5
    elif wanted and wanted in name:
        score += 0.3

    if query.card_number:
        number = query.card_number.split("/")[0].strip()
        if number and str(card.get("number")) == number:
            score += 0.3

    card_set = card.get("set") or {}
    if query.set_code:
        code = query.set_code.lower()
        if code in {
            str(card_set.get("ptcgoCode") or "").lower(),
            str(card_set.get("id") or "").lower(),
        }:
            score += 0.2
    return round(min(score, 1.0), 2)


# --- Price extraction --------------------------------------------------------


def _pick_tcgplayer(prices: dict, variant: str | None) -> tuple[float | None, str | None]:
    """Pick the printing that matches the card, then the best field in it."""
    wanted = _PRINTING_BY_VARIANT.get((variant or "").strip().lower(), _DEFAULT_PRINTINGS)
    ordered = [*wanted, *(p for p in _DEFAULT_PRINTINGS if p not in wanted)]

    for printing in ordered:
        block = prices.get(printing)
        if not isinstance(block, dict):
            continue
        for field_name in _TCGPLAYER_FIELDS:
            value = block.get(field_name)
            if isinstance(value, int | float) and value > 0:
                return float(value), f"{printing}.{field_name}"
    return None, None


def _pick_cardmarket(prices: dict) -> tuple[float | None, str | None]:
    for field_name in _CARDMARKET_FIELDS:
        value = prices.get(field_name)
        if isinstance(value, int | float) and value > 0:
            return float(value), field_name
    return None, None


def _parse_updated(value: Any) -> date | None:
    """This API dates things ``2026/08/10``. Anything else is left as unknown."""
    if not isinstance(value, str):
        return None
    for pattern in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip()[:10], pattern).date()
        except ValueError:
            continue
    return None
