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

## Phase 6 — Submission optimiser ✅

Building the parcel, in `app/services/submissions.py` and `app/services/optimiser.py`.

- **Real batch costing.** Phase 4 costed a hypothetical card in a hypothetical batch; this costs
  the parcel you built. Insurance comes from the real declared values, not one card's value
  multiplied by the batch size.
- **Value-weighted allocation**, which Phase 4 could only promise — it had no other cards to weight
  against. Penny-exact either way, and it still falls back with a note when there is nothing to
  weight by.
- **Minimums are per tier, not per parcel.** Bulk pricing needs N cards *at bulk rates*; thirty
  cards with three at Bulk is three bulk cards. Shipping is shared across the whole parcel
  regardless of tier, which is what makes a mixed submission worth building.
- **The optimiser** routes every ready card at a batch big enough for the bulk tiers to be visible,
  packs by (company, tier), and then **re-costs every card at the size its batch actually came out
  at**. Cards that stop paying are listed with the number that changed and excluded from every
  total.
- **Lifecycle** draft → planned → shipped → received → grading → returned, with actual grades and
  cert numbers recorded per card. A shipped parcel's contents are frozen: what you sent is a
  record, and Phase 8 compares predicted grades against it.
- **Membership break-even** against the fees in the parcel in front of you.

**Learned the hard way, twice.**

`grade_if_batch_filled` was being accepted at re-verification. It is a fair answer while the batch
is hypothetical; once the size is a known fact it means "not in the batch you have", and accepting
it let the optimiser bless a plan on the strength of a submission that did not exist.

A batch of nine routed to Bulk was labelled Bulk while quoting Economy's price, because Bulk needs
twenty-five and was unavailable at that size. Batches now carry both tiers — the one they were
routed to and the one they actually land on — and short batches report what filling them is worth
rather than only asking for more cards.

**And a Phase 4 bug this phase exposed.** The declared value was computed once against the
*headline* grader and then applied to every company's tier eligibility. With no PSA sales stored,
the PSA-weighted figure fell back to the raw value — so a card whose CGC slabs are worth £987 was
declared at £240 and slipped under CGC Bulk's £400 ceiling. The engine was recommending a tier the
card cannot legally use. Declared values are now computed per company, against that company's own
ladder and its own slab prices, which is the rule the best-case panel already followed.

---

## Phase 7 — Analytics ✅

**Built.** `analytics.py` plus `/api/analytics/*` and the Analytics page.

The governing constraint, stated in the module's own docstring: **everything here is a view.** The
decision engine says whether a card is worth grading, the market engine says what it is worth, the
submission engine says what a parcel cost — analytics ranks, filters and compares those answers and
never re-derives one of them. Two rankings of one question drift apart, and the one that goes stale
is whichever the user was not looking at.

- **Ranked opportunities** — the same verdicts as `/collection/decisions`, cut to what you would
  actually send. `batch_size` changes the list, because a card that does not pay alone often pays
  in a submission of twenty.
- **A selling queue.** "Sell it raw" is where the decision engine stops; what to *ask* is the next
  question. The markup over the realistic price scales with liquidity — 5% when it trades weekly,
  18% when it trades twice a year and needs room — and is capped at the upper quartile of real
  sales, because asking more than anyone has recently paid is how a listing sits unsold. A card
  with no raw sales gets no suggested price and says why.
- **Submission ROI** — predicted grades against the grades that came back. Parcels still out are
  counted and reported rather than averaged in at zero, and a grade nobody has sold cannot be
  valued and says so instead of borrowing a neighbouring grade's price.
- **Nine saved cuts**, each a predicate over figures the engines already produced. Where a figure
  is unknown the card does not match and is counted in `unclassified`: an unknown risk is not a low
  risk, and a card with no trend behind it is not a falling one.

**Two of those cuts were dishonest when first written.** "Hard to sell" read a decision value and
"declining" read another, so neither measured what its own label promised. Both now read the
liquidity and trend blocks, and "hard to sell" uses the minimum liquidity score *you* configured
rather than a threshold invented here — so the filter and the verdicts agree by construction.

**And a gap this phase exposed, again by driving the UI.** `submission_cards.predicted_grade` was a
column nothing ever wrote, so the left-hand side of "predicted vs actual" was permanently null and
the returns view could only ever show half a comparison. The prediction is now recorded when a card
*joins* a parcel, not read back when it returns: the prediction worth scoring is the one you held
when you sent the card, and recomputing it later would grade a model against an outcome it has
already seen. Moving a draft to another grader re-takes it; a card with no assessment gets none,
because no prediction was made and there is nothing to be right or wrong about.

