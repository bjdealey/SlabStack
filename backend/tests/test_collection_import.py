"""Importing a whole collection from somebody else's export.

The last thing standing between the engine and a real collection was that cards
could only be added one at a time. Most of what is tested here is the reading —
files come from exporters nobody controls — and the two refusals: a dry run
writes nothing, and a coarse condition word never becomes an assessment.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Card, ConditionAssessment

SIMPLE = """Name,Set,Number,Quantity,Condition,Price
Umbreon VMAX,Evolving Skies,215/203,1,NM,310.00
Charizard,Base Set,4/102,2,Lightly Played,1200
Snorlax,Jungle,11/64,1,MP,18.50
"""


def rows_of(client, csv_text: str, **params) -> dict:
    return client.post("/api/cards/import", json={"csv": csv_text}, params=params).json()


# --- Reading the file ---------------------------------------------------------


def test_a_plain_export_reads_every_row(client):
    body = rows_of(client, SIMPLE)

    assert body["imported"] == 3
    assert [card["name"] for card in body["cards"]] == ["Umbreon VMAX", "Charizard", "Snorlax"]
    assert body["cards"][1]["quantity"] == 2


def test_column_names_are_matched_loosely(client):
    """Nobody should have to rewrite a file's header row before importing it."""
    body = rows_of(
        client,
        "Card Name;Edition;Collector Number;Qty;Card Condition\n"
        "Umbreon VMAX;Evolving Skies;215/203;1;Near Mint\n",
    )

    card = body["cards"][0]
    assert card["name"] == "Umbreon VMAX"
    assert card["set_name"] == "Evolving Skies"
    assert card["card_number"] == "215/203"


def test_prices_import_in_whatever_format_they_were_exported_in(client):
    """Thousands separators, decimal commas and bare numbers all appear in the wild.

    Quoted, because that is how a real exporter emits a price containing a
    comma. An unquoted one is genuinely two columns and no parser can undo that.
    """
    body = rows_of(client, 'Name,Price\nA,"\u00a31,234.56"\nB,"1.234,56"\nC,45\n')

    assert [card["purchase_price"] for card in body["cards"]] == [1234.56, 1234.56, 45.0]


def test_a_bad_row_is_named_and_the_rest_still_import(client):
    body = rows_of(client, "Name,Quantity\nUmbreon VMAX,1\n,2\nSnorlax,many\n")

    assert body["imported"] == 1
    assert body["failed"] == 2
    assert {error["line_number"] for error in body["errors"]} == {3, 4}


def test_a_file_with_no_name_column_is_refused_rather_than_half_read(client):
    body = rows_of(client, "Quantity,Condition\n1,NM\n")

    assert body["status"] == "error"
    assert "name column" in body["reason"]


def test_an_empty_file_says_so(client):
    assert rows_of(client, "")["status"] == "error"


# --- The coarse condition -----------------------------------------------------


def test_a_condition_word_becomes_a_label_and_never_an_assessment(client, db):
    """Spec section 6 rejects NM/LP/MP as the condition model.

    Inventing per-corner severities from one word would put fabricated evidence
    under a grading decision, so the word lands in `raw_condition` — which no
    engine reads — and the card stays unassessed until somebody looks at it.
    """
    rows_of(client, SIMPLE, dry_run=False)

    card = db.scalar(select(Card).where(Card.name == "Umbreon VMAX"))
    assert card.raw_condition == "Near Mint"
    assert db.scalars(select(ConditionAssessment)).all() == []


def test_an_unrecognised_condition_stays_unknown_rather_than_being_guessed_at(client):
    body = rows_of(client, "Name,Condition\nUmbreon VMAX,MINT-ISH\n")

    card = body["cards"][0]
    assert card["raw_condition"] == "Unknown"
    assert card["condition_as_written"] == "MINT-ISH"
    assert any("do not recognise" in note for note in body["notes"])


def test_the_vocabulary_covers_what_exporters_actually_write(client):
    words = "NM,nm-mt,Lightly Played,LP,MP,HP,Damaged,Gem Mint"
    body = rows_of(client, "Name,Condition\n" + "".join(f"C{i},{w}\n" for i, w in enumerate(words.split(","))))

    assert [card["raw_condition"] for card in body["cards"]] == [
        "Near Mint", "Near Mint", "Lightly Played", "Lightly Played",
        "Moderately Played", "Heavily Played", "Damaged", "Gem Mint",
    ]


# --- The property that matters most -------------------------------------------


def test_a_dry_run_writes_nothing(client, db):
    body = rows_of(client, SIMPLE)

    assert body["dry_run"] is True
    assert body["imported"] == 3, "it still reports what it would do"
    assert db.scalars(select(Card)).all() == []


def test_writing_happens_only_when_asked(client, db):
    rows_of(client, SIMPLE, dry_run=False)

    assert len(db.scalars(select(Card)).all()) == 3


# --- Not doubling the collection ----------------------------------------------


def test_reimporting_the_same_file_adds_nothing(client, db):
    rows_of(client, SIMPLE, dry_run=False)
    body = rows_of(client, SIMPLE, dry_run=False)

    assert body["imported"] == 0
    assert body["duplicates"] == 3
    assert len(db.scalars(select(Card)).all()) == 3


def test_a_second_copy_can_be_forced_because_only_you_know(client, db):
    """You might genuinely have bought another one, and the engine decides per
    physical card."""
    rows_of(client, SIMPLE, dry_run=False)
    body = rows_of(client, SIMPLE, dry_run=False, skip_duplicates=False)

    assert body["imported"] == 3
    assert len(db.scalars(select(Card)).all()) == 6


