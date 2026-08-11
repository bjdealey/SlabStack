# Roadmap

The build order from the product specification, with what is done and what each later phase has to
add. The architectural rule holds throughout: **a decision engine with a collection database
attached**, not a collection tracker with grading bolted on.

`evaluate_card(card_id)` is the seam. Its response shape is fixed as of Phase 1; later phases fill
in blocks that currently report `not_implemented` or `insufficient_data`. No phase should need to
change the envelope.

---

## Phase 1 — Foundation ✅

React + FastAPI + SQLite. Card CRUD, search and filters, image upload, condition assessment storage
and scoring, grading/selling configuration, collection dashboard, and the `evaluate_card` envelope.

Delivered beyond the minimum, because later phases depend on it:

- Full 22-table schema for **all** phases, with Alembic migrations
- `catalog_key` identity, so duplicate copies share market data and provider results can be matched
- Integer-minor-unit money throughout, with penny-exact shared-cost allocation
- Configuration-driven grading companies, tiers, memberships and selling profiles
- `MarketDataProvider` abstraction and the provider registry (no network adapters yet)
- Later-phase endpoints registered as `501`s carrying their phase number

---

## Phase 2 — Condition engine ✅

Grade probabilities from a condition assessment. A rules engine plus a spread model, in
`app/services/prediction_service.py`, with every constant either a row or a setting.

- Central estimate blends the weighted mean of the sub-scores with the **worst** of them, so one
  bad attribute is not hidden behind three good ones
- Spread grows with unanswered fields, with disagreement between sub-scores, and never drops below
  a floor for grader inconsistency — the model has no point estimates
- `grade_rules` apply as hard caps or as multipliers on the top of the range
- Discretised over each company's own ladder, so half grades appear only where they are awarded
- `physical` and `market` predictions kept distinct, per spec section 8
- Rules editor and model parameters in Settings; per-company `strictness`, shipped neutral
- User overrides win over the model and survive a rerun

Two things it refuses: predicting from an assessment with nothing answered, and treating an
unanswered field as perfect.

**Learned the hard way:** the bottom of the grade ladder must not absorb the whole lower tail. It
made a card capped at 3 report "probably a 1" whenever the spread was wide. Caught by driving the
UI, not by the unit tests — they all used complete assessments, where the spread is small.

---

## Phase 3 — Market data ✅ (except network providers)

Valuation, liquidity and trend, computed from sales the user already has.
`app/services/market_service.py` does the maths; `sales_import.py` gets the data in and keeps the
bad rows out.

- Manual entry and CSV import, deduplicated on `(source_id, external_id)`. Column names matched
  loosely, prices and dates parsed in whatever format they were exported in, bad rows reported by
  line number while the rest import
- Exclusion heuristics for lots, damage, customs, wrong language, wrong printing, wrong variant,
  wrong grade and best-offer sales — plus an IQR price fence, per grade
- Valuation: median, recency-weighted median, quartiles, realistic and quick-sale figures, each
  carrying `sample_size`, `window_days`, `last_sale_at` and `confidence`
- Liquidity from frequency, recency and the sold-to-active ratio, measured across every grade
- Trend across 7/30/90/180/365 days with its own confidence, measured **within one grade**
- Daily `price_snapshots`, so a price history accrues with no provider connected
- `evaluate_card`'s market, liquidity and trend blocks are real, and `data_confidence` is now the
  weakest link rather than a placeholder

Three rules the filtering follows: excluded is never deleted, only positive evidence excludes (a
title that fails to say "English" is not evidence of a Japanese card), and the outlier fence is not
drawn below eight sales, because a fence drawn by five points is drawn by the points it is meant to
judge.

**Not built:** network provider adapters (PokePrice, PriceCharting, eBay, Cardmarket, TCGplayer).
They need API credentials and each service's terms reviewed, and shipping untested network code
against unreachable APIs would be a guess. `POST /api/market/refresh` stays a `501` that says what
does work without it; the `MarketDataProvider` interface and the disabled `data_sources` rows are
already there. Also outstanding: `CardIdentificationProvider` for image-assisted identification —
always a suggestion the user confirms, never applied silently.

**Learned the hard way, twice.** A trend measured across pooled grades measures *sales mix*, not
price: three PSA 10s selling in a month when none sold the month before makes a still market read
as a 500% jump. And when a valuation falls back outside its window it has to widen the window it
reports — saying "90 days" while valuing sales that span nine months is the false precision the
whole module exists to avoid. Both were found by driving the UI against realistic data, not by the
unit tests.

**Watch for:** blending markets. Language, variant and printing are in `catalog_key` precisely so a
Japanese copy's sales never contaminate an English alt art's median.

---

## Phase 4 — Grading economics ✅

What grading actually costs and what a sale actually nets, in
`app/services/economics.py`.

- **Declared value** as a probability-weighted figure across the grades the card might come back
  as — not the top-grade value, because declaring high buys a more expensive tier than the card
  needs and declaring low leaves it under-insured. Falls down a ladder as evidence thins (graded
  prices → raw value → what you paid), reporting a lower confidence at each step, and is never
  below the raw card. Overridable, stored in its own column, kept apart from the suggestion.
- **Tier eligibility** per card: declared value against each tier's floor and ceiling, batch
  minimums and maximums, membership requirements, and effective dates. Tiers are returned with
  their reasons rather than filtered out — "Bulk needs 25 cards and you have three" beats Bulk
  silently disappearing.
