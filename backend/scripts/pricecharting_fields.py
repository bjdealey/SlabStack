"""Show everything PriceCharting returns, and what the mapping makes of it.

Two sections, in this order and for a reason.

**Every key in the response, unfiltered.** This is the one that answers "what am
I actually paying for?". A grade that came back as ``0``, or under a key shaped
differently from the rest, would be invisible in any tidied view and would look
like a grade the subscription does not include — so nothing is tidied first.

**The mapping's reading of it.** Each price beside the grade ``grade_fields``
claims it is, so the two can be compared against the card's own page on the
site. That comparison is what turned the shipped mapping from folklore into
something known, and it caught two errors in the widely repeated version.

    make pricecharting-fields CARD="Umbreon VMAX Evolving Skies"
    make pricecharting-fields ID=2513024

Nothing is confirmed by running this, and nothing is written by it. When the
columns agree, say so explicitly:

    Settings → Data sources → PriceCharting → config → grade_fields_confirmed

While that is false only the raw price is written, because "loose" is the one
label on this API that cannot mean anything else.
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


def _dump_everything(fields: dict) -> None:
    """Every key the API returned, unfiltered, before anything is interpreted.

    The comparison table below this is filtered — non-zero keys ending in
    ``-price`` — and a filter is exactly what you must not trust when the
    question is "what does this API actually give me?". A grade that came back
    as ``0``, or under a key shaped differently from the rest, would be invisible
    in the tidy view and look like a grade the subscription does not include.

    So: the raw thing first, then the reading of it.
    """
    print(f"  \033[1mEvery key in the response ({len(fields)})\033[0m")
    print("  The unfiltered truth about what this subscription returns.\n")
    if not fields:
        print("    (nothing at all)\n")
        return

    width = max(len(str(key)) for key in fields)
    for key in sorted(fields):
        value = fields[key]
        shown = f"${value / 100:,.2f}" if key.endswith("-price") and isinstance(value, int) else value
        note = ""
        if key.endswith("-price") and value == 0:
            # The interesting case: present, and empty. That is "no sales at
            # this grade", not "this grade is not in your plan".
            note = "   ← returned, but empty"
        print(f"    {str(key).ljust(width)}   {shown}{note}")
    print()


def main(term: str, product_id: str = "") -> int:
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

        if product_id:
            chosen_id, chosen_name = product_id, f"product {product_id}"
        else:
            try:
                matches = provider.search_card(CardQuery(name=term, limit=5))
            except ProviderRequestError as exc:
                print(f"  {NO} {exc}")
                return 1

            if not matches:
                print(f"  {NO} Nothing matched “{term}”. Include the set name — PriceCharting keys")
                print('      a card by its set, so "Umbreon VMAX Evolving Skies" beats "Umbreon".')
                return 1

            print(f"\n  Matches for “{term}”:\n")
            for index, match in enumerate(matches, start=1):
                print(
                    f"    {index}. {match.name} — {match.set_name or 'unknown set'} "
                    f"(id {match.external_id})"
                )
            # Any of them can be inspected directly:
            #     make pricecharting-fields ID=2512907
            chosen_id, chosen_name = matches[0].external_id, matches[0].name
            print(f"\n  Showing the first: {chosen_name}. For another, pass ID=<id>.\n")

        try:
            fields = provider.fields_for_product(chosen_id)
        except ProviderRequestError as exc:
            print(f"  {NO} {exc}")
            return 1

        _dump_everything(fields)

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
    identifier = os.environ.get("ID") or ""
    card = " ".join(sys.argv[1:]) or os.environ.get("CARD") or ""
    if not card and not identifier:
        print('Usage: make pricecharting-fields CARD="Umbreon VMAX Evolving Skies"')
        print("   or: make pricecharting-fields ID=2513024")
        sys.exit(2)
    sys.exit(main(card, identifier))