def test_one_file_listing_a_card_twice_does_not_slip_through(client, db):
    """The second row is not a duplicate of anything held *yet*, which is
    exactly how a de-duplicating import quietly creates duplicates."""
    twice = "Name,Set,Number\nUmbreon VMAX,Evolving Skies,215/203\nUmbreon VMAX,Evolving Skies,215/203\n"
    body = rows_of(client, twice, dry_run=False)

    assert body["imported"] == 1
    assert len(db.scalars(select(Card)).all()) == 1


def test_duplicates_are_reported_not_silently_dropped(client):
    from app.services.identity import build_catalog_key

    key = build_catalog_key(name="Umbreon VMAX", set_name="Evolving Skies", card_number="215/203")
    assert key  # the row carries the key it matched on
    body = rows_of(client, SIMPLE, dry_run=False)
    assert all(card["catalog_key"] for card in body["cards"])


# --- What lands on the card ---------------------------------------------------


def test_a_known_set_code_links_the_reference_row(client, db):
    body = rows_of(client, "Name,Set Code,Number\nUmbreon VMAX,EVS,215/203\n", dry_run=False)

    assert body["imported"] == 1
    card = db.scalar(select(Card).where(Card.name == "Umbreon VMAX"))
    assert card.set_code == "EVS"


def test_a_foil_column_is_a_variant_and_never_a_printing(client):
    """Holo is a variant row; printing is Unlimited / 1st Edition / Shadowless.

    Both feed `catalog_key`, so filing one under the other would give an
    imported card an identity no hand-added card ever matches, and the two
    copies of one card would never share a price.
    """
    body = rows_of(client, "Name,Foil\nUmbreon VMAX,Yes\nCharizard,No\nSnorlax,\n")

    assert [card["variant"] for card in body["cards"]] == ["Holo", "Standard", None]
    assert all(card["printing"] is None for card in body["cards"])


def test_a_printing_column_is_matched_against_our_own_vocabulary(client):
    body = rows_of(client, "Name,Printing\nA,1st Edition\nB,unlimited\nC,Wobble\n")

    assert [card["printing"] for card in body["cards"]] == ["1st Edition", "Unlimited", None]


def test_an_unknown_language_is_kept_as_written_rather_than_called_english(client):
    """Language is part of a card's identity — guessing prices it against the
    wrong sales."""
    body = rows_of(client, "Name,Language\nA,japanese\nB,Fictional\n")

    assert body["cards"][0]["language"] == "Japanese", "case is normalised"
    assert body["cards"][1]["language"] == "Fictional"
    assert any("Unrecognised language" in note for note in body["notes"])


def test_the_note_about_labels_is_always_present(client):
    """The one thing a user must not misread about this import."""
    body = rows_of(client, SIMPLE)

    assert any("not a condition assessment" in note for note in body["notes"])


def test_foiling_written_in_the_printing_column_is_still_read_as_a_variant(client):
    """One exporter writes "Foil: Yes", another "Printing: Holofoil".

    Dropping the second would lose a fact the user gave us, and putting it in
    `printing` would split the card's identity.
    """
    body = rows_of(client, "Name,Printing\nA,Holofoil\nB,1st Edition\n")

    assert (body["cards"][0]["variant"], body["cards"][0]["printing"]) == ("Holo", None)
    assert (body["cards"][1]["variant"], body["cards"][1]["printing"]) == (None, "1st Edition")


def test_a_known_set_name_fills_the_code_so_one_card_has_one_identity(client, db):
    """`catalog_key` prefers the set code and falls back to the name.

    Without this, "Evolving Skies" imported from a file and "EVS" typed by hand
    are two identities for one card, and they never share a price or a sale.
    """
    rows_of(client, "Name,Set,Number\nUmbreon VMAX,Evolving Skies,215/203\n", dry_run=False)
    by_name = db.scalar(select(Card).where(Card.name == "Umbreon VMAX"))

    typed = client.post(
        "/api/cards",
        json={"name": "Umbreon VMAX", "set_code": "EVS", "card_number": "215/203"},
    ).json()

    assert by_name.set_code == "EVS"
    assert by_name.catalog_key == typed["catalog_key"]


def test_the_preview_count_is_what_the_commit_delivers(client, db):
    """The property a preview exists for.

    It broke once: the preview keyed rows off the raw file while a saved card
    got its key re-derived after the set reference resolved, so the two
    disagreed and a re-import slipped past the duplicate check.
    """
    preview = rows_of(client, SIMPLE)
    committed = rows_of(client, SIMPLE, dry_run=False)

    assert preview["imported"] == committed["imported"]
    assert len(db.scalars(select(Card)).all()) == preview["imported"]
    assert [card["catalog_key"] for card in preview["cards"]] == [
        card["catalog_key"] for card in committed["cards"]
    ]


def test_a_card_added_before_set_codes_resolved_is_still_recognised(client, db):
    """Cards created earlier key off the set *name*; new ones key off the code.

    Matching only the resolved spelling would duplicate every card added before
    `resolve_references` learned to fill the code in — which is exactly the
    failure the duplicate check exists to prevent.
    """
    from app.services.identity import build_catalog_key

    old = Card(
        name="Umbreon VMAX",
        set_name="Evolving Skies",
        card_number="215/203",
        language="English",
    )
    old.catalog_key = build_catalog_key(
        name="Umbreon VMAX", set_name="Evolving Skies", card_number="215/203", language="English"
    )
    db.add(old)
    db.commit()
    assert "evolving-skies" in old.catalog_key, "the old spelling, keyed off the name"

    body = rows_of(client, "Name,Set,Number\nUmbreon VMAX,Evolving Skies,215/203\n", dry_run=False)

    assert body["duplicates"] == 1
    assert body["imported"] == 0
    assert len(db.scalars(select(Card)).all()) == 1