- **Cost per card** = fee after any membership discount + per-card fees + percentage-of-declared
  fee + this card's share of shipping out, shipping back, insurance and handling. Split
  penny-exact through `allocate()`.
- **`batch_size`** on `GET /cards/{id}/evaluation`, because shipping belongs to the parcel rather
  than the card: £40 of postage is £40 on one card and £1.60 across twenty-five. Defaults to 1,
  the honest worst case, and travels back with every figure.
- **Net sale value** per grade after platform, payment and listing fees, postage and packaging —
  using the *graded* postage for slabs. `raw.net_raw_sale_value` is real, which is what every
  grading decision is measured against.
- **Best case per grader**, and the cost breakdown behind every total.

Value-weighted allocation is accepted as a setting but falls back to equal with a note, because
weighting by value needs the other cards in the batch. It becomes real in Phase 6.

**Learned the hard way:** the first version of the "best case" line paired the cheapest fee across
*all* graders with the best slab price across *all* graders — ACE's £18 fee with PSA's £880 slab.
That route does not exist. Best case is now computed strictly within one company, which is also
what makes the real insight visible: ACE is the cheapest to grade and CGC is worth far more, so
cheapest is not best.

---

## Phase 5 — Decision engine ✅

The question the application exists to answer, in `app/services/decision.py` and
`app/services/portfolio.py`.

- **Expected value per route** — `Σ P(grade) × net(grade)`, for every usable (company, tier)
  pair. Profit is measured against **selling the card raw today**, not against zero, because that
  is the alternative you actually have.
- **Minimum profitable grade**, and how often the card comes back at or above it — a question
  about the grade, so it is answered over the whole distribution whether or not that grade has
  ever sold.
- **Probability of profit**, and of clearing £25 / £50 / £100.
- **Downside and upside** as percentiles of the outcome distribution rather than the worst and
  best grades on the ladder: a one-per-cent chance of a 3 is a tail, not a forecast.
- **The Grading Opportunity Score** — profitability, grade odds, liquidity, trend and risk, each
  0–10, weighted by settings you control, reported with its components so it can be argued with.
- **The decision**, plus the liquidity-aware tie-break: the richest route on paper is not always
  the one to take, and the one that loses is surfaced with the reason it lost (§26).
- **`grade_if_batch_filled`** — the card is re-costed at each tier's own minimum, so "not worth
  grading" and "not worth grading *on its own*" stop being the same answer.
- **Risk tolerance** shifts the thresholds and which percentile counts as the downside. It never
  changes the arithmetic: the expected value is the same number for everyone.
- **`GET /api/collection/decisions`** runs the engine across the collection and returns a ranked
  list. It is a separate call from the summary because it costs ~20 ms per card, and a dashboard
  that blocks for nine seconds is a dashboard nobody waits for.

**Coverage is the honest part.** Grades with no sales behind them are *unknown*, not worthless.
Expectations renormalise over the outcomes that can be priced and report what share that was;
probabilities deliberately do **not** — an unpriced grade counts against P(profit), because
"profitable 100% of the time" and "100% of the 13% we can price" are different claims and only one
of them is what a reader hears.

**Learned the hard way, twice:**

*P(profit) renormalised over coverage first.* A card with one priced grade out of six read
"P(profit) 100%" — technically the conditional probability, practically a lie. Making it
unconditional flipped that card from Grade to Hold, correctly, and forced a second fix: the reason
then had to distinguish "this card does not grade well enough" from "we cannot see enough of the
outcomes to say", because those need opposite actions and blaming the card for a gap in your data
is the wrong answer.

*A batched quote wore the wrong batch size.* At `batch_size=1` the engine correctly answered
"worth grading, but not on its own" and quoted the batched cost — £20.20 — beside `assumed_batch_size: 1`.
The number was right and the label made it a lie. A route now carries the submission it was
costed against, and the recommendation reports that rather than what you asked for.

**`make parity`** runs both the server engine and the browser port over the same cases and compares
every field. The demo is a hand-maintained port, so it can drift without anything failing to
compile — this is what notices. It has already caught a real one: the two rendered negative money
differently.

---

## Phase 6 — Submission optimiser ← next

**Build:** batch creation and drag-and-drop, whole-collection optimisation against tier minimums
and value ceilings, membership break-even, shared-cost allocation across a real batch, and the
"move card #184 from Economy to Bulk to save £2.20" suggestion. The tables and the allocation maths
already exist.

---

## Phase 7 — Analytics

**Build:** ranked opportunities, the raw selling queue with suggested listing prices, price/volume/
liquidity charts per card, submission ROI, and the one-click collection filters (grade now, grade
if batch filled, sell raw, hold, high risk, high upside, low liquidity, undervalued, declining).

---

## Phase 8 — Learning system

**Build:** record actual grades into `prediction_results`, score predictions (Brier), surface the
user's personal bias ("your predicted PSA 10 rate runs 14 points above your actual rate"), and feed
a calibrated adjustment back into the Phase 2 model as `source: calibrated` — keeping the raw model
output alongside it.

This is the feature that compounds. Everything else can be rebuilt from public data; a personal
calibration curve can only be earned.

---

## Deliberately out of scope

Authentication, cloud hosting, payments, social features, mobile apps. SlabStack binds to
`127.0.0.1` and serves one local user.
