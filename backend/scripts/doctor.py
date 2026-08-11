"""Walk the live-data chain and say exactly where it stops.

"Nothing happened" has about five causes and they look identical from the UI:
the source is off, no card is linked, no exchange rate is set, the provider is
unreachable, or it answered and had no price. Each one is fixable in a minute
once you know which it is, and unfindable until you do.

So this checks them in order and stops at the first thing that would block a
sync, with the command or click that fixes it. It makes **at most one** network
request, and only once everything local looks right — there is no point asking
an API whether it is up when nothing would be sent to it either way.

    make doctor
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

from app.db import session_scope
from app.models import Card, DataSource, MarketPrice
from app.services import settings_service
from app.services.market_data.base import CardQuery
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider

OK = "\033[32m✓\033[0m"
NO = "\033[31m✗\033[0m"
HM = "\033[33m!\033[0m"


def line(mark: str, text: str, fix: str | None = None) -> None:
    print(f"  {mark} {text}")
    if fix:
        print(f"      → {fix}")


def main() -> int:
    print("\nSlabStack — live market data check\n")

    with session_scope() as db:
        total_cards = db.scalar(select(func.count()).select_from(Card)) or 0
        print(f"  Collection: {total_cards} card(s)\n")

        # 1. Is a network source switched on at all?
        sources = list(
            db.scalars(
                select(DataSource).where(
                    DataSource.enabled.is_(True), DataSource.provider_class.is_not(None)
                )
            )
        )
        network = [s for s in sources if s.code not in {"manual", "csv"}]
        if not network:
            line(NO, "No network source is enabled.",
                 "Settings → Data sources → enable 'Pokémon TCG API'. No key needed.")
            print("\n  Nothing else can work until that is on. Stopping here.\n")
            return 1
        for source in network:
            line(OK, f"{source.name} is enabled.")

        source = network[0]

        # 2. Does its adapter actually load? Catches a missing required key.
        try:
            provider = load_provider(source)
        except ProviderUnavailableError as exc:
            line(NO, f"{source.name} will not load.", str(exc))
            return 1
        keyed = "with an API key" if provider.api_key else "anonymously (no key set — fine)"
        line(OK, f"Adapter loaded, running {keyed}.")

        # 3. Is anything linked? This is the usual answer.
        linked = [
            card
            for card in db.scalars(select(Card))
            if (card.external_ids or {}).get(source.code)
        ]
        if not linked:
            line(NO, f"No card is linked to {source.name}.",
                 "Open a card → 'Find in catalogue' → pick the match. "
                 "Refresh only prices cards it has been told the provider's id for.")
            print("\n  This is the most common reason a refresh appears to do nothing.\n")
            return 1
        line(OK, f"{len(linked)} of {total_cards} card(s) linked.")
        if len(linked) < total_cards:
            line(HM, f"{total_cards - len(linked)} card(s) are not linked and will be skipped.")

        # 4. Can a fetched price be stated in your currency?
        values = settings_service.get_all(db)
        currency = values.get("currency", "GBP")
        rates = values.get("fx_rates") or {}
        provider_currency = getattr(provider, "price_currency", None)

        if provider_currency and provider_currency != currency:
            pair = f"{provider_currency}_{currency}"
            inverse = f"{currency}_{provider_currency}"
            if rates.get(pair) or rates.get(inverse):
                used = rates.get(pair) or f"1/{rates.get(inverse)}"
                line(OK, f"{provider_currency}→{currency} rate is set ({used}).")
            else:
                line(NO, f"{provider_currency} prices, but no {pair} rate is set.",
                     f'Settings → Market → Exchange rates: {{"{pair}": 0.79}}. '
                     "Prices are fetched but deliberately not written without one.")
                print("\n  The sync will run and write nothing. Stopping here.\n")
                return 1
        else:
            line(OK, f"Provider quotes {currency}; no conversion needed.")

        # 5. One real request, now that everything else is in order.
        print()
        try:
            matches = provider.search_card(CardQuery(name=linked[0].name, limit=1))
        except ProviderRequestError as exc:
            line(NO, "The provider could not be reached.", str(exc))
            print("\n  Everything local is set up correctly — this is the network or the API.\n")
            return 1
        line(OK, f"Reached {source.name}: {len(matches)} result(s) for '{linked[0].name}'.")

        # 6. Has anything actually landed?
        written = db.scalar(
            select(func.count())
            .select_from(MarketPrice)
            .where(MarketPrice.source_id == source.id)
        ) or 0
        if written:
            line(OK, f"{written} price(s) already stored from this source.")
        else:
            line(HM, "No prices stored from this source yet.",
                 "Settings → Data sources → 'Refresh prices'.")

        if source.last_sync_at:
            status = source.last_sync_status or "unknown"
            line(OK if status == "ok" else HM,
                 f"Last sync: {source.last_sync_at:%Y-%m-%d %H:%M} ({status}).")
            if source.last_sync_error:
                line(HM, f"Last error: {source.last_sync_error}")
        else:
            line(HM, "Never synced.", "Settings → Data sources → 'Refresh prices'.")

    print("\n  Everything needed is in place.\n")
    print("  Remember what this source cannot do: no graded prices, so the")
    print("  grade-or-sell decision still needs graded comparables you enter")
    print("  or import. It fills the raw value, not the decision.\n")
    return 0


if __name__ == "__main__":
    if os.environ.get("SLABSTACK_DATA_DIR"):
        print(f"Using SLABSTACK_DATA_DIR={os.environ['SLABSTACK_DATA_DIR']}")
    sys.exit(main())
