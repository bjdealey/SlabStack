"""The configuration section of `make doctor`.

A diagnostic's worst failure is silence about a broken setting, and that is what
this section used to do: it checked a hardcoded pair of eBay variables, so a
source added later could have a missing or misspelt key and the report would say
nothing at all about it.

So what is pinned here is coverage — every source that reads a variable gets a
line — and the three verdicts that line can carry, which are genuinely different
situations and were briefly all the same one.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import DataSource
from scripts.doctor import check_env


@pytest.fixture
def db(seeded_db):
    """Data sources present: they are what declares the variables."""
    return seeded_db


def enable(db, code: str, on: bool = True) -> DataSource:
    row = db.scalar(select(DataSource).where(DataSource.code == code))
    row.enabled = on
    db.commit()
    return row


def report(db, capsys, env: dict[str, str] | None = None, monkeypatch=None) -> str:
    if env is not None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
    check_env(db)
    return capsys.readouterr().out


def test_every_source_that_reads_a_variable_gets_a_line(db, capsys, monkeypatch):
    """The bug this replaced: a hardcoded pair of names, so anything added later
    was invisible however broken it was."""
    monkeypatch.delenv("SLABSTACK_PRICECHARTING_API_KEY", raising=False)
    out = report(db, capsys)

    for name in (
        "SLABSTACK_PRICECHARTING_API_KEY",
        "SLABSTACK_EBAY_APP_ID",
        "SLABSTACK_EBAY_CERT_ID",
    ):
        assert name in out, f"{name} went unmentioned"


def test_a_missing_key_on_an_enabled_source_is_a_failure(db, capsys, monkeypatch):
    enable(db, "pricecharting")
    monkeypatch.delenv("SLABSTACK_PRICECHARTING_API_KEY", raising=False)

    out = report(db, capsys)

    assert "SLABSTACK_PRICECHARTING_API_KEY is not set, and PriceCharting is enabled." in out
    assert "cannot authenticate" in out


def test_a_missing_key_on_a_disabled_source_is_not_a_problem(db, capsys, monkeypatch):
    enable(db, "pricecharting", on=False)
    monkeypatch.delenv("SLABSTACK_PRICECHARTING_API_KEY", raising=False)

    out = report(db, capsys)

    assert "switched off, so nothing needs it" in out
    assert "cannot authenticate" not in out


def test_an_optional_key_is_never_a_failure(db, capsys, monkeypatch):
    """pokemontcg.io works anonymously, which is why it is the one source that
    ships enabled. A red cross on a working install is a bug in the doctor."""
    monkeypatch.delenv("SLABSTACK_POKEMONTCG_API_KEY", raising=False)

    out = report(db, capsys)

    assert "Pokémon TCG API does not need one" in out
    assert "SLABSTACK_POKEMONTCG_API_KEY is not set, and" not in out


def test_a_key_that_is_set_says_where_it_came_from(db, capsys, monkeypatch):
    """The report that used to be least helpful named a variable to somebody
    looking straight at the line where they had set it. The environment and the
    file on disk are different places."""
    out = report(
        db, capsys, {"SLABSTACK_PRICECHARTING_API_KEY": "abc123"}, monkeypatch=monkeypatch
    )

    assert "SLABSTACK_PRICECHARTING_API_KEY is set (exported in your shell)" in out


def test_a_source_with_no_adapter_is_not_reported(db, capsys, monkeypatch):
    """Those cannot be enabled at all, so their variables are not yet anybody's
    problem."""
    row = db.scalar(select(DataSource).where(DataSource.provider_class.is_(None)))
    assert row is not None, "the seed should still ship planned-but-unbuilt sources"

    out = report(db, capsys)

    assert row.name not in out