**Not built:** the per-card price/volume/liquidity chart. `price_snapshots`,
`GET /api/cards/{id}/market/history` and `SnapshotSeries` all exist and are exercised; only the
chart component is outstanding, and a demo store rebuilt on every page load has no history to plot.

---

## Phase 8 — Learning system ✅

**Built.** `calibration.py`, `/api/analytics/accuracy`, `/api/calibration`, and the "How it's doing"
view.

The only engine here that reasons backwards. Everything else goes condition → distribution → value →
verdict; this takes grades that actually came back and asks whether the model that predicted them
was any good. It is also the only part that cannot be rebuilt from public data: a record of how
*your* cards, assessed by *your* eye, come back from *your* graders can only be earned.

- **Scoring** is a Brier score over the whole distribution rather than the mode, because being 95%
  sure of a 10 and being 40% sure of a 10 are very different claims with the same mode. A grade the
  model gave zero probability scores as a maximal miss rather than being skipped.
- **The bias** is the mean signed error, per grader, reported next to exact / within-half /
  within-one hit rates and a calibration curve — predicted rate against observed rate, per rung of
  the ladder. The headline names the worst rung, which is the sentence the spec asked for.
- **The correction** lands on the two parameters the model already has — where the distribution is
  centred and how wide it is — rather than a new mechanism. That keeps it inspectable: a calibrated
  prediction is the same model with a shifted centre, and the shift is a number you can read.
- **The raw model is kept beside the corrected one**, on the card page as well as in the API. A
  silent adjustment is untrustworthy: you could not tell whether a prediction moved because the card
  differs or because the model learned something.

**Three refusals matter more than the arithmetic.** It will not score a prediction made after the
fact — `submission_cards` freezes the whole distribution when a card *joins* a parcel, because
marking one recomputed afterwards grades a model against an outcome it has already seen. It will not
learn from a handful of results — below `calibration_minimum_sample` the bias is measured, reported
and explicitly not applied, and the offset is clamped besides. And it will not pool graders, because
PSA's bias is not CGC's and a correction learned across both describes neither.

**Kept apart from `strictness`**, which is an opinion the user holds about a grader rather than an
observation measured from their results. Merging them would lose the ability to tell them apart, or
to switch one off.

**Two bugs this phase surfaced, neither in the new code.**
`submission_cards.predicted_probabilities` needed `JSON(none_as_null=True)`: SQLAlchemy stores
Python `None` as the JSON text `null`, which reads back as `None` in Python while `IS NULL` never
matches — so the count of cards that *cannot* be scored came out zero while quietly hiding them. And
PATCHing a submission's `submitted_at` / `received_at` / `returned_at` returned a 500, because the
API took the ISO string straight to a `Date` column without parsing it.

---

## Live market data ✅

The first source that actually reaches the network: `pokemontcg_io`, behind
`app/services/market_sync.py` and the adapter in `app/services/market_data/`.

Chosen because it is the only source a user can try with **no signup, no approval and no payment** —
it answers anonymously and a key only raises the ceiling. eBay and TCGplayer need an application
review; PriceCharting and Cardmarket need paid or account credentials. Those rows stay listed, and
now say what each actually needs rather than "Phase 3".

- **Nothing reaches the internet until a source is enabled**, and `/api/market/refresh` returns 409
  until one is. A source with no adapter cannot be enabled at all.
- **Card lookup** comes with the catalogue: search returns candidates and writes nothing, confirming
  is a separate call, and `apply_fields` names what to accept so a lookup fills gaps rather than
  overwriting decisions. The provider's id is stored, so later syncs price that exact printing.
- **Your own sales win.** `market_prices` was already unique per source, so the two coexist; they
  are now resolved to one row per grade, and a provider price is used only where you have nothing
  better. The card page says which.
- **No invented exchange rate.** A foreign price with no configured rate is fetched, reported, and
  not written — a guessed rate rescales every price silently and the mistake would be invisible.
- **No deletion on failure.** Nothing is cleared before a run.

**What this source cannot do is the important part**, and it is stated in the adapter, the API
contract and the UI: no individual sales means liquidity stays **unknown**; no history means a trend
accrues **forward only** from daily snapshots; and no graded prices means the grading decision is
**still unanswerable** from it alone. A graded key returns nothing rather than the raw price —
answering it would put the same number on both sides and make grading look exactly break-even every
time. A source that filled the raw price and left the decision where it was would otherwise look
like a fix.

**Verified as far as this environment allows.** The sandbox cannot reach any provider host, so the
adapter is tested against recorded fixtures through an injectable transport. That proves the
documented shape parses correctly; it cannot prove the live API still returns it. Driving the UI did
confirm the request is genuinely attempted and that a network failure surfaces as a real error
rather than being swallowed. **The first live request is the verification, and it happens on the
user's machine.**

---

