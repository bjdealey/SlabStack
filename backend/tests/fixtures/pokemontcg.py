"""Recorded api.pokemontcg.io responses, shaped as the v2 API documents them.

Hand-built rather than captured, because this build has never had network access
to capture from — which makes one thing worth stating plainly: these fixtures
prove the adapter parses *this* shape correctly. They cannot prove the live API
still returns it. The first real request is the verification, and it happens on
the user's machine.

So the adapter is written to fail loudly on an unexpected shape rather than
quietly returning a half-populated row, and the cases below include the awkward
ones — a card with no price block, a printing that does not match the variant,
a zero price — because those are what a real catalogue is full of.
"""

from __future__ import annotations

BASE = "https://api.pokemontcg.io/v2"

#: A card with the full picture: both marketplaces, several printings.
UMBREON = {
    "id": "swsh7-215",
    "name": "Umbreon VMAX",
    "supertype": "Pokémon",
    "number": "215",
    "rarity": "Secret Rare",
    "set": {
        "id": "swsh7",
        "name": "Evolving Skies",
        "series": "Sword & Shield",
        "printedTotal": 203,
        "total": 237,
        "ptcgoCode": "EVS",
        "releaseDate": "2021/08/27",
    },
    "images": {
        "small": "https://images.pokemontcg.io/swsh7/215.png",
        "large": "https://images.pokemontcg.io/swsh7/215_hires.png",
    },
    "tcgplayer": {
        "url": "https://prices.pokemontcg.io/tcgplayer/swsh7-215",
        "updatedAt": "2026/08/10",
        "prices": {
            "holofoil": {
                "low": 300.0,
                "mid": 420.0,
                "high": 600.0,
                "market": 410.55,
                "directLow": 399.99,
            }
        },
    },
    "cardmarket": {
        "url": "https://prices.pokemontcg.io/cardmarket/swsh7-215",
        "updatedAt": "2026/08/10",
        "prices": {
            "averageSellPrice": 350.0,
            "lowPrice": 300.0,
            "trendPrice": 362.5,
            "avg7": 358.0,
            "avg30": 365.0,
        },
    },
}

#: Two printings, so variant matching has something to get wrong.
PIKACHU = {
    "id": "swsh45-25",
    "name": "Pikachu",
    "number": "25",
    "rarity": "Common",
    "set": {
        "id": "swsh45",
        "name": "Shining Fates",
        "printedTotal": 72,
        "ptcgoCode": "SHF",
        "releaseDate": "2021/02/19",
    },
    "images": {"small": "https://images.pokemontcg.io/swsh45/25.png"},
    "tcgplayer": {
        "updatedAt": "2026/08/09",
        "prices": {
            "normal": {"low": 0.5, "mid": 1.2, "market": 0.94},
            "reverseHolofoil": {"low": 2.0, "mid": 3.4, "market": 3.11},
        },
    },
}

#: In the catalogue, but nobody is listing it. A real and common state.
UNPRICED = {
    "id": "base1-4",
    "name": "Charizard",
    "number": "4",
    "rarity": "Rare Holo",
    "set": {
        "id": "base1",
        "name": "Base",
        "printedTotal": 102,
        "ptcgoCode": "BS",
        "releaseDate": "1999/01/09",
    },
    "images": {"small": "https://images.pokemontcg.io/base1/4.png"},
}

#: A price block that exists but is all zeros — must read as "no price", not £0.
ZERO_PRICED = {
    "id": "swsh1-1",
    "name": "Celebi V",
    "number": "1",
    "set": {"id": "swsh1", "name": "Sword & Shield", "printedTotal": 202, "ptcgoCode": "SSH"},
    "tcgplayer": {
        "updatedAt": "2026/08/01",
        "prices": {"holofoil": {"low": 0, "mid": 0, "market": 0}},
    },
}


def search_response(*cards: dict) -> dict:
    return {
        "data": list(cards),
        "page": 1,
        "pageSize": 20,
        "count": len(cards),
        "totalCount": len(cards),
    }


def card_response(card: dict) -> dict:
    return {"data": card}


def transport(**extra: dict):
    """A RecordedTransport preloaded with the cards above."""
    from app.services.market_data.http import RecordedTransport

    responses = {
        f"{BASE}/cards/{card['id']}": card_response(card)
        for card in (UMBREON, PIKACHU, UNPRICED, ZERO_PRICED)
    }
    responses[f"{BASE}/cards"] = search_response(UMBREON)
    responses.update(extra)
    return RecordedTransport(responses=responses)
