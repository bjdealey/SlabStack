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

## Phase 3 — Market data ← next

**Have:** `market_sales`, `market_listings`, `market_prices`, `price_snapshots`, `data_sources`,
and the `MarketDataProvider` interface. Grade probabilities now exist too, so the moment graded
prices land, expected value becomes computable.

**Smallest useful slice:** manual and CSV sales import. No provider, no API key, no terms review —
and it is what turns the dashboard's "not calculated" into numbers.

**Build:**

1. Adapters behind the existing interface. Official APIs only, under each service's terms — a
   source that requires scraping a site that forbids it does not belong here.
2. Import + dedupe on `(source_id, external_id)`, writing to `catalog_key`.
3. Exclusion filtering: lots, bundles, damaged, wrong card/language/variant/grade, price outliers.
   Excluded rows are kept with a reason and are reversible.
4. Valuation: median, recency-weighted median, quartiles, realistic and quick-sale figures — with
   `sample_size`, `window_days` and `confidence` attached to every number.
5. Liquidity score and band from sales counts, gaps and the sold-to-active ratio.
6. Trend across 7/30/90/180/365 days, **with its own confidence** — +25% from three sales is not
   +12% from a hundred and fifty.
7. Daily `price_snapshots`, so your history accrues regardless of provider.
8. `CardIdentificationProvider` for image-assisted identification — always a suggestion the user
   confirms, never applied silently.

**Watch for:** blending markets. Language, variant and printing are in `catalog_key` precisely so a
Japanese copy's sales never contaminate an English alt art's median.

---

## Phase 4 — Grading economics

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
