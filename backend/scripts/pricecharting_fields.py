"""Show what PriceCharting returned, beside what the mapping claims it means.

PriceCharting is a video-game price guide that also covers cards, and it reuses
the game condition fields for card grades — ``box-only-price`` and
``manual-only-price`` hold grades, not boxes and manuals. Which field is which
grade is the single most important fact about this source, and it is not
something this application can check for itself.

Getting it wrong is silent. A PSA 9 price sitting in the PSA 10 slot is a real
number, of the right magnitude, in the right currency, and it would quietly
invert the recommendation on every card it touched. That is worse than an error.

So: this prints the numbers the API actually returned, in one column, and what
the current mapping says each one is, in another. Open the same card on
pricecharting.com, look at its price table, and see whether the two agree. Half
a minute, once, and the answer is knowledge rather than folklore.

    make pricecharting-fields CARD="Umbreon VMAX"

Nothing is confirmed by running this. When the columns agree, say so explicitly:

    Settings → Data sources → PriceCharting → config → grade_fields_confirmed: true

and the graded prices start being written. Until then only the raw price is,
because "loose" is the one label on this API that cannot mean anything else.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import select

from app.db import session_scope
from app.models import DataSource
from app.services.market_data.base import CardQuery
from app.services.market_data.http import ProviderRequestError
from app.services.market_data.registry import ProviderUnavailableError, load_provider

OK = "\033[32m✓\033[0m"
NO = "\033[31m✗\033[0m"
HM = "\033[33m!\033[0m"


def main(term: str) -> int:
    with session_scope() as db:
        source = db.scalar(select(DataSource).where(DataSource.code == "pricecharting"))
        if source is None:
            print("  No pricecharting data source row. Run the app once to seed it.")
            return 1
        if not source.enabled:
            print(f"  {HM} PriceCharting is disabled. Enabling it is what this checks, so it is")
            print("      switched on temporarily for this lookup only — nothing is written.\n")
            source.enabled = True

        try:
            provider = load_provider(source)
        except ProviderUnavailableError as exc:
            print(f"  {NO} {exc}")
            return 1

        try:
            matches = provider.search_card(CardQuery(name=term, limit=5))
        except ProviderRequestError as exc:
            print(f"  {NO} {exc}")
            return 1

        if not matches:
            print(f"  {NO} Nothing matched “{term}”. Include the set name — PriceCharting keys a")
            print('      card by its set, so "Umbreon VMAX Evolving Skies" beats "Umbreon".')
            return 1

        print(f"\n  Matches for “{term}”:\n")
        for index, match in enumerate(matches, start=1):
            print(f"    {index}. {match.name} — {match.set_name or 'unknown set'} (id {match.external_id})")

        chosen = matches[0]
        print(f"\n  Showing prices for the first: {chosen.name}\n")

        try:
            fields = provider.fields_for_product(chosen.external_id)
        except ProviderRequestError as exc:
            print(f"  {NO} {exc}")
            return 1

        mapping = provider.grade_fields
        prices = {
            key: value
            for key, value in fields.items()
            if key.endswith("-price") and isinstance(value, int | float) and value
        }
        if not prices:
            print(f"  {HM} This product carries no prices at all. Try another card.")
            return 1

        width = max(len(key) for key in prices)
        print(f"    {'FIELD'.ljust(width)}   PRICE     MAPPING SAYS")
        print(f"    {'-' * width}   --------  ------------")
        for key in sorted(prices):
            dollars = f"${prices[key] / 100:,.2f}"
            claim = mapping.get(key, "— not mapped, ignored —")
            print(f"    {key.ljust(width)}   {dollars:>8}  {claim}")

        unmapped = sorted(set(mapping) - set(prices))
        if unmapped:
            print(f"\n  {HM} Mapped but absent from this product: {', '.join(unmapped)}.")
            print("      Normal — a card with few sales at a grade has no price for it.")

        print("\n  Now open this card on pricecharting.com and compare.")
        print("  Does each price above appear under the grade the mapping claims?\n")
        if provider.mapping_confirmed:
            print(f"  {OK} grade_fields_confirmed is already true — graded prices are being written.")
        else:
            print(f"  {HM} grade_fields_confirmed is false, so only the raw price is written.")
            print("      If the columns agree, set it true in Settings → Data sources.")
            print("      If they disagree, correct grade_fields there — it is data, not code.\n")

        # Nothing is persisted. A temporary enable to run a lookup must not
        # become a permanent one because a diagnostic was run.
        db.rollback()
    return 0


if __name__ == "__main__":
    card = " ".join(sys.argv[1:]) or os.environ.get("CARD") or ""
    if not card:
        print('Usage: make pricecharting-fields CARD="Umbreon VMAX Evolving Skies"')
        sys.exit(2)
    sys.exit(main(card))
