"""Normalised card identity.

Two physical copies of the same card share one market history, and provider
results have to be matched to something stable. ``catalog_key`` is that stable
thing: a lowercase, punctuation-free join of the fields that actually change
which market a card trades in.

Language, printing and variant are part of the key on purpose — a Japanese
Umbreon VMAX, an English Alt Art and an English Reverse Holo are three different
markets, and blending their sales is the single easiest way to produce a
confidently wrong valuation.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(value: str | None, fallback: str = "unknown") -> str:
    if not value:
        return fallback
    slug = _NON_ALNUM.sub("-", value.strip().lower()).strip("-")
    return slug or fallback


def build_catalog_key(
    *,
    name: str,
    set_code: str | None = None,
    set_name: str | None = None,
    card_number: str | None = None,
    variant: str | None = None,
    language: str | None = None,
    printing: str | None = None,
) -> str:
    """Build the identity key. Stable for a given set of inputs."""
    set_part = _slug(set_code or set_name, "noset")
    number_part = _slug(card_number, "nonum")
    # A number is enough to identify the card within a set; the name is only a
    # fallback so that catalogue-less entries still get a distinct key.
    name_part = "" if card_number else _slug(name)
    parts = [
        _slug(language, "english"),
        set_part,
        number_part,
        _slug(variant, "standard"),
        _slug(printing, "unlimited"),
    ]
    if name_part:
        parts.insert(3, name_part)
    return "|".join(parts)


def grade_label(company_code: str | None, grade: float | None) -> str:
    """Human/lookup label for a grade: ``raw``, ``PSA 10``, ``CGC 9.5``."""
    if grade is None or company_code is None:
        return "raw"
    text = f"{grade:g}"
    return f"{company_code.upper()} {text}"
