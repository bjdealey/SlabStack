# SlabStack database schema

SQLite, one file, local. 22 tables. The SQLAlchemy models in `backend/app/models/` are the source
of truth; `docs/schema.sql` is generated from them (`cd backend && python -m scripts.dump_schema`)
and Alembic owns changes from there.

---

## Conventions

| Convention        | Meaning                                                                          |
| ----------------- | -------------------------------------------------------------------------------- |
| `*_minor`         | Integer count of minor currency units (pence). Never a float. See below.          |
| `catalog_key`     | Normalised card identity. Shared by duplicate copies; how market data is keyed.    |
| `is_current`      | Marks the live row where history is kept (condition assessments, predictions).     |
| `id`              | 32-char UUID hex, generated application-side.                                      |
| `*_pct`           | Percentage as a number: `12.0` means 12%.                                          |
| Enum columns      | `TEXT` with a `CHECK` constraint, not native enums — adding a value is a migration, not a table rebuild. |

### Why integer money

Expected value sums a probability-weighted outcome across every grade, for every card, across a
whole submission. In floats that drifts; in pence it cannot. The API converts once at the edge
(`app/money.py`), and `allocate()` splits shared costs so the parts always sum exactly back to the
total — no lost pennies when £53.00 of shipping is spread over 25 cards.

### Why one row per physical card

Copy A can grade a 10 and copy B an 8. `quantity` exists for interchangeable bulk, but anything
entering a grading workflow must be its own row — `POST /api/cards/{id}/split` does that.

### Why the local database is the source of truth

Providers write *into* these tables; every calculation reads only from here. An API going dark
costs future updates, never history. `price_snapshots` means your own long-run price series builds
even from a provider that offers no history of its own.

---

## Table map

### Collection

| Table                     | Purpose                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `cards`                   | One row per physical card. Identity, ownership, user overrides.          |
| `card_images`             | Front/back/detail/slab images. Metadata in the DB, files on disk.        |
| `collection_groups`       | Folders, watchlists, and saved filters (`kind = 'smart'`).               |
| `collection_group_cards`  | Group membership.                                                       |

`cards` keeps denormalised `set_name`, `set_code` and `variant` alongside the FKs, so a card stays
readable if its reference row is deleted and can be created from free text before the catalogue is
populated.

User-owned columns that the engine must never overwrite: `user_raw_value_minor`,
`decision_override`, `decision_override_reason`, `review_after`.

### Catalogue

| Table           | Purpose                                                                |
| --------------- | ---------------------------------------------------------------------- |
| `sets`          | Set code, name, series, release date. Unique on `(code, language)`.     |
| `card_variants` | Alt Art, Reverse Holo, Illustration Rare… Variant decides which market a card trades in. |

### Condition and prediction

| Table                    | Purpose                                                                |
| ------------------------ | ---------------------------------------------------------------------- |
| `condition_assessments`  | 16 defect fields × 2 faces, 8 centering measurements, derived sub-scores. |
| `grade_rules`            | Configurable defect caps and probability adjustments.                   |
| `grade_predictions`      | Probability distribution over grades, per company.                      |
| `prediction_results`     | Predicted vs actual — the Phase 8 calibration input.                    |

`condition_assessments` is deliberately wide rather than an EAV table: the fields are a fixed
vocabulary, and a wide row makes "all cards with moderate-or-worse creasing" a plain index scan.
Per-defect free text lives in `front_defect_notes` / `back_defect_notes` JSON maps rather than 32
more columns.

`grade_predictions.kind` keeps two questions apart: `physical` (what the card is) and `market`
(what a given grader will award it).

`grade_rules` holds *our estimated model*, not any grader's published standard — which is why it is
data the user can tune, not code. A rule triggers on a field at or above a severity and either caps
the achievable grade (`max_grade`) or scales probability mass above a grade
(`probability_multiplier` + `penalty_from_grade`).

### Grading configuration

| Table                  | Purpose                                                                |
| ---------------------- | ---------------------------------------------------------------------- |
| `grading_companies`    | PSA, CGC, ACE, BGS, SGC — and any you add. Carries `market_recognition_score`. |
| `grading_tiers`        | Price, minimum cards, declared-value ceiling, turnaround, fees.          |
| `grading_memberships`  | Annual fee and discount, so "does membership pay for itself?" is answerable. |
| `grading_submissions`  | A batch: company, tier, shared shipping/insurance, allocation method.    |
| `submission_cards`     | Per-card declared value, fee, allocated overhead, predicted and actual grade. |

`grading_tiers` carries `effective_from` / `effective_to` so a historical submission keeps costing
what it actually cost when a grader changes price.