## Sales-level market data ✅

eBay, in `app/services/market_data/ebay.py`. The first source that supplies **evidence** rather
than somebody's index, which changes what three engines can answer.

Everything before this handed over one number. This hands over individual sold listings, and
because slabs sell on eBay with the grade in the title, **one query returns raw sales and PSA 10
sales and CGC 9.5 sales together**. That is the comparison the whole application exists to make,
and until now the graded half of it had to be typed in by hand.

- **Nothing writes a valuation.** Sales go into `market_sales`, listings into `market_listings`,
  and then the Phase 3 pricing engine recomputes from them. So a fetched sale and a typed one go
  through the same exclusion rules, the same outlier fence and the same arithmetic, and the price
  arrives carrying a sample size instead of standing on a third party's authority.
- **Liquidity becomes measurable** — it needs real trades and a sold-to-active ratio, and no
  aggregate price can supply either.
- **No link needed.** A catalogue has to be told its own id for a card; a marketplace is searched
  by name. `requires_external_id` carries that difference, because a marketplace adapter that
  demanded a link would sync nothing, forever, and look exactly like a working sync.
- **Application credentials, not a user's.** Client-credentials OAuth, cached until just before it
  expires. Nothing here can list, bid or buy. Both variable *names* live in config; neither value
  ever touches the database.
- **Active listings are marked inactive, never deleted**, when they stop coming back — otherwise
  the active count only grows and the liquidity denominator rots.
- **The honest denominator.** eBay reports how many listings exist; one page is not the size of
  the market, and counting the page would understate supply — which flatters liquidity, which
  flatters the decision to grade.

**What it cannot do, and this turned out to be bigger than expected.** Sold listings come from
Marketplace Insights, which eBay documents as a **Limited Release, not open to new users**, granted
case by case to selected partners. The realistic assumption is that a normal developer account will
not get it. What a production keyset does give is the Browse API — *active* listings, which are
asking prices.

So for most users this source arrives as **half of itself**: the sold-to-active denominator, and no
sold prices. That is a real contribution to liquidity and nothing else, and it means the graded
prices the decision needs still have to come from somewhere. It is reported as
`CapabilityDeniedError` — deliberately *not* a failure, because the source is healthy and the run
carries on with what it can do.

That reframes what to build next. **PriceCharting** returns PSA 10 / PSA 9 / BGS / CGC prices as
named fields — no title parsing, no 90-day window, and no approval committee, just a paid key. For
the specific blocker this project cares about, a lesser API you can actually get beats a better one
you cannot.

Other limits, where access does exist: sold data covers 90 days, so a longer trend still accrues
forward from snapshots; and asking prices are never written as sales.

**Learned the hard way, twice, and both by driving the UI.**

*"PSA 10 READY" is a raw card.* Marketplace titles are full of grades the seller hopes for —
"would grade a 9 easy", "gem mint candidate" — and every one names a company and a number, so the
existing title parser read them as completed graded sales. In the fixtures that put a £230 raw
card beside a real £420 PSA 10 and pulled the graded price down by nearly half, silently, turning a
card worth grading into one that is not. `parse_grade_from_title` now refuses them and they fall
through to `raw`, which is what they are. The narrowness matters in both directions: "Gem Mint" is
PSA's own name for a 10 and appears in genuine slab titles constantly, so it is deliberately *not*
an aspiration word.

*The declared value denied the sales it had just imported.* With PSA 10 and CGC 9.5 comparables on
screen — and the line "PSA 10 sells +95% against raw" four rows above it — the declared value still
read "no graded sales are stored for this card". The number was right, because an unassessed card
has no grade distribution to weight with. The *reason* was a false claim about the data, and it
would have sent you off to import comparables you already had. Falling back to the raw value has
three quite different causes and they now say which one applies.

**Verified as far as this environment allows.** The sandbox cannot reach eBay, so the adapter runs
against recorded fixtures. For the UI pass those fixtures were served from a local stub with the
adapter's `base_url` pointed at it, so the real adapter, the real sync engine, the real exclusion
rules and the real React screen all ran over real HTTP — which is how both bugs above were found.
That proves the documented shape parses and the chain works end to end; it cannot prove eBay still
returns that shape. **The first live request is the verification, and it happens on your machine.**

---

## Graded prices ✅

PriceCharting, in `app/services/market_data/pricecharting.py`. The source that finally fills the
slab side of the grading decision, which every earlier source left empty.

Chosen after eBay's sold data turned out to be unobtainable in practice: it returns the graded
ladder as named fields for a paid key and no approval committee. A lesser API you can actually have
beats a better one you cannot.

