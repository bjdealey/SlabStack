"""Walk the live-data chain and say exactly where it stops.

"Nothing happened" has about five causes and they look identical from the UI:
the source is off, a credential is missing, no card is linked, no exchange rate
is set, the provider is unreachable, or it answered and had no data. Each one is
fixable in a minute once you know which it is, and unfindable until you do.

So this checks them in order, per enabled source, and stops that source at the
first thing that would block it — with the command or click that fixes it. It
makes **at most one** network request per source, and only once everything local
looks right: there is no point asking an API whether it is up when nothing would
be sent to it either way.

The checks differ by what a source actually is. A catalogue has to be told its
own id for a card before it can price it; a marketplace is searched by name and
needs no link at all. Reporting "no card is linked" for eBay would send you off
to do something that changes nothing.

    make doctor
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import func, select

from app.config import LOADED_FROM_ENV_FILES, env_file_report
from app.db import session_scope
from app.models import Card, DataSource, MarketListing, MarketPrice, MarketSale
from app.services import settings_service
from app.services.market_data.base import CardQuery, MarketKey
from app.services.market_data.http import CapabilityDeniedError, ProviderRequestError
from app.services.market_data.registry import (
    ProviderUnavailableError,
    credentials_present,
    load_provider,
)

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
        # First, and inside the session because the variables a source reads are
        # declared on the source. Configuration before anything asks whether it
        # is right: the report that used to be least helpful was one about a
        # variable, printed to somebody looking straight at where they set it.
        check_env(db)

        total_cards = db.scalar(select(func.count()).select_from(Card)) or 0
        print(f"  Collection: {total_cards} card(s)\n")

        sources = list(
            db.scalars(
                select(DataSource)
                .where(DataSource.enabled.is_(True), DataSource.provider_class.is_not(None))
                .order_by(DataSource.priority)
            )
        )
        network = [s for s in sources if s.code not in {"manual", "csv"}]
        if not network:
            line(NO, "No network source is enabled.",
                 "Settings → Data sources → enable 'Pokémon TCG API'. No key needed.")
            print("\n  Nothing else can work until that is on. Stopping here.\n")
            return 1

        values = settings_service.get_all(db)
        blocked = 0
        graded_capable = False

        for source in network:
            print(f"  \033[1m{source.name}\033[0m")
            result = check_source(db, source, values=values, total_cards=total_cards)
            blocked += 0 if result.ok else 1
            graded_capable = graded_capable or result.graded
            print()

        if blocked:
            print(f"  {blocked} of {len(network)} source(s) cannot sync yet — see above.\n")
            return 1

        print("  Everything needed is in place.\n")
        if not graded_capable:
            print("  Remember what these sources cannot do: no graded prices, so the")
            print("  grade-or-sell decision still needs graded comparables you enter")
            print("  or import. They fill the raw value, not the decision.\n")
    return 0


def check_env(db) -> None:
    """Where configuration came from, before anything asks whether it is right.

    This runs first because of the one report that used to be actively
    unhelpful: "needs SLABSTACK_EBAY_APP_ID in the environment", printed to
    somebody looking straight at the line where they set it. The environment and
    the file on disk are different places, and until this section existed
    nothing said which one the application had actually read.
    """
    print("  Configuration")
    files = env_file_report()

    if not any(entry["exists"] for entry in files):
        line(HM, "No .env file — reading the shell environment only.",
             f"Optional. If you meant to use one: {files[0]['path']}")
    for entry in files:
        if not entry["exists"]:
            continue
        if entry["bom"]:
            line(NO, f"{entry['path']} starts with a UTF-8 byte-order mark.",
                 "Invisible in an editor, and it fuses onto the first variable name so nothing "
                 "matches. Re-save the file as UTF-8 without BOM.")
        elif entry["keys"]:
            line(OK, f"{entry['path']} defines {len(entry['keys'])}: {', '.join(entry['keys'])}.")
        else:
            line(NO, f"{entry['path']} exists but sets nothing.",
                 "Usually every line is still commented out from copying .env.example — delete "
                 "the leading # on the lines you filled in.")

    # What the application is actually going to read, which is the only thing
    # that decides whether a provider works.
    #
    # Asked of every source rather than of a hardcoded pair. The list used to be
    # eBay's two variables, which meant a source added later — PriceCharting,
    # say — could have a missing or misspelt key and this section would print
    # nothing at all about it. Silence is the one thing a diagnostic must never
    # say about a broken setting.
    reported = 0
    for source in db.scalars(
        select(DataSource)
        .where(DataSource.provider_class.is_not(None))
        .order_by(DataSource.priority)
    ):
        # A key some sources merely *prefer*: pokemontcg.io works anonymously at
        # a lower rate limit, which is exactly why it is the one source that
        # ships enabled. Calling its absence a failure would put a red cross on
        # a working install, and the same flag the loader reads decides it here.
        optional = bool((source.config or {}).get("api_key_optional"))

        for name, present in credentials_present(source).items():
            reported += 1
            if present:
                origin = (
                    "from a file" if name in LOADED_FROM_ENV_FILES else "exported in your shell"
                )
                line(OK, f"{name} is set ({origin}) — {source.name}.")
            elif optional and name == source.api_key_env_var:
                line(OK, f"{name} is not set — {source.name} does not need one.",
                     "Setting it raises the rate limit, nothing more.")
            elif source.enabled:
                # Enabled and unkeyed is broken now, not one day.
                line(NO, f"{name} is not set, and {source.name} is enabled.",
                     "That source cannot authenticate until it is. Set it in .env or your shell, "
                     "then run this again.")
            else:
                line(HM, f"{name} is not set — {source.name} is switched off, so nothing needs it.")

    if not reported:
        line(OK, "No source needs a credential.",
             "The Pokémon TCG API works with no account and no key.")
    print()


class Result:
    def __init__(self, ok: bool, graded: bool = False) -> None:
        self.ok = ok
        self.graded = graded


def check_source(db, source: DataSource, *, values: dict, total_cards: int) -> Result:
    # 1. Does the adapter load? Catches a missing required credential.
    try:
        provider = load_provider(source)
    except ProviderUnavailableError as exc:
        line(NO, "Will not load.", str(exc))
        return Result(False)

    missing = [name for name, present in credentials_present(source).items() if not present]
    if missing:
        # Not necessarily fatal — some sources work anonymously — but it is the
        # first thing to check when a request comes back 401.
        line(HM, f"Not set: {', '.join(missing)}.")
    caps = provider.capabilities()
    line(OK, f"Adapter loaded ({_describe(caps)}).")

    # 2. Are there cards it could say anything about?
    if provider.requires_external_id:
        linked = [c for c in db.scalars(select(Card)) if (c.external_ids or {}).get(source.code)]
        if not linked:
            line(NO, "No card is linked to this source.",
                 "Open a card → 'Find in catalogue' → pick the match. It only prices cards it "
                 "has been told the provider's id for.")
            return Result(False)
        line(OK, f"{len(linked)} of {total_cards} card(s) linked.")
        if len(linked) < total_cards:
            line(HM, f"{total_cards - len(linked)} not linked and will be skipped.")
        probe = linked[0]
    else:
        if not total_cards:
            line(NO, "No cards in the collection to look up.", "Add a card first.")
            return Result(False)
        # Searched by name, so nothing has to be linked to it first.
        line(OK, f"Searched by name — all {total_cards} card(s) are eligible, no linking needed.")
        probe = db.scalars(select(Card)).first()

    # 3. Can what it returns be stated in your currency?
    currency = values.get("currency", "GBP")
    rates = values.get("fx_rates") or {}
    quoted = getattr(provider, "price_currency", None)
    if quoted and quoted != currency:
        pair, inverse = f"{quoted}_{currency}", f"{currency}_{quoted}"
        if rates.get(pair) or rates.get(inverse):
            line(OK, f"{quoted}→{currency} rate is set ({rates.get(pair) or f'1/{rates[inverse]}'}).")
        else:
            line(NO, f"Quotes {quoted}, and no {pair} rate is set.",
                 f'Settings → Market → Exchange rates: {{"{pair}": 0.79}}. '
                 "Figures are fetched and deliberately not written without one.")
            return Result(False)
    else:
        line(OK, f"Quotes {currency}; no conversion needed.")

    # 4. One real request, now that everything local is in order.
    graded = False
    try:
        if caps.search:
            found = provider.search_card(CardQuery(name=probe.name, limit=1))
            line(OK, f"Reached it: {len(found)} catalogue result(s) for '{probe.name}'.")
        elif caps.sales_history:
            key = MarketKey(
                catalog_key=probe.catalog_key or "",
                query=CardQuery(name=probe.name, card_number=probe.card_number),
            )
            sold = provider.get_sales_history(key)
            line(OK, f"Reached it: {len(sold)} sold listing(s) for '{probe.name}'.")
            graded = caps.graded_prices
        else:
            line(HM, "Nothing to probe with — it supplies neither search nor sales.")
    except CapabilityDeniedError as exc:
        # The healthy-but-not-granted case. Half the source still works.
        line(HM, "Part of this source is not granted to your application.", str(exc))
    except ProviderRequestError as exc:
        line(NO, "Could not be reached.", str(exc))
        print("      Everything local is set up correctly — this is the network or the API.")
        return Result(False)

    # 5. Has anything actually landed?
    stored = {
        "price": _count(db, MarketPrice, source),
        "sale": _count(db, MarketSale, source),
        "listing": _count(db, MarketListing, source),
    }
    held = ", ".join(f"{n} {name}(s)" for name, n in stored.items() if n)
    if held:
        line(OK, f"Stored from this source: {held}.")
    else:
        line(HM, "Nothing stored from this source yet.",
             "Settings → Data sources → 'Refresh prices'.")

    if source.last_sync_at:
        status = source.last_sync_status or "unknown"
        line(OK if status == "ok" else HM,
             f"Last sync: {source.last_sync_at:%Y-%m-%d %H:%M} ({status}).")
        if source.last_sync_error:
            line(HM, f"Last error: {source.last_sync_error}")
    else:
        line(HM, "Never synced.", "Settings → Data sources → 'Refresh prices'.")

    return Result(True, graded)


def _count(db, model, source: DataSource) -> int:
    return db.scalar(
        select(func.count()).select_from(model).where(model.source_id == source.id)
    ) or 0


def _describe(caps) -> str:
    """What this source is actually for, in the words the checks below use."""
    parts = []
    if caps.search:
        parts.append("catalogue search")
    if caps.current_price:
        parts.append("aggregate prices")
    if caps.sales_history:
        parts.append("sold listings")
    if caps.active_listings:
        parts.append("active listings")
    if caps.graded_prices:
        parts.append("graded")
    return ", ".join(parts) or "nothing declared"


if __name__ == "__main__":
    if os.environ.get("SLABSTACK_DATA_DIR"):
        print(f"Using SLABSTACK_DATA_DIR={os.environ['SLABSTACK_DATA_DIR']}")
    sys.exit(main())
