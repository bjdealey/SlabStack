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

## Phase 4 — Grading economics ← next

**Have:** companies, tiers, minimums, declared-value ceilings, memberships, selling profiles, and
`allocate()` for shared costs.

**Build:**

1. Declared value: a suggestion with a confidence, overridable, stored separately from the user's
   number. Not simply the PSA 10 value.
2. Tier eligibility per card: declared value against the ceiling, batch minimums, membership
   requirements.
3. Total cost per card = grading fee + per-card fees + allocated share of shipping, insurance,
   handling and membership.
4. Allocation methods: equal and value-weighted, user-selectable.
5. Net sale value per grade after platform fees, payment fees, postage and packaging — using the
   *graded* postage figures for slabs.

---

## Phase 5 — Decision engine

**Build:**

1. Expected value: `Σ P(grade) × net_value(grade)`, per company and tier.
2. Minimum profitable grade.
3. Expected profit and ROI against the raw net alternative.
4. Probability of profit, and of profit above £25 / £50 / £100.
5. Downside (worst reasonable outcome) and upside.
6. The composite Grading Opportunity Score, weighted by the user's configurable weights.
7. The decision itself, and the liquidity-aware tie-break: when CGC shows more theoretical profit
   but PSA is far more liquid, recommend PSA **and say why** — surfacing the alternative rather
   than hiding it.
8. Risk profiles (conservative / balanced / aggressive) shifting the thresholds.

**Watch for:** the spec's core principle. Optimise realisable, risk-adjusted profit — not
theoretical card value. A recommendation that ignores liquidity is a recommendation to hold an
unsellable slab.

---

## Phase 6 — Submission optimiser

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