**The mapping is data, and refuses to be guessed.** PriceCharting is a video-game price guide that
also covers cards, and it reuses the game condition fields for card grades — `box-only-price` and
`manual-only-price` hold grades, not boxes and manuals. Which field is which grade is the single
most important fact about this source and the one thing this build could never verify: the sandbox
cannot reach the site and the reachable documentation does not spell it out.

So it lives in `grade_fields` on the data source, ships marked unconfirmed, and **no graded price is
written until a human confirms it**. `make pricecharting-fields CARD="…"` prints what the API
actually returned beside what the mapping claims, so confirming it is half a minute of comparing
against the card's own page. The raw price flows regardless, because "loose" is the one label here
that cannot mean anything else.

That refusal is the whole design. A price under the wrong grade would not fail: it is a real number,
of the right magnitude, in the right currency, and it would quietly invert the recommendation on
every card it touched. Returning nothing is strictly better than returning that.

- **Aggregates, not sales.** `sample_size` stays zero and liquidity stays unknown — this answers
  *what is a slab worth*, never *how easily does it sell*.
- **Prices are already integers in pennies**, which is what this app stores, so nothing is converted
  and no penny is lost on the way in.
- **A bad token answers HTTP 200** with `status: error`, so the adapter checks the body rather than
  the status code — otherwise every card would report "no prices" forever.
- **`as_of` is left null.** The response carries the card's *release* date, and dating today's price
  2021 would be worse than admitting the date is unknown.
- **Linking is per source.** Cards are matched and linked separately for each provider, and the
  lookup dialog now picks which one — without that, PriceCharting would have had no route to a card
  id and would have synced nothing, forever.

---

## Liquidity, finally measurable ✅

Liquidity had read **unknown** on every card, from every source, since Phase 3 — while being one of
the five components of the Grading Opportunity Score. The score was running on four.

PriceCharting's `sales-volume` is the first number any obtainable source has offered that measures
the thing directly: yearly units sold. It describes the *product*, pooled across grades, which is
exactly the shape liquidity is already measured at, so it needs no attribution and gets none.

- **A count, not sales.** It is stored in its own column and deliberately *not* expanded into rows
  in `market_sales`. A derived sale with an invented date would corrupt trend, valuation and the
  outlier fence at once, to answer a question none of them asked.
- **Your own sales win**, the same precedence prices follow and for the same reason: they carry a
  date each, so they answer both how often a card trades *and* whether it traded recently.
- **The reading says which evidence it used.** `basis` is `sales` or `reported_volume`, and the
  card page renders them differently — the windowed counts are hidden for a derived reading,
  because they count sales *you* hold and this never looked at those.
- **The counts stay at zero.** Deriving `sales_90d` from an annual figure would turn a derivation
  into a claim about the user's data.

**Learned the hard way, by driving the UI.** The first version produced a confident **10.0 "very
liquid"** from a single number. Normalising over the components that exist makes a *partial* reading
easier to max out than a complete one — with sales you need frequency and recency both perfect to
reach ten; with a yearly count you need only frequency. A reading with no recency behind it is now
capped one notch below `very_liquid`, which in this application means *you can realise the money
now* — precisely what recency evidences and an annual total cannot.

---

## What is left

`POST /api/cards/identify` is the last `501`. Image-assisted identification needs a vision provider,
not an engine — and it will always be a suggestion the user confirms, never applied silently.

*(PriceCharting is built — see below.)*

The per-card price/volume/liquidity chart is also outstanding — `price_snapshots`,
`GET /api/cards/{id}/market/history` and `SnapshotSeries` all exist and are exercised; only the
chart component is missing.

---

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and every push to `main`, in three jobs:

- **Backend** — ruff, then the test suite, then a check that `docs/schema.sql` still matches the
  models. A model change that forgets `make schema` leaves the documented schema quietly wrong,
  which is worse than having no documented schema.
- **Frontend** — typecheck, the production build, and the *demo* build. The demo is what Pages
  serves, so without that last step a demo-only break would not surface until the deploy runs,
  after the merge.
- **Parity** — `make parity`, comparing the browser port against the server engine field by field.

Three jobs rather than one because the three failures mean different things, and it is worth
seeing which went red without opening the log. `make check` runs the same set locally; if that
passes and CI disagrees, one of the two is lying.

**Not covered:** the browser passes. Most of the real bugs in this build were found by driving the
UI against realistic data — the grade ladder absorbing the lower tail, the pooled-grade trend, the
mixed-grader best case, the batch-size mislabel, the declared value costing an ineligible tier —
and none of them would have been caught by a unit test. A Playwright suite in CI would be worth
having; until then, driving the UI stays part of finishing a phase, not an optional extra.

---

## Deliberately out of scope

Authentication, cloud hosting, payments, social features, mobile apps. SlabStack binds to
`127.0.0.1` and serves one local user.