`grading_tiers.additional_fees_minor` (per submission) and `per_card_fees_minor` (per card) are
separate because they allocate differently across a batch.

`submission_cards` stores `declared_value_minor` and `system_declared_value_minor` side by side —
the system's suggestion and your number are different facts.

### Market

| Table              | Purpose                                                                     |
| ------------------ | --------------------------------------------------------------------------- |
| `data_sources`     | Provider registry: adapter path, enablement, the *name* of the API-key env var. |
| `market_sales`     | Completed sales. The primary evidence for every valuation.                   |
| `market_listings`  | Active listings, for the sold-to-active ratio in the liquidity score.         |
| `market_prices`    | Derived valuation per (identity, grade): median, weighted median, quartiles.  |
| `price_snapshots`  | One row per identity/grade/day — your own long-term price history.            |

`market_sales.is_excluded` + `exclusion_reason` mean filtered sales are kept, never deleted, so you
can inspect and reverse any automatic exclusion (lot, wrong variant, outlier, suspected fake…).

`market_prices` travels with `sample_size`, `window_days`, `last_sale_at` and `confidence`. A price
without its evidence is false precision. `user_value_minor` sits alongside the computed columns
rather than overwriting them.

`data_sources` stores `api_key_env_var`, never a key.

### Configuration

| Table                    | Purpose                                                              |
| ------------------------ | -------------------------------------------------------------------- |
| `selling_cost_profiles`  | Per-platform fees, postage and packaging. Graded postage kept separate — a slab is heavier and normally tracked. |
| `app_settings`           | Typed key/value. Only keys you have changed are stored; defaults live in code. |

---

## Relationships

```
sets ──┐                         ┌── card_images
       ├──< cards >──────────────┤
card_variants                    ├── condition_assessments ──< grade_predictions
                                 ├── collection_group_cards >── collection_groups
                                 ├── submission_cards >── grading_submissions
                                 └── prediction_results

grading_companies ──< grading_tiers
                  ├──< grading_memberships
                  └──< grading_submissions

data_sources ──< market_sales
             ├──< market_listings
             ├──< market_prices
             └──< price_snapshots
                        │
                 (joined by catalog_key, not FK —
                  market data outlives any single card row)
```

Foreign keys are enforced: SQLite ignores them unless `PRAGMA foreign_keys=ON` is set per
connection, which `app/db.py` does on every connect. Without it, deleting a card would silently
orphan its images and assessments.

---

## Indexes

Beyond primary and unique keys:

- `cards`: `name`, `set_name`, `set_code`, `card_number`, `pokemon`, `variant`, `status`,
  `(set_code, card_number)`, `catalog_key`
- `condition_assessments`: `card_id`, `(card_id, is_current)`
- `grade_predictions`: `card_id`, `(card_id, is_current)`
- `market_sales`: `catalog_key`, `grade_label`, `sale_date`, `(catalog_key, grade_label, sale_date)`
- `price_snapshots`: `(catalog_key, snapshot_date)`

Search is a `LIKE` scan rather than FTS5: a personal collection is thousands of rows, and an FTS
index would need its own synchronisation path for no measurable gain.

---

## Migrations

Alembic, configured with `render_as_batch=True` — SQLite cannot `ALTER` most things in place, so
Alembic rebuilds the table instead. Without it every Phase 2+ schema change would fail against an
existing database.

```bash
cd backend
alembic upgrade head                                  # apply
alembic revision --autogenerate -m "add x"            # after editing a model
python -m scripts.dump_schema                         # refresh docs/schema.sql
```

A fresh install does not need Alembic — nobody should have to run a migration tool to open the app
for the first time. Startup calls `create_all`, seeds reference data, and then *stamps* the
database at head. Without that stamp the first `alembic upgrade head` would try to create tables
that already exist. A database Alembic already knows about is left alone, so both orders work:

| First run                | Then                                    | Result       |
| ------------------------ | --------------------------------------- | ------------ |
| Start the app            | `alembic upgrade head` → no-op          | at head ✅   |
| `alembic upgrade head`   | Start the app → `create_all` no-op, seeds | at head ✅ |

---

## Seed data

`app/services/seed.py`, idempotent, never overwrites an existing row:

- 5 grading companies; CGC and ACE tiers priced from the product specification, PSA as structure
  only (`active: false`, price 0) for you to fill in
- 3 selling profiles (eBay UK, Cardmarket, private sale) with fee estimates flagged for checking
- 8 data sources — `manual` and `csv` enabled, every network provider disabled
- 13 card variants, 13 starter sets, 15 grade rules, one "Watchlist" group

**Pricing honesty rule:** a tier is only seeded `active` when it carries a price we can attribute.
An unpriced tier left active would cost a submission at £0 and make everything look profitable.
