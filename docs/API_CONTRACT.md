# SlabStack API contract

Version `0.1.0` · base path `/api` · live schema at `/api/openapi.json`, browsable at `/api/docs`.

This document is the contract for **every phase**, not just the one that is built. Endpoints
marked ✅ are implemented; endpoints marked ⏳ are registered and return `501` with the phase
attached, so a client can be written against the whole surface today and degrade honestly.

---

## Conventions

### Money

Every monetary value on the wire is in **major units** — `18.8` means £18.80. It is stored and
calculated as an integer count of minor units (pence) server-side, so summing expected values
across a 400-card collection cannot drift. Columns ending `_minor` never appear in a response.

### Currency

One currency per installation, set in `/api/settings` (`currency`). Grading tiers and selling
profiles each carry their own `currency` field so a US-priced tier can sit alongside a UK one;
cross-currency conversion is not implemented and is out of scope for Phase 1.

### Dates and times

`date` fields are `YYYY-MM-DD`. `datetime` fields are ISO 8601 with a UTC offset.

### Pagination

List endpoints that can grow unbounded return an envelope, never a bare array:

```json
{ "items": [], "total": 427, "page": 1, "page_size": 50, "total_pages": 9 }
```

Query parameters: `page` (≥ 1, default 1), `page_size` (1–500, default 50).

Reference-data endpoints (`/sets`, `/variants`, `/groups`, `/grading/companies`,
`/selling-profiles`, `/data-sources`) return plain arrays — they are bounded by configuration.

### Errors

Every failure uses one envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Card 'abc123' was not found.",
    "details": { "resource": "Card", "id": "abc123" }
  }
}
```

| `code`              | HTTP | Meaning                                                             |
| ------------------- | ---- | ------------------------------------------------------------------- |
| `validation_error`  | 422  | Body failed validation. `details.fields` maps field path → message.  |
| `not_found`         | 404  | No such record.                                                      |
| `conflict`          | 409  | Unique constraint, or a delete blocked by references.                |
| `invalid_image`     | 400  | Upload was not a decodable image, or exceeded the size limit.        |
| `cannot_split`      | 400  | Split requested on a single-copy card.                               |
| `unknown_setting`   | 400  | Setting key is not in the registry.                                  |
| `invalid_setting`   | 400  | Setting value failed its type/range/consistency rule.                |
| `not_implemented`   | 501  | Documented endpoint, later phase. `details.phase` says which.        |
| `network_error`     | —    | Client-side only: the API could not be reached.                      |

### PATCH semantics

`PATCH` bodies are sparse. An omitted field is left alone; a field present as `null` clears the
value. `PUT` replaces the whole resource.

### Authentication

None, by design. SlabStack binds to `127.0.0.1` and serves a single local user. Introducing auth
would be a change of product, not a change of endpoint.

---

## System

| Method | Path                 | Status | Description                                              |
| ------ | -------------------- | ------ | -------------------------------------------------------- |
| GET    | `/api/health`        | ✅     | Service status, database path, row counts, current phase. |
| POST   | `/api/system/seed`   | ✅     | Insert missing reference data. Idempotent; never overwrites your edits. |
| GET    | `/api/meta/enums`    | ✅     | Every controlled vocabulary plus the defect field list.   |

`GET /api/meta/enums` exists so the UI has no hard-coded dropdowns:

```json
{
  "enums": { "severity": ["none", "minor", "moderate", "severe", "unknown"], "...": [] },
  "defect_fields": ["corner_tl", "...", "misc_defects"],
  "corner_fields": ["corner_tl", "corner_tr", "corner_bl", "corner_br"]
}
```

---

## Cards

| Method | Path                          | Status | Description                            |
| ------ | ----------------------------- | ------ | -------------------------------------- |
| GET    | `/api/cards`                  | ✅     | Search, filter, sort, paginate.        |
| POST   | `/api/cards`                  | ✅     | Add one card.                          |
| POST   | `/api/cards/bulk`             | ✅     | Add up to 1000 cards in one request.   |
| POST   | `/api/cards/import`           | ✅     | Import a CSV export. Dry run by default. |
| GET    | `/api/cards/{id}`             | ✅     | One card, with its images.             |
| PATCH  | `/api/cards/{id}`             | ✅     | Sparse update.                         |
| DELETE | `/api/cards/{id}`             | ✅     | Delete the card, its images and its assessments. |
| POST   | `/api/cards/{id}/split`       | ✅     | Split a stack into one row per physical copy. |
| POST   | `/api/cards/{id}/sold`        | ✅     | Record what it actually sold for.      |
| GET    | `/api/cards/{id}/sold`        | ✅     | That record, or `null`.                |
| DELETE | `/api/cards/{id}/sold`        | ✅     | Undo it; the card returns to the collection. |
| GET    | `/api/cards/{id}/evaluation`  | ✅     | **The decision-engine call.** See below. |
| POST   | `/api/cards/identify`         | ⏳ P3  | Suggest an identity from images.        |

### Importing a collection

`POST /api/cards/import?dry_run=true&skip_duplicates=true` takes `{ "csv": "…" }` and reads a
collection export. Until it existed the only way in was one card at a time — fine for the card in
your hand, hopeless for the four hundred in a box.

**Dry run by default.** A bad import is not a wrong answer on screen, it is four hundred rows you
now have to find and delete, so the first pass reads the file, says exactly what it found, and
writes nothing.

```jsonc
{
  "dry_run": true, "status": "partial",
  "imported": 9, "duplicates": 0, "failed": 1,
  "cards": [{
    "line_number": 2, "name": "Umbreon VMAX",
    "set_name": "Evolving Skies", "set_code": "EVS",   // resolved, not as written
    "card_number": "215/203", "quantity": 1,
    "variant": "Holo", "printing": null, "language": "English",
    "raw_condition": "Near Mint",
    "condition_as_written": null,        // set when we could not read it
    "catalog_key": "english|evs|215-203|holo|unlimited",
    "duplicate_of": null                 // set when a card already held matches
  }],
  "errors": [{ "line_number": 10, "message": "No card name on this row." }],
  "notes": [ … ]
}
```

Column names are matched loosely and only a name is required. Understood: name, set, set code,
number, quantity, condition, language, foil, printing, rarity, price, currency and purchase date.

**A condition column is a label, not an assessment.** It lands in `raw_condition`, which is
documented as a quick label and which no engine reads. It deliberately does *not* become a
`condition_assessment`: spec §6 rejects NM/LP/MP as the condition model, and inventing per-corner
severities from one word would put fabricated evidence under a grading decision. Imported cards
report "not assessed" until somebody looks at them, which is true. Anything outside the vocabulary
stays `Unknown` and travels back as written rather than being rounded to the nearest guess.

**Foil is a variant, not a printing.** `Holo` and `Reverse Holo` are variant rows; `printing` is
Unlimited / 1st Edition / Shadowless. Both feed `catalog_key`, so filing one under the other gives
an imported card an identity no hand-added card ever matches — and the two copies never share a
price. A foiling word found in a `Printing` column is read as the variant it plainly is, because
exporters disagree about which header carries it.

**An unrecognised language is kept as written**, never assumed to be English: language is part of a
card's identity, so guessing prices the card against the wrong sales.

**Re-importing must not double the collection.** Each row is matched on `catalog_key` against what
is already held, and reported rather than silently dropped — you might genuinely have bought a
second copy, and `skip_duplicates=false` says so. Rows are matched on both the resolved key and the
key the raw row would have produced, because a card added before `resolve_references` learned to
fill a set code in from its name carries the older spelling.

### Recording a sale

`POST /api/cards/{id}/sold` records **the one figure in this application that is not a projection**.
Everything else — what a card is worth, what grading costs, what a sale would net — is an estimate;
this is the money that arrived.

Only `sold_on` and `gross` are required. Fees, postage and packaging are filled in from the selling
profile so that recording a sale is a price and a date rather than a form of nine boxes.

```jsonc
{ "sold_on": "2026-08-12", "gross": 900.00,
  "sold_graded": true, "grade_label": "CGC 10",
  "net_proceeds": 800.00,      // from a payout statement — wins over every estimate
  "grading_cost": 60.00 }      // null means unrecorded, NEVER free
```

**A payout you supply beats a payout we compute**, and the response says which it was through
`net_is_user_entered`. A statement is a fact; a fee model is not, and the two are kept apart the way
every other override in this application is.

**`grading_cost` null means unrecorded, not free.** A realised profit computed without it would
flatter grading, which is the exact bias the whole build exists to correct.

The card is marked `sold`. A second sale on the same card is a `409` — a card sells once, and two
records would double the realised profit. `DELETE` undoes the record and returns the card to the
collection.

### `GET /api/cards` parameters

| Parameter          | Type      | Notes                                                       |
| ------------------ | --------- | ----------------------------------------------------------- |
| `q`                | string    | Case-insensitive match on name, set name/code, number, Pokémon, variant, notes. |
| `set_id`, `set_code` | string  | Exact set.                                                  |
| `language`, `variant`, `rarity`, `pokemon` | string | Exact match.               |
| `status`           | string[]  | Repeatable. Any of the `card_status` vocabulary.            |
| `is_promo`         | boolean   |                                                             |
| `group_id`         | string    | Cards in a collection group.                                |
| `decision_override`| string    | Cards you have pinned to a decision.                        |
| `has_images`       | boolean   | `false` finds the backlog of unphotographed cards.          |
| `has_condition`    | boolean   | `false` finds the unassessed backlog.                       |
| `sort`             | string    | `created_at` \| `updated_at` \| `name` \| `set_code` \| `card_number` \| `purchase_price` \| `quantity` \| `release_date`. |
| `order`            | `asc`\|`desc` | Default `desc`.                                         |

### Card object

```jsonc
{
  "id": "9f2c…",
  "name": "Umbreon VMAX",
  "set_id": "3a1…", "set_name": "Evolving Skies", "set_code": "EVS",
  "card_number": "215/203",
  "variant_id": "7c4…", "variant": "Alternate Art",
  "language": "English", "printing": "Unlimited",
  "rarity": null, "pokemon": "Umbreon", "card_type": null,
  "is_promo": false, "release_date": null,

  // Derived identity: two physical copies of the same card share it, and market
  // data is keyed by it. Never sent by the client.
  "catalog_key": "english|evs|215-203|alternate-art|unlimited",

  "raw_condition": "Near Mint",
  "quantity": 1,
  "purchase_price": 185.0, "purchase_currency": null, "purchase_date": "2025-11-02",
  "status": "in_collection",

  // Your own figure, always kept apart from anything the system computes.
  "user_raw_value": 210.0,
  "decision_override": null, "decision_override_reason": null,
  "review_after": null,                 // "recheck in 30 days" for cards on hold

  "notes": null, "external_ids": null,
  "created_at": "2026-08-10T21:14:02Z", "updated_at": "2026-08-10T21:14:02Z",
  "images": [ /* CardImage */ ],
  "primary_image_url": "/api/images/1a2…/thumbnail",
  "has_condition_assessment": true
}
```

`POST`/`PATCH` accept the writable subset. Only `name` is required — a collection is entered in a
hurry, and a card with just a name beats a card that was never added.

### Splitting

`quantity` exists for interchangeable bulk. Grading is per copy — copy A can grade a 10 and copy B
an 8 — so anything headed for a submission must be its own row.
`POST /api/cards/{id}/split` with optional `{"count": 2}` breaks copies out; images, assessments
and notes stay on the original row. Splitting a single copy returns `400 cannot_split`.

---

## Images

| Method | Path                            | Status | Description                    |
| ------ | ------------------------------- | ------ | ------------------------------ |
| POST   | `/api/cards/{id}/images`        | ✅     | `multipart/form-data`.         |
| GET    | `/api/cards/{id}/images`        | ✅     | List a card's images.          |
| GET    | `/api/images/{id}/file`         | ✅     | Full-size bytes.               |
| GET    | `/api/images/{id}/thumbnail`    | ✅     | JPEG thumbnail, longest side 480 px. |
| PATCH  | `/api/images/{id}`              | ✅     | `side`, `caption`, `is_primary`, `sort_order`. |
| DELETE | `/api/images/{id}`              | ✅     | Removes the row and both files. |

Form fields: `files` (repeatable), `side` (`front` \| `back` \| `detail` \| `slab`, default
`front`), `caption`.

Uploads are validated by decoding them with Pillow, not by trusting the declared content type.
Limits: 25 MB, JPEG/PNG/WebP. Each side keeps its own primary image; deleting a primary promotes
the next image on that side.

---

## Condition and grade prediction

| Method | Path                                     | Status | Description                        |
| ------ | ---------------------------------------- | ------ | ---------------------------------- |
| GET    | `/api/cards/{id}/condition`              | ✅     | Current assessment. `404` if none. |
| PUT    | `/api/cards/{id}/condition`              | ✅     | Record an assessment; supersedes the previous one. |
| GET    | `/api/cards/{id}/condition/history`      | ✅     | Every assessment, newest first.    |
| GET    | `/api/cards/{id}/grade-predictions`      | ✅     | Stored predictions. `?current_only=true` for the live set. |
| POST   | `/api/cards/{id}/grade-prediction`       | ✅     | Run the probability model and persist the run. |
| PUT    | `/api/cards/{id}/grade-prediction/override` | ✅  | Set the probabilities yourself.    |

The wire format is nested even though the table is flat, because that is how a card is inspected:

```jsonc
{
  "assessor": "user",
  "centering": {
    // Border widths or percentages — only each pair's ratio is used, so
    // millimetres and percentages give the same score.
    "front": { "left": 48, "right": 52, "top": 51, "bottom": 49 },
    "back":  { "left": 47, "right": 53, "top": 50, "bottom": 50 }
  },
  "front": {
    "corner_tl": "none", "corner_tr": "none", "corner_bl": "minor", "corner_br": "none",
    "edge_condition": "none", "surface_condition": "none", "holo_condition": "none",
    "scratches": "minor", "print_lines": "none", "silvering": "none", "whitening": "none",
    "dents": "none", "dimpling": "none", "creases": "none", "staining": "none",
    "misc_defects": "none",
    "notes": null,
    "defect_notes": { "scratches": "One hairline under direct light" }
  },
  "back": { /* same 16 fields */ },
  "notes": "Pack fresh"
}
```

Severity is `none` \| `minor` \| `moderate` \| `severe` \| `unknown`. **Unanswered means unknown,
never perfect** — unanswered fields are excluded from the sub-scores and lower `completeness`.

The response adds derived scores (0–10):

```jsonc
"scores": {
  "centering": 9.2, "centering_front": 9.2, "centering_back": 10.0,
  "corners": 9.4, "edges": 10.0, "surface": 8.5,
  "overall": 9.3,          // presentational weighting only
  "completeness": 1.0      // fraction of the 40 inputs answered
}
```

Centering is scored per face against that face's own tolerance (backs are judged more leniently),
then the combined score is the weaker face. Sub-scores are arithmetic, not grading: the grade
*probability* model is Phase 2 and reads these together with the configurable rules in
`grade_rules`.

`PUT` never edits in place. A card re-examined under better light is a new opinion, and the history
is what Phase 8 calibrates against.

### Grade predictions

`POST /api/cards/{id}/grade-prediction` returns an array: one company-agnostic `physical`
prediction — what the card is — plus one `market` prediction per grading company in scope. It
supersedes the previous run but leaves any prediction you overrode alone. `400 no_assessment` if
the card has not been assessed.

`PUT …/grade-prediction/override` takes `{company_id?, probabilities, confidence, notes?}`.
Probabilities are keyed by grade string and must sum to 1 (±0.02). The model's own output is
superseded rather than deleted, so its accuracy can still be scored against the eventual grade.

The evaluation block recomputes predictions live rather than reading stored ones, so they can never
lag a reassessment. `POST` exists for history and Phase 8 calibration.

---

## `GET /api/cards/{id}/evaluation` — the decision engine

Spec section 45: *build a decision engine with a collection database attached.* Everything needed
to answer "should I grade this?" arrives in one response and the UI only visualises it.

**The shape below is fixed and does not change as later phases land.** Each block carries a
`status`; Phase 1 returns real data for `raw`, `condition` and `grading_options`, and an explicit
`not_implemented` / `insufficient_data` — with the delivering phase and a human-readable reason —
for the rest. No block ever invents a number to look complete.

`status` values: `ok` · `partial` · `not_assessed` · `insufficient_data` · `not_implemented`.

```jsonc
{
  "card_id": "9f2c…",
  "evaluated_at": "2026-08-10T21:20:11Z",
  "engine_version": "0.1.0",
  "currency": "GBP",

  "raw": {                          // ✅ Phase 1
    "status": "ok",
    "display_name": "Umbreon VMAX 215/203",
    "set_label": "Evolving Skies (EVS)",
    "number": "215/203", "variant": "Alternate Art", "language": "English",
    "quantity": 1, "currency": "GBP",
    "purchase_price": 185.0,
    "user_raw_value": 210.0,
    "market_raw_value": null,
    "best_raw_value": 210.0,        // user figure wins, market is the fallback
    "raw_value_source": "user_override",
    "net_raw_sale_value": null      // needs the selling-cost engine (P4)
  },

  "condition": {                    // ✅ Phase 1
    "status": "partial",
    "reason": "Only 50% of the assessment is filled in — the grade estimate will be wide…",
    "assessment_id": "4b8…", "assessed_at": "…", "assessor": "user",
    "completeness": 0.5,
    "scores": { "centering": 9.2, "corners": 10.0, "edges": 10.0, "surface": 8.5, "overall": 9.6 },
    "notable_defects": ["Front creases: moderate"]
  },

  "grade_prediction": {             // ✅ Phase 2
    "status": "ok",                 // not_assessed until a condition assessment exists
    "reason": null, "phase": null,
    // Headline fields describe the first company in scope.
    "company_code": "PSA", "kind": "market", "source": "rules_engine",
    "probabilities": [{ "grade": 10, "label": "PSA 10", "probability": 0.55 }],
    "likely_grade": 10, "grade_min": 9, "grade_max": 10,
    "max_grade_cap": null, "confidence": "high", "caps_applied": [],
    // The card itself, ignoring who is grading it (spec section 8).
    "physical": { "company_code": "physical", "likely_grade": 10, "probabilities": [] },
    // Every grader, because they do not grade alike and must not be merged.
    "by_company": [{
      "company_id": "…", "company_code": "PSA", "company_name": "…",
      "probabilities": [], "likely_grade": 10, "grade_min": 9, "grade_max": 10,
      "max_grade_cap": null, "confidence": "high", "caps_applied": [],
      "is_user_override": false
    }],
    "model_version": "rules-1.0",
    "base_grade": 9.6                // sub-score blend, before any rule applied
  },

  "market": {                       // ✅
    "status": "ok", "phase": null, "reason": null,
    "currency": "GBP",
    "raw": { … },                   // MarketValueRow
    "graded": [ … ],                // one MarketValueRow per grade with sales
    "computed_at": "2026-08-11T10:04:00Z",
    "sources": ["Manual entry", "CSV import"]
  },

  "liquidity": {                    // ✅ — measured across every grade
    "status": "ok", "phase": null, "score": 8.4, "band": "liquid",
    "sales_7d": 2, "sales_30d": 9, "sales_90d": 24, "sales_365d": 61,
    "days_since_last_sale": 3, "active_listings": 11, "sold_to_active_ratio": 2.18,
    "median_days_between_sales": 5.0, "sales_per_month": 5.08
  },

  "trend": {                        // ✅ — measured within ONE grade
    "status": "ok", "phase": null,
    "direction": "up", "confidence": "medium",
    "grade_label": "raw",           // which grade the direction describes
    "change_7d_pct": null, "change_30d_pct": 7.4, "change_90d_pct": 12.1,
    "change_180d_pct": null, "change_365d_pct": null, "sample_size": 24
  },

  "grading_options": {              // ✅
    "status": "ok", "phase": null, "reason": null,
    "currency": "GBP",
    "declared_value": 469.19,        // probability-weighted, not the top-grade value
    "declared_value_source": "system",
    "declared_value_confidence": "high",
    "declared_value_basis": "Probability-weighted across the PSA grades with sales data…",
    "declared_value_coverage": 1.0,
    "assumed_batch_size": 25,        // shipping belongs to the parcel, not the card
    "allocation_method": "equal",
    "allocation_note": null,
    "selling_profile_code": "ebay_uk",
    "net_values": [ … ],             // net per grade — tier-independent, computed once
    "best_case": [ … ],              // per grader: its own fee against its own slab price
    "cheapest_available_cost": 24.10,
    "options": [{
      "company_id": "…", "company_code": "CGC", "company_name": "CGC Cards",
      "base_fee": 19.0, "membership_discount": null, "grading_fee": 19.0,
      "per_card_fees": null, "declared_value_fee": null,
      "allocated_overhead": 6.10, "total_cost": 25.10,
      "shared_total": 152.30, "assumed_batch_size": 25,
      "tier_id": "…", "tier_name": "Bulk", "currency": "GBP",
      "grading_fee": 16.8, "turnaround_days": 45,
      "minimum_cards": 25, "requires_batch": true, "membership_required": false,
      "declared_value": null, "allocated_overhead": null, "total_cost": null,
      "available": true, "blockers": []
    }]
  },

  "expected_outcomes": {            // ✅ one ExpectedOutcome per usable route, best first
    "status": "ok", "phase": null, "reason": null,
    "outcomes": [ /* ExpectedOutcome */ ]
  },

  "recommendation": {               // ✅ the verdict, and the working behind it
    "status": "ok", "phase": null, "reason": null,
    "decision": "grade", "confidence": "high",
    "headline": "Grade with CGC Standard.",
    "company_code": "CGC", "tier_name": "Standard",
    "expected_profit": 460.22,      // over selling raw today, not over zero
    "expected_net": 742.84,
    "net_raw_alternative": 181.32,  // the bar grading has to clear
    "roi_pct": 454.0,               // on the grading fee, not on the card
    "probability_of_profit": 1.0,
    "probability_of_target_profit": { "25": 1.0, "50": 1.0, "100": 0.95 },
    "minimum_profitable_grade": 9.0,
    "downside": 274.94, "upside": 547.74,   // percentiles, not the worst and best grades
    "opportunity_score": 95.0,
    "score_parts": { "profitability": 10.0, "grade_probability": 10.0,
                     "liquidity": 10.0, "trend": 5.0, "risk": 10.0 },
    "grading_cost": 101.30,
    "assumed_batch_size": 25,       // the batch the quoted cost assumes (see below)
    "coverage": 1.0,                // share of the likely grades that have sales behind them
    "review_in_days": null,         // set on a Hold (§33)
    "alternative": null,            // the better-on-paper route that was not chosen (§26)
    "alternative_note": null,
    "is_user_override": false,
    "reasons": [ /* ExplanationItem */ ]
  },

  "explanation": [                  // ✅ the "Why?" panel (§30)
    { "kind": "pass", "text": "Front and back photographs on file.", "detail": null },
    { "kind": "fail", "text": "No market data for this card.", "detail": null }
  ],
  "blockers": [                     // ✅ what is missing, in order
    "Assess the card's condition.",
    "Add comparable sales for the raw card and each relevant grade."
  ],
  "data_confidence": "none"
}
```

`ExplanationItem.kind` is `pass` \| `warn` \| `fail` \| `info`.

`decision` vocabulary: `grade` · `grade_if_batch_filled` · `sell_raw` · `keep_raw` · `hold` ·
`do_not_grade` · `insufficient_data`.

A `decision_override` set on the card takes precedence and comes back with
`recommendation.is_user_override: true` — the engine explains itself, but it does not overrule you.

**`assumed_batch_size` is not always the `batch_size` you asked for.** On a
`grade_if_batch_filled` the engine re-costs the card at each tier's own minimum, so the quoted
`grading_cost` describes a fuller submission than the one requested. The field reports the batch
the numbers actually assume; reading it as the request would make the cost a lie.

**`coverage` qualifies every expected figure.** Below `1.0`, `expected_profit`, `expected_net`,
`roi_pct`, `downside` and `upside` are conditional on the card landing on a grade that has sales
behind it — grades with none are unknown, not zero, so they are left out of the expectation and
their share is reported here. `probability_of_profit` is the exception: it is **not**
renormalised, so unpriced grades count against it.

### `MarketValueRow`

```jsonc
{
  "grade_label": "PSA 10", "company_code": "PSA", "grade": 10,
  "median": 398.0, "weighted_median": 412.0,
  "low_quartile": 355.0, "high_quartile": 445.0,
  "last_sale": 420.0, "realistic_sale": 400.0, "quick_sale": 360.0,
  "sample_size": 37, "window_days": 90, "last_sale_at": "2026-08-06",
  "confidence": "high",              // never a price without its evidence (§36)
  "premium_vs_raw_pct": 118.0,
  "is_user_override": false
}
```

### `ExpectedOutcome`

One evaluated (company, tier) route. `rows` is the working behind the expectation: every grade the
card might get, including the ones with no price, which keep their probability and carry `null`
values rather than being dropped.

```jsonc
{
  "company_code": "CGC", "tier_name": "Standard",
  "grading_cost": 101.30,
  "expected_gross": null, "expected_net": 742.84, "expected_profit": 460.22,
  "roi_pct": 454.0,
  "probability_of_profit": 1.0,
  "probability_of_target_profit": { "25": 1.0, "50": 1.0, "100": 0.95 },
  "minimum_profitable_grade": 9.0,
  "probability_at_or_above_minimum": 1.0,   // over every grade, priced or not
  "downside": 274.94, "upside": 547.74,
  "liquidity_score": 10.0,                  // this grader's slabs of this card
  "opportunity_score": 95.0,
  "score_parts": { "profitability": 10.0, "grade_probability": 10.0,
                   "liquidity": 10.0, "trend": 5.0, "risk": 10.0 },
  "coverage": 1.0, "confidence": "high",
  "notes": [],                              // e.g. what the coverage leaves out
  "rows": [
    { "grade": 10, "label": "CGC 10", "probability": 0.71,
      "gross_value": 950.0, "net_value": 830.36, "profit": 547.74 },
    { "grade": 7.5, "label": "CGC 7.5", "probability": 0.05,
      "gross_value": null, "net_value": null, "profit": null }
  ]
}
```

---

## Collection

| Method | Path                        | Status | Description                       |
| ------ | --------------------------- | ------ | --------------------------------- |
| GET    | `/api/collection/summary`   | ✅     | Dashboard aggregates.             |
| GET    | `/api/collection/decisions` | ✅     | The decision engine across the collection, ranked. |
| GET    | `/api/collection/facets`    | ✅     | Distinct values present, for filter menus. |

`summary` returns totals, values, decision counts, per-set breakdown and a `readiness` array. Any
figure that cannot be calculated yet is `null` with a `values_reason` — a `0` would read as "no
upside" rather than "not calculated". Its `decisions` counts are the overrides *you* set, not the
engine's verdicts: running the engine over every card is what `/decisions` is for.

### `GET /api/collection/decisions`

Query: `batch_size` (default 1), `limit` (default 300).

Runs `evaluate_card` over every card that has both a current condition assessment and a computed
price — the rest have no decision to compute, and are counted rather than evaluated. Kept separate
from `summary` because it costs about 20 ms a card, and a dashboard that blocks for nine seconds is
a dashboard nobody waits for.

```jsonc
{
  "status": "partial",
  "reason": "1 of 6 cards were skipped: they need a condition assessment and comparable sales…",
  "currency": "GBP",
  "analysed": 5, "total_cards": 6, "skipped_not_ready": 1,
  "truncated": false, "batch_size": 20,
  "expected_profit": 637.74,        // only across cards it would actually grade
  "potential_graded_value": 1289.68,
  "potential_uplift": 753.99,
  "total_grading_cost": 116.25,
  "counts": { "grade": 3, "sell_raw": 1, "do_not_grade": 1 },
  "opportunities": [{
    "card_id": "…", "name": "Umbreon VMAX 215/203", "set_label": "Evolving Skies (EVS)",
    "decision": "grade", "headline": "Grade with CGC Economy.", "confidence": "high",
    "company_code": "CGC", "tier_name": "Economy",
    "expected_profit": 95.42, "roi_pct": 374.9, "probability_of_profit": 0.9185,
    "opportunity_score": 95.5, "grading_cost": 25.45, "net_raw_alternative": 179.21,
    "coverage": 1.0, "is_user_override": false
  }]
}
```

Money totals count **only** the cards the engine would actually grade — summing the expected profit
of cards it told you not to grade would describe a plan nobody is going to carry out. The totals are
never returned without `analysed` and `total_cards` beside them. Above `limit` analysable cards the
sweep is truncated and says so; it is never silently cut.

---

## Groups

| Method | Path                                | Status | Description              |
| ------ | ----------------------------------- | ------ | ------------------------ |
| GET    | `/api/groups`                       | ✅     | With `card_count`.       |
| POST   | `/api/groups`                       | ✅     | `kind`: `folder` \| `watchlist` \| `smart`. |
| PATCH  | `/api/groups/{id}`                  | ✅     |                          |
| DELETE | `/api/groups/{id}`                  | ✅     | Cards are not deleted.   |
| POST   | `/api/groups/{id}/cards`            | ✅     | `{"card_ids": [...]}`. Idempotent. |
| DELETE | `/api/groups/{id}/cards/{card_id}`  | ✅     |                          |

---

## Reference catalogue

| Method | Path                    | Status | Description                          |
| ------ | ----------------------- | ------ | ------------------------------------ |
| GET    | `/api/sets`             | ✅     | `q`, `language`, `limit`.            |
| POST   | `/api/sets`             | ✅     |                                      |
| PATCH  | `/api/sets/{id}`        | ✅     |                                      |
| DELETE | `/api/sets/{id}`        | ✅     | `409` if any card references it.     |
| GET    | `/api/variants`         | ✅     |                                      |
| POST   | `/api/variants`         | ✅     |                                      |
| PATCH  | `/api/variants/{id}`    | ✅     |                                      |
| DELETE | `/api/variants/{id}`    | ✅     | `409` for built-ins; deactivate instead. |

A card can be created from free text alone. If its `set_code` matches a known set the reference is
linked and the set name filled in.

---

## Grading configuration

Nothing about PSA, CGC or ACE is hard-coded. A company is a row, a tier is a row, and changing a
price is a `PATCH` the engine picks up on the next evaluation.

| Method | Path                                            | Status | Description                 |
| ------ | ----------------------------------------------- | ------ | --------------------------- |
| GET    | `/api/grading/companies`                        | ✅     | With tiers and memberships. |
| POST   | `/api/grading/companies`                        | ✅     |                             |
| PATCH  | `/api/grading/companies/{id}`                   | ✅     |                             |
| DELETE | `/api/grading/companies/{id}`                   | ✅     | `409` if submissions reference it. |
| GET    | `/api/grading/companies/{id}/tiers`             | ✅     |                             |
| POST   | `/api/grading/companies/{id}/tiers`             | ✅     |                             |
| PATCH  | `/api/grading/tiers/{id}`                       | ✅     |                             |
| DELETE | `/api/grading/tiers/{id}`                       | ✅     |                             |
| GET    | `/api/grading/companies/{id}/memberships`       | ✅     |                             |
| POST   | `/api/grading/companies/{id}/memberships`       | ✅     |                             |
| PATCH  | `/api/grading/memberships/{id}`                 | ✅     |                             |
| DELETE | `/api/grading/memberships/{id}`                 | ✅     |                             |
| GET    | `/api/grading/rules`                            | ✅     | Grade model caps and multipliers. |
| POST   | `/api/grading/rules`                            | ✅     |                             |
| PATCH  | `/api/grading/rules/{id}`                       | ✅     |                             |
| DELETE | `/api/grading/rules/{id}`                       | ✅     | `409` for built-ins; deactivate instead. |

Company fields include `strictness`: grade points that company is assumed to award above (+) or
below (-) the model's baseline. It ships at `0.0` for every company — SlabStack makes no claim
about who grades harder, and the user sets it from their own returned submissions.

Rule fields: `field` (an assessment defect field, or a group such as `corner_any`), `face`
(`front` | `back` | `any`), `min_severity`, and an effect — `max_grade` (a hard ceiling) and/or
`probability_multiplier` with `penalty_from_grade` (scales the mass at and above that grade). A
rule must have at least one effect.

Tier fields: `tier_code`, `tier_name`, `price`, `currency`, `minimum_cards`, `maximum_cards`,
`min_declared_value`, `max_declared_value`, `turnaround_days`, `membership_required`,
`membership_discount_pct`, `additional_fees` (per submission), `per_card_fees`,
`declared_value_fee_pct`, `effective_from`, `effective_to`, `active`, `source_url`,
`source_checked_at`, `notes`.

`effective_from` / `effective_to` exist so historical submissions keep costing what they actually
cost when a grader changes price.

**Pricing honesty rule.** A tier is only seeded `active` when it carries a price we can attribute.
Tiers with no verified price ship `active: false` at `price: 0` with a note, so the engine skips
them rather than costing a submission at zero. In this build CGC and ACE are priced from the
figures in the product specification; PSA ships as structure only, for you to fill in.

---

## Selling costs

| Method | Path                             | Status | Description                     |
| ------ | -------------------------------- | ------ | ------------------------------- |
| GET    | `/api/selling-profiles`          | ✅     |                                 |
| POST   | `/api/selling-profiles`          | ✅     |                                 |
| PATCH  | `/api/selling-profiles/{id}`     | ✅     | Setting `is_default` clears the others. |
| DELETE | `/api/selling-profiles/{id}`     | ✅     |                                 |

Fields: `platform_fee_pct`, `payment_fee_pct`, `payment_fixed_fee`, `listing_fee`,
`other_fee_pct`, `fees_apply_to_shipping`, `shipping_charged_to_buyer`, `shipping_cost`,
`packaging_cost`, `graded_shipping_cost`, `graded_packaging_cost`.

Graded postage is separate because a slab is heavier and normally goes tracked — a detail that
changes the answer on cards near the profit threshold.

---

## Settings

| Method | Path                          | Status | Description                         |
| ------ | ----------------------------- | ------ | ----------------------------------- |
| GET    | `/api/settings`               | ✅     | `{ values, definitions }`.          |
| PATCH  | `/api/settings`               | ✅     | `{ "values": { "key": … } }`.       |
| POST   | `/api/settings/{key}/reset`   | ✅     | Back to the shipped default.        |

`definitions` describes each key (`type`, `default`, `category`, `minimum`, `maximum`, `options`,
`description`), so the settings UI is generic and a new setting needs no client change. Only keys
you have changed are persisted; defaults live in code, so new settings appear without a migration.

Keys by category:

- **general** — `currency`, `default_selling_profile_code`, `default_grading_company_codes`
- **thresholds** — `minimum_roi_pct`, `desired_profit_margin_pct`, `minimum_absolute_profit`,
  `minimum_probability_of_profit`, `grading_value_floor`, `quick_sale_discount_pct`
- **risk** — `risk_tolerance`, `minimum_liquidity_score`, `decision_score_weights`,
  `hold_recheck_days`
- **submission** — `cost_allocation_method`, `default_submission_shipping_out`,
  `default_submission_shipping_return`, `default_submission_insurance_pct`
- **grade_model** — `grade_model_weights`, `grade_model_worst_weight`, `grade_model_base_sigma`,
  `grade_model_unknown_sigma`, `grade_model_disagreement_factor`, `grade_model_max_sigma`,
  `grade_model_min_probability`
- **market** — `market_window_days`, `recency_half_life_days`, `outlier_iqr_multiplier`,
  `min_sales_high_confidence`, `min_sales_medium_confidence`

Money-valued settings are in major units. `decision_score_weights` must total 100.

---

## Grading economics

`GET /api/cards/{id}/evaluation?batch_size=N` costs the card as though it travelled in a
submission of `N`. It defaults to **1** — the honest worst case, where one card carries the whole
postage — and it changes which tiers are usable as well as what each one costs. The assumed size
comes back on the block, so a per-card figure is never read without the batch it assumed.

A tier is returned with the reasons it cannot be used rather than filtered out:

```jsonc
{
  "company_code": "CGC", "tier_name": "Bulk",
  "grading_fee": 16.80, "total_cost": 21.70,
  "minimum_cards": 25, "requires_batch": true,
  "available": false,
  "blockers": [
    "Declared value exceeds this tier's ceiling of £400.00 — a more expensive tier is required."
  ]
}
```

An unpriced tier returns `total_cost: null` and a blocker naming what to configure — never a
total, because costing it at the shared overhead alone would read as a cheap route.

`best_case` pairs each grader's own cheapest usable tier with the best-netting grade *that grader*
has sales data for. A grader with no graded sales returns `best_net: null` and a `reason` rather
than borrowing another grader's prices.

`PATCH /api/cards/{id}` accepts `user_declared_value`. It is stored in its own column, separate
from `user_raw_value` — one is what you would sell the card for today, the other is what you
insure the slab for — and clearing it restores the engine's suggestion.

---

## Market data

| Method | Path                          | Status | Description                          |
| ------ | ----------------------------- | ------ | ------------------------------------ |
| Method | Path                                        | Status | Description                                    |
| ------ | ------------------------------------------- | ------ | ---------------------------------------------- |
| GET    | `/api/data-sources`                         | ✅     | Adapters, enablement, key presence.            |
| GET    | `/api/cards/{id}/market`                    | ✅     | Prices, liquidity and trend for one card.      |
| POST   | `/api/cards/{id}/market/recompute`          | ✅     | Re-fence outliers and reprice; writes a snapshot. |
| GET    | `/api/cards/{id}/market/sales`              | ✅     | Comparable sales, excluded ones included.      |
| POST   | `/api/cards/{id}/market/sales`              | ✅     | Record one sale.                               |
| POST   | `/api/cards/{id}/market/sales/import`       | ✅     | CSV import, deduped and filtered.              |
| POST   | `/api/cards/{id}/market/reclassify`         | ✅     | Re-run the exclusion filters.                  |
| GET    | `/api/cards/{id}/market/history`            | ✅     | Daily `price_snapshots`, one series per grade. |
| GET    | `/api/cards/{id}/market/listings`           | ✅     | Active unsold listings, grouped by grade.      |
| GET    | `/api/market/sales?catalog_key=…`           | ✅     | Sales for an identity, with no card in hand.   |
| PATCH  | `/api/market/sales/{sale_id}`               | ✅     | Correct a stored sale.                         |
| PUT    | `/api/market/sales/{sale_id}/exclusion`     | ✅     | Include or exclude by hand; outranks the system. |
| DELETE | `/api/market/sales/{sale_id}`               | ✅     | Delete a row entered in error.                 |
| GET    | `/api/market/prices?catalog_key=…`          | ✅     | Derived valuations for an identity.            |
| PUT    | `/api/market/prices/{price_id}/override`    | ✅     | Your own value, stored beside the computed one. |
| POST   | `/api/market/recompute-all`                 | ✅     | Reprice every identity in the collection.      |
| POST   | `/api/market/refresh`                       | ✅     | Fetch prices from every enabled provider.      |
| PATCH  | `/api/data-sources/{code}`                  | ✅     | Enable or configure a source.                  |
| GET    | `/api/catalog/lookup`                       | ✅     | Find a card in a provider's catalogue.         |
| POST   | `/api/cards/{id}/catalog-link`              | ✅     | Confirm a catalogue match for a card.          |
| POST   | `/api/catalog/link-all`                     | ✅     | Link every unlinked card. Dry run by default.  |

`/api/data-sources` reports `credentials` — one `{env_var, present}` per environment variable the
source reads — and never returns a value. Some sources need more than one (eBay wants a client id
*and* a secret), and reporting only the first would show a green tick beside a source that cannot
authenticate. `api_key_present` remains for the primary key. The database stores variable *names*.

**One source ships enabled**: `pokemontcg_io`, the only one that works with no account, no key and
no approval — a switch the user has to find before anything works is a worse default than an
outbound request they can turn off in one click. Everything else ships **disabled**, and
`/api/market/refresh` returns `409` until a network source is on. `manual` and `csv` are always
enabled, because they work with no network, no key and no terms of service — and they are what
everything degrades to.

### Live providers

Two have adapters, and they are different *kinds* of source. The rest are listed to show what is
planned and **cannot be enabled** — `PATCH` returns `409`, because a switch with nothing behind it
is worse than no switch.

| | `pokemontcg_io` — a catalogue | `ebay` — a marketplace |
| --- | --- | --- |
| Supplies | One aggregate price | Individual sold listings and active listings |
| Setup | Nothing. Optional key raises the limit | Developer application; two credentials |
| Identifies a card by | Its own id, stored once you confirm a match | Name — `requires_external_id: false`, nothing to link |
| Valuation | Someone's index, used only where you have no sales | A median of real sales, computed by the same engine as your own |
| Liquidity | **Unknown** — one price cannot say how often a card trades | **Measured**, from sold frequency and the sold-to-active ratio |
| Trend | Forward only, from daily snapshots | 90 days of history, then forward |
| Grading decision | **Unanswerable** — no graded prices at all | **Answerable** — slabs sell with the grade in the title |

A graded `MarketKey` returns `None` from the catalogue rather than the raw price. Answering it would
put the same number on both sides of the grading decision and make grading look exactly break-even,
every time.

**What eBay cannot do.** Sold data lives behind Marketplace Insights, which eBay grants per
application; until it is approved every sold query returns `403`. That is reported as a *note* and
not a failure — the run continues and still records active listings — because "you are not approved
for this" and "this card has not sold" need completely different things from the user. Sold data
covers 90 days. And asking prices are recorded as listings, never as sales.

**The grade comes from the seller's own listing title**, since eBay has no grade field. Titles that
name a grade the seller only *hopes* for — "PSA 10 READY", "would grade a 9" — are read as `raw`,
which is what they are. Counting them as graded sales would drag the graded price towards the raw
price and quietly invert the decision.

### Precedence

`market_prices` is unique per `(catalog_key, grade_label, source_id)`, so a fetched price and one
computed from your sales coexist. They are resolved to one row per grade, and **your own sales
win** — a median from twenty-two real sales of this card is better evidence than a third party's
index. `source_code` on a market row names where the number came from; `null` means your sales.

An empty sale-derived row does not win: one left over from sales that were all later excluded has a
zero sample and describes nothing.

### Currency

Providers quote USD and EUR; the app reports one currency. `fx_rates` is a setting keyed
`{"USD_GBP": 0.79}` and defaults to empty. **A price with no configured rate is fetched, reported,
and not written.** There is no live FX feed here and no sensible default: a wrong rate rescales
every provider price silently, and the mistake would be invisible because the numbers still look
like money. Every converted figure reports the rate it used.

### Refresh

`POST /api/market/refresh?card_id=…` returns one report per enabled source:

```jsonc
{
  "source_code": "pokemontcg_io", "source_name": "Pokémon TCG API",
  "started_at": "…", "finished_at": "…",
  "requested": 12, "updated": 9, "skipped": 3, "failed": 0,
  "status": "partial",
  "reason": "Updated 9, skipped 3.",
  "cards": [{
    "card_id": "…", "name": "Umbreon VMAX 215/203", "status": "updated",
    "value": 328.44, "currency": "GBP",
    "source_value": 410.55, "source_currency": "USD",   // both, so the number is checkable
    "fx_rate": 0.8,                                     // yours, from Settings
    "reason": null
  }],
  "notes": [ … ]
}
```

A **sales-level** source fills different fields on the same report, because it did a different
kind of work — one number per card would say nothing about a run that imported fourteen sales
across three grades:

```jsonc
{
  "source_code": "ebay", "source_name": "eBay",
  "requested": 1, "updated": 1, "skipped": 0, "failed": 0, "status": "ok",
  "sales_imported": 7, "sales_excluded": 3, "listings_seen": 3,
  "cards": [{
    "card_id": "…", "name": "Umbreon VMAX 215/203", "status": "updated",
    "sales_imported": 7, "sales_updated": 0,
    "sales_excluded": 3,              // job lots, wrong language, wrong printing — kept, reversible
    "grades": ["raw", "CGC 9.5", "PSA 10"],   // the graded ones are the point
    "listings_seen": 3,
    "listings_reported": 61,          // what eBay says exists; one page is not the market
    "value": null, "reason": null     // no aggregate price: the engine computes it from the sales
  }],
  "notes": [ … ]                      // e.g. Marketplace Insights not granted
}
```

`listings_reported` is the honest denominator for the sold-to-active ratio. Counting only the page
fetched would understate supply, and understated supply flatters liquidity — which flatters the
decision to grade.

**A failure costs future updates, never history.** Nothing is cleared before a run, so a sync that
breaks halfway leaves everything it had already written. Active listings that stop appearing are
marked inactive rather than deleted, and only after a *successful* fetch.

### Catalogue lookup

`GET /api/catalog/lookup?name=…&set_code=…&card_number=…` returns candidates and **writes nothing**.
Confirming one is a separate `POST /api/cards/{id}/catalog-link`, because a confident API silently
rewriting somebody's card is the exact failure this abstraction was shaped to prevent (spec §5).

`apply_fields` names which catalogue values to accept — anything omitted is left as it was, so a
lookup fills gaps rather than overwriting decisions. The provider's own id is stored in
`cards.external_ids`, so later syncs price that exact card instead of re-searching by name and
drifting onto a different printing.

`confidence` is for ordering candidates and nothing else.

### Linking the whole collection

`POST /api/catalog/link-all?source_code=…&dry_run=true&relink=false&limit=100` searches the source
once per unlinked card and stores the provider's id where the match is unambiguous. It exists
because per-card linking is the most tedious thing in this application — a hundred cards and two
sources is two hundred searches, each ending in a click on the obvious answer.

The tedium is not the risk, though. A wrong link is silent and lasting: every future refresh prices
a different printing, the figures stay plausible, and nothing ever says so. So this is not "link
everything" — it is **link the ones where there is nothing to decide, and hand the rest back**.
Three things are required, and all three:

1. **The card carries enough to search with.** A name alone matches a dozen Pikachus, so a card with
   no number and no set is never linked automatically however confident a provider sounds.
2. **The best candidate clears a confidence floor** (`0.7` — the name matched exactly *and* at least
   one thing pinned it down).
3. **The best is clearly ahead of the runner-up** (`0.2`). Two candidates at 0.85 are a choice, and
   a choice belongs to the user.

```jsonc
{
  "source_code": "pokemontcg_io", "source_name": "Pokémon TCG API",
  "linked": 1, "ambiguous": 1, "skipped": 2, "failed": 0,
  "dry_run": true, "status": "ok",
  "cards": [{
    "card_id": "…", "name": "Charizard 4/102", "status": "ambiguous",
    "reason": "Two candidates are close (80% and 80%). That is a choice, and a wrong one prices a
               different printing from here on.",
    "candidates": [ { "external_id": "base1-4", "name": "Charizard",
                      "set_name": "Base", "confidence": 0.8 }, … ]
  }],
  "notes": [ … ]                      // e.g. the run was capped, or N need a decision
}
```

**Dry run by default**, and the dry run is a real pass against the source that happens to write
nothing — so what you approve is what was actually found rather than an estimate of it.

**Only the link is written**, even when the match is certain. Set name, number and rarity feed
`catalog_key`, which is how sales and prices find a card at all, so accepting those in bulk could
re-key a card away from its own history. The per-card dialog offers that; this deliberately does
not.

`limit` caps one pass and **the cap is always reported** — a run that quietly covered the first
hundred of nine hundred reads exactly like one that covered everything. One failing card is recorded
and the run continues; a source with no `search` capability (`ebay` is searched by name at sync
time and has no catalogue to link to) is refused outright.

### Active listings

`GET /api/cards/{id}/market/listings` returns what is on sale right now — the supply you would be
competing with if you sold today. Fetched and stored since the eBay adapter shipped; the card page
draws it now.

**These are asking prices, not sales.** Anyone can ask anything, and an *unsold* listing is evidence
that nobody paid it. Nothing here reaches a valuation: listings exist for the sold-to-active ratio
in §17, and for the reader.

```jsonc
{
  "grade_label": "raw", "grade": null, "price": 299.00, "currency": "GBP",
  "shipping": 3.95,               // null means unstated, NOT free
  "total_ask": 302.95,            // only where postage is known — never price + a guess
  "is_auction": false,
  "ends_at": null,                // auctions only
  "seen_at": "2026-08-12T09:14:00Z",
  "listing_title": "…", "source_url": "…", "platform": "ebay"
}
```

Ordered **by grade, then by price**. A slabbed 10 and a raw copy are different markets, and one
price ladder built from both is the pooled-grade error again — sorted by price alone they interleave,
and averaging down the column describes neither.

Three fields exist because the display would otherwise mislead:

- `shipping` is `null` when the source did not state it, and the UI says "postage not stated" rather
  than showing a total. A cheap card with expensive postage is not a cheap card.
- `total_ask` is absent unless postage is known, so no total is ever a price with a guess inside it.
- `seen_at` dates the evidence. Listings end and prices get cut; a fetch from three weeks ago
  describes a shop window that has since changed, and the panel says so above the list.

The card page also separates **auctions from fixed-price listings** when it summarises a grade. A
live bid is not an asking price — it is an unfinished result that usually rises — so the "from £X"
figure and the comparison against realised prices come from fixed-price listings only, with auctions
counted beside them.

### Import

`POST /api/cards/{id}/market/sales/import` takes `{ "csv": "…", "day_first": true }`. Column names
are matched loosely (a sale date and a price are the only required ones), rows are deduplicated on
`(source_id, external_id)`, and unreadable rows are reported by line number while the rest import:

```jsonc
{
  "imported": 41, "updated": 3, "skipped": 0,
  "excluded": 6, "outliers_flagged": 1,
  "exclusions": { "lot_or_bundle": 4, "wrong_language": 1, "damaged": 1 },
  "errors": [{ "line_number": 58, "message": "Could not read the price.", "values": { … } }],
  "prices": [ … ]                  // the card is repriced in the same request
}
```

### Exclusions

An excluded sale keeps its row, its `exclusion_reason` and `excluded_by` (`system` or `user`), and
is returned by default. The filters are heuristics over listing titles, so they are wrong sometimes
and every decision is one request from being reversed. A user's decision outranks the system's and
survives re-imports and reclassification.

`exclusion_reason`: `lot_or_bundle` · `damaged` · `wrong_card` · `wrong_language` · `wrong_variant` ·
`wrong_grade` · `price_outlier` · `suspected_fake` · `best_offer_unknown` · `user_excluded`.

---

## Submissions and analytics

| Method | Path                                            | Status | Description                   |
| ------ | ----------------------------------------------- | ------ | ----------------------------- |
| GET    | `/api/submissions`                              | ✅     | Every submission, fully costed. |
| POST   | `/api/submissions`                              | ✅     | Start a parcel.               |
| GET    | `/api/submissions/{id}`                         | ✅     | One submission, costed.       |
| PATCH  | `/api/submissions/{id}`                         | ✅     | Rename, re-tier, set postage, move the lifecycle on. |
| DELETE | `/api/submissions/{id}`                         | ✅     | Draft or cancelled only.      |
| POST   | `/api/submissions/{id}/cards`                   | ✅     | Add cards.                    |
| PATCH  | `/api/submissions/{id}/cards/{lineId}`          | ✅     | Declared value, tier, actual grade, cert number. |
| DELETE | `/api/submissions/{id}/cards/{lineId}`          | ✅     | Remove a card.                |
| POST   | `/api/submissions/optimise`                     | ✅     | Pack the collection into batches that still pay once packed. |
| GET    | `/api/analytics/opportunities`                  | ✅     | Ranked grading opportunities. |
| GET    | `/api/analytics/selling-queue`                  | ✅     | Cards to sell raw, with a price to ask. |
| GET    | `/api/analytics/assessment-queue`               | ✅     | Which unassessed cards are worth the five minutes. |
| GET    | `/api/analytics/realised`                       | ✅     | What you actually made, against what was predicted. |
| GET    | `/api/analytics/submission-returns`             | ✅     | Predicted grades against the ones that came back. |
| GET    | `/api/analytics/filters`                        | ✅     | The saved cuts on offer.      |
| GET    | `/api/analytics/filters/{key}`                  | ✅     | Apply one saved cut.          |
| GET    | `/api/analytics/accuracy`                       | ✅     | Predicted vs actual grades, marked. |
| GET    | `/api/calibration`                              | ✅     | What those results taught the model. |

### Costing a submission

Every submission response is fully costed — the list included, because the cost of a parcel is the
reason to open it. Three things differ from the single-card costing in `evaluate_card`:

**Insurance comes from the real declared values.** Not one card's value multiplied by the batch.

**Allocation can be value-weighted.** `cost_allocation_method` is `equal` or `value_weighted`;
either way the shares sum back to `shared_pot` exactly. Value-weighted falls back to equal, with
`allocation_note` saying so, when no card in the parcel has a declared value.

**Tier minimums count cards at that tier**, not cards in the parcel. `tiers[]` groups the lines and
reports `short_by` per tier; shipping is still shared across the whole parcel regardless of tier,
which is what makes a mixed submission worth building.

`cost_per_card` is an average, and `null` with no cards — never `0`.

A submission that breaks a tier's rules is **still costed and still returned**, with every
violation in `blockers`. You are allowed to build a parcel over several sittings.

Once a submission is `shipped` or later, its cards are frozen: adding, removing or reordering
returns `409`. What you sent is a record, and Phase 8 compares predicted grades against the
`actual_grade` recorded on each line. Deleting is refused for the same reason — cancel it instead.

### Submission object

```jsonc
{
  "id": "6bb4…",
  "reference": "SUB-2026-08-001",   // what you write on the parcel
  "name": "January bulk", "status": "draft", "currency": "GBP",
  "company_id": "…", "company_code": "CGC", "company_name": "CGC Trading Cards",
  "tier_id": "…",                   // the parcel's default tier; a line may override it
  "card_count": 9,
  "declared_value_total": 3803.07,

  // The shared pot, and where it came from.
  "shipping_out": 20.0, "shipping_return": 20.0,
  "insurance": 38.03,               // charged on the parcel's real declared values
  "handling": 0.0, "other_fees": 0.0,
  "tier_additional_fees": 0.0,      // charged once per tier used, not per card
  "shared_pot": 78.03,

  "grading_fees": 151.20, "per_card_fees": 0.0, "declared_value_fees": 0.0,
  "membership_discount": 0.0,
  "total_cost": 229.23,
  "cost_per_card": 25.47,           // an average, and null with no cards

  "allocation_method": "value_weighted",
  "allocation_note": "Shared costs are split by declared value…",
  "membership_code": null,

  "submitted_at": null, "received_at": null, "returned_at": null,
  "tracking_outbound": null, "tracking_return": null, "notes": null,

  "tiers": [{
    "tier_id": "…", "tier_name": "Bulk", "company_code": "CGC",
    "card_count": 9, "minimum_cards": 25, "maximum_cards": null,
    "short_by": 16, "over_by": 0,
    "blockers": ["CGC Bulk needs 25 cards at that tier; this submission has 9…"]
  }],
  "cards": [ /* CardLine */ ],
  "blockers": [ … ],                // what would stop you sending it as it stands
  "warnings": [ … ]                 // worth knowing, but not blocking
}
```

### CardLine

```jsonc
{
  "submission_card_id": "…",        // the line, not the card — the id you PATCH
  "card_id": "…",
  "name": "Umbreon VMAX 215/203", "set_label": "Evolving Skies (EVS)",
  "tier_id": "…", "tier_name": "Bulk",
  "declared_value": 807.90,
  "declared_value_source": "system",   // "user" once you set one
  "declared_value_confidence": "high",
  "base_fee": 16.80, "membership_discount": null,
  "grading_fee": 16.80, "per_card_fees": null, "declared_value_fee": null,
  "allocated_overhead": 8.08,       // this card's share of the pot
  "total_cost": 24.88,
  "allocation_weight": 80790,       // 1 under an equal split; the declared value when weighted
  "predicted_grade": null,
  "actual_grade": null,             // recorded when the parcel comes back; Phase 8 learns from it
  "status": "planned",
  "sort_order": 0,
  "blockers": ["Declared value £807.90 exceeds CGC Bulk's ceiling of £400.00…"]
}
```

### `POST /api/submissions/optimise`

Query: `limit` (default 150).

Three passes. It **routes** every card with an assessment and a price at a batch big enough for the
bulk tiers to be visible, **packs** by (company, tier), then **re-costs every card at the size its
batch actually came out at**. That last pass is the point: grading costs depend on the batch, so a
card that clears your bar at twenty-five may not at six.

```jsonc
{
  "status": "partial",
  "reason": "1 proposed batch(es) are short of their tier's minimum…",
  "currency": "GBP",
  "analysable": 14, "worth_grading": 11, "placed": 11, "total_cards": 15,
  "truncated": false,
  "routed_at_batch_size": 25,   // routing at 1 would hide the bulk tiers entirely
  "expected_profit": 1982.28,
  "total_grading_cost": 384.40,
  "batches": [{
    "company_id": "…", "company_code": "CGC",
    "tier_id": "…", "tier_name": "Bulk",
    "effective_tier_name": "Economy",   // where they land at the current count
    "card_count": 9, "minimum_cards": 25, "short_by": 16,
    "expected_profit": 1459.88, "grading_cost": 224.41,
    "expected_profit_if_filled": 1502.09,
    "viable": false,
    "reason": "CGC Bulk needs 25 cards and this batch has 9. As it stands these would be graded
               at Economy. Adding 16 more card(s) at this tier is worth £42.21…",
    "cards": [{
      "card_id": "…", "name": "Umbreon VMAX 215/203",
      "tier_name": "Economy",
      "decision_when_routed": "grade", "decision_in_batch": "grade",
      "expected_profit": 505.26, "grading_cost": 24.89,
      "still_pays": true, "reason": null,
      "cheaper_tier_name": null, "cheaper_tier_saving": null
    }]
  }],
  "stopped_paying": [ /* PlacedCard, with the reason and the number that changed */ ],
  "unplaced": [ /* worth grading, but no batch could take it */ ],
  "notes": [ … ]
}
```

**`tier_name` and `effective_tier_name` are different questions.** The first is what these cards
were routed to and what they would be graded at once the batch is full; the second is what they
would be graded at *today*. While a batch is short they differ, and quoting the routed tier's name
against the effective tier's price would describe a route that does not exist at that size.

**`stopped_paying` is the honest part.** Cards worth grading in a full batch and not in the batch
they landed in are listed with the number that changed, and are excluded from every total. They are
never silently shipped and never silently dropped.

### Analytics

Everything under `/api/analytics` is a **projection of an answer another engine already gave**. No
verdict, value or cost originates here. If a number appears below it can be traced to the card page
showing the same figure, and the two cannot drift apart because there is only one of them.

**`GET /api/analytics/opportunities`** — query `batch_size` (default 1), `limit`.

The same verdicts as `/api/collection/decisions`, cut to `grade` and `grade_if_batch_filled` and
ranked by `opportunity_score`. `batch_size` changes the list: a card that does not pay alone often
pays in a submission of twenty.

**`GET /api/analytics/realised`** — query `limit`.

Closes the loop. `prediction_results` has scored *grade* predictions since Phase 8, so the app could
report that it called a PSA 9 correctly while having no idea whether the submission made money.

```jsonc
{
  "sold": 3, "scored": 2,
  "total_net_proceeds": 1020.08,
  "total_realised_profit": 681.72,   // only across the ones with every cost known
  "total_grading_gain": 440.00,
  "items": [{
    "name": "Umbreon VMAX 215/203", "sold_on": "2026-08-12", "grade_label": "CGC 10",
    "net_proceeds": 800.00, "purchase_price": 120.00, "grading_cost": 60.00,
    "realised_profit": 620.00, "profit_is_complete": true,
    "market_value_on_the_day": 900.00, "vs_market_pct": 0.0,
    "raw_value_on_the_day": 300.00, "grading_gain": 440.00
  }]
}
```

Three rules:

- **A profit missing a cost is not reported as a profit.** A sale with no recorded purchase price,
  or a graded sale with no recorded grading cost, is counted in the proceeds and left out of the
  profit, with `profit_is_complete: false` and a reason naming what is absent. A total that silently
  dropped a cost would be wrong in the flattering direction.
- **Scored against the day, not against today.** `market_value_on_the_day` reads `price_snapshots`
  at the sale date. Today's price has moved for reasons that have nothing to do with the decision
  being judged.
- **`vs_market_pct` compares gross against gross** — the sale price against the market price, never
  the net payout against a market valuation. Measuring the payout against a sale price reports the
  fee load as though it were selling badly, and every sale would read about a tenth under the market.

`grading_gain` answers "was grading worth it" with two figures that both actually happened: what the
slab netted, less what the same card was worth raw on the same day, less what grading cost.

**`GET /api/analytics/assessment-queue`** — query `batch_size`, `limit`.

Importing four hundred cards takes a second; assessing four hundred does not. The decision engine
cannot rank them — it needs an assessment before it says anything at all — so this ranks on the one
thing already known about every card: what the market pays for it raw, and what it pays for the same
card in a slab.

The measure is a **ceiling**, not a forecast: the best-netting grade that has sales behind it, less
what the card already nets raw, less what grading costs. That is the most grading could possibly
add, and a real assessment can only bring it down.

```jsonc
{
  "status": "ok",
  "reason": "4 of 7 unassessed card(s) could gain from grading. 1 cannot, whatever condition…",
  "currency": "GBP", "analysed": 7, "total_cards": 7,
  "worth_assessing": 4, "ruled_out": 1, "unknown": 2,
  "total_ceiling": 912.50,
  "items": [{
    "card_id": "…", "name": "Umbreon VMAX 215/203",
    "verdict": "assess",                  // `assess` | `skip` | `unknown`
    "ceiling": 499.45,
    "ceiling_is_complete": true,          // false ⇒ the best *priced* grade is not the top grade
    "company_code": "CGC", "tier_name": "Economy", "grading_cost": 24.60,
    "best_grade_label": "CGC 10", "best_net": 786.36, "net_raw_value": 263.16,
    "reason": "At best — a CGC 10 — grading adds about 499.45 over selling it raw…"
  }],
  "notes": [ … ]
}
```

Three rules decide the verdict:

- **`assess`** — the ceiling clears `minimum_absolute_profit`, the user's own bar. Not a second
  definition of "worth it" invented here; the same setting the decision engine uses.
- **`skip`** — the ceiling is below that bar **and** `ceiling_is_complete`. Settled without looking
  at the card: its best possible outcome already fails the test. The reason distinguishes the two
  ways that happens — losing money outright, or making some but not enough.
- **`unknown`** — everything else, and deliberately never `skip`. If the best grade with sales
  behind it is a 9, a 10 might pay well and a negative ceiling proves nothing. A card with no graded
  sales at all lands here too, with the missing piece named: no sales stored (sync a source) or no
  priced tier configured (Settings → Grading), because those are different work.

`batch_size` matters as much here as anywhere else: shipping belongs to the parcel, so a ceiling
costed at one card is the honest worst case and a fuller batch raises every one of them.

Only cards that are **priced but not yet assessed** are ranked — the exact complement of the
population `/api/collection/decisions` analyses, so no card appears in both lists.

**`GET /api/analytics/selling-queue`** — query `limit`.

The decision engine says "sell raw" and stops; this answers what to ask for it.

```jsonc
{
  "status": "partial",
  "reason": "1 card(s) have no raw sales to price against…",
  "currency": "GBP", "analysed": 3, "total_cards": 5,
  "total_net_proceeds": 285.20,
  "items": [{
    "card_id": "…", "name": "Sylveon VMAX 211/203", "set_label": "Evolving Skies (EVS)",
    "decision": "sell_raw",
    "realistic_sale": 126.00,      // what the market says it fetches
    "net_proceeds": 110.04,        // what you keep — the same figure the card page shows
    "suggested_listing": 132.30,   // what to ask, which is not what it fetches
    "listing_basis": "5% above the realistic sale price. It trades often, so it does not need room.",
    "liquidity_score": 9.9, "liquidity_band": "very_liquid",
    "days_since_last_sale": 0, "trend_direction": "down",
    "confidence": "high",
    "purchase_price": null,
    "gain_vs_purchase": null,      // null when you never recorded what you paid — not zero
    "blockers": []
  }],
  "notes": [ … ]
}
```

The markup over the realistic price scales with liquidity — 5% when it trades often, 18% when it
trades rarely and needs room to be haggled down — and is capped at the upper quartile of actual
sales, because asking more than anyone has recently paid is how a listing sits unsold. The cap
never pushes the suggestion *below* the realistic price. A card with no raw sales gets **no**
suggested price and says why in `blockers`.

Membership is by verdict: `sell_raw`, `keep_raw` and `do_not_grade`. A card the engine returned
`insufficient_data` for is **not** here — "sell it raw" is a conclusion, and it cannot be reached
until the graded outcome has a price to lose against.

**`GET /api/analytics/submission-returns`** — no parameters.

**A sale that happened beats a price that might.** Where a card in the parcel has a recorded
disposal, its value is what it actually fetched and `value_basis` is `realised`; otherwise it is
today's price for that grade and the basis is `market`. Before disposals existed every returned slab
was valued at the current market, which made a parcel's ROI drift with the market long after the
position was closed.

The grading cost still comes from the **submission line**, not from the sale record's own
`grading_cost` — this is the parcel's return, and taking both would charge the fee twice.

`realised_count` and `status_note` say which kind of number a return is made of. A parcel scored
entirely on sales is a result; one scored on current prices is a projection; a mixture is neither,
and that is the case worth naming.

```jsonc
{
  "status": "partial",
  "reason": "Scored 1 returned submission(s); 2 still out and not counted in any total.",
  "currency": "GBP",
  "scored": 1, "awaiting": 2,
  "total_cost": 24.88, "total_profit": 620.32, "roi_pct": 2493.9,
  "submissions": [{
    "submission_id": "…", "reference": "SUB-2026-08-001", "company_code": "CGC",
    "status": "returned", "returned_at": "2026-08-02T10:14:00",
    "card_count": 1, "graded_count": 1,
    "total_cost": 24.88, "total_value": 645.20,
    "total_profit": 620.32, "roi_pct": 2493.9,
    "mean_surprise": 0.5,          // positive: the grader was kinder than the model expected
    "realised_count": 1,           // how many rest on money that actually arrived
    "cards": [{
      "card_id": "…", "name": "Umbreon VMAX 215/203",
      "predicted_grade": 9.5, "actual_grade": 10, "surprise": 0.5,
      "cost": 24.88,
      "graded_value": 900.00,      // what it sold for, or that grade's price, or null
      "net_if_sold": 645.20, "profit": 620.32,
      "value_basis": "realised",   // `realised` | `market` | null
      "sold_on": "2026-08-11",
      "blockers": []
    }],
    "status_note": "Every card here has sold, so this return is money, not an estimate."
  }]
}
```

**Open submissions are counted, not scored.** A parcel still at the grader has no grades to compare,
and averaging it in at zero would make every open submission look like a loss. It appears with
`status_note` and is excluded from `total_cost`, `total_profit` and `roi_pct`.

**A grade with no sales behind it cannot be valued.** `graded_value` is `null` with a blocker saying
so, rather than falling back to a neighbouring grade's price.

**`GET /api/analytics/filters`** → `[{ "key", "label", "description" }]`.

**`GET /api/analytics/filters/{key}`** — query `batch_size`, `limit`. Unknown keys `404` and list
the valid ones.

`grade_now` · `grade_if_batch_filled` · `sell_raw` · `hold` · `high_upside` · `high_risk` ·
`low_liquidity` · `declining` · `needs_data`

Each filter is a predicate over figures the decision and market engines already produced — never a
fresh definition of the same idea. `low_liquidity` uses the `minimum_liquidity_score` you
configured, so the filter and the verdicts agree by construction; `declining` reads the trend
block's direction.

Where a figure is unknown the card does **not** match — an unknown risk is not a low risk, and a
card with no trend behind it is not a falling one. Those cards are counted in `unclassified` so a
short list is never mistaken for a complete one.

```jsonc
{
  "key": "grade_now", "label": "Grade now",
  "description": "Clears your bar on its own today.",
  "status": "partial",
  "reason": "3 card(s) could not be decided, so they were not tested against this filter.",
  "currency": "GBP",
  "matched": 4, "analysed": 12, "total_cards": 15, "unclassified": 3,
  "card_ids": [ … ],
  "items": [ /* the same Opportunity shape as /api/collection/decisions */ ]
}
```

### Learning from what came back

The only part of this API that reasons backwards. Everything else goes condition → distribution →
value → verdict; this takes grades that actually came back and asks whether the model that predicted
them was any good.

**What gets marked is the prediction that was actually held.** `submission_cards` freezes both
`predicted_grade` and `predicted_probabilities` when a card joins a parcel. Scoring a distribution
recomputed after the grade is known would mark the model against an outcome it has already seen,
which measures nothing and flatters it enormously. A card sent with no assessment has no frozen
prediction, cannot be marked, and is counted in `awaiting` rather than dropped.

**`GET /api/analytics/accuracy`** — query `limit` (default 500).

```jsonc
{
  "status": "partial",
  "reason": "1 graded card(s) had no prediction recorded when they were sent…",
  "scored": 15, "awaiting": 1, "minimum_sample": 10,
  "companies": [{
    "company_id": "…", "company_code": "CGC", "company_name": "CGC Cards",
    "scored": 12,
    "exact_pct": 0.0, "within_half_pct": 16.7, "within_one_pct": 75.0,
    "mean_error": -1.083,        // the bias, signed: negative = comes back worse
    "mean_absolute_error": 1.083,
    "error_stdev": 0.417,
    "mean_brier": 1.3743,        // marks the whole distribution, not the mode
    "bands": [{                  // the calibration curve
      "grade": 10,
      "predicted_count": 8.19, "actual_count": 0,
      "predicted_rate": 0.6825, "actual_rate": 0.0,
      "gap_pct": -68.3           // negative: predicted far more often than it happens
    }],
    "headline": "Your predicted CGC 10 rate runs 68 points above your actual rate.",
    "status": "ok", "reason": null
  }],
  "results": [{
    "card_id": "…", "name": "Umbreon VMAX 215/203", "company_code": "CGC",
    "predicted_grade": 10, "actual_grade": 9,
    "surprise": -1,              // positive means it graded better than predicted
    "brier": 1.31,
    "graded_at": "2026-07-22"
  }]
}
```

A **Brier score** marks the probability vector against reality — 1 on the grade that happened, 0
elsewhere. Being 95% sure of a 10 and being 40% sure of a 10 are very different claims with the same
mode, and only this notices when the confident one is wrong. A grade the model gave *zero*
probability scores as a maximal miss rather than being skipped.

**`GET /api/calibration`** — no parameters.

```jsonc
{
  "enabled": true, "minimum_sample": 10, "max_offset": 1.0,
  "companies": [{
    "company_id": "…", "company_code": "CGC",
    "sample_size": 12, "minimum_sample": 10,
    "grade_offset": -1.0,        // grades added to the model's centre
    "spread_multiplier": 1.0,    // above 1.0 the model was over-confident
    "applied": true,
    "confidence": "low",
    "reason": "Learned from 12 CGC result(s): the centre moves down by 1.00 grades."
  }]
}
```

The correction lands on the two parameters the model already has — where the distribution is centred
and how wide it is — rather than a new mechanism. That keeps it inspectable: a calibrated prediction
is the same model with a shifted centre, and the shift is a number you can read.

**Three restraints, and they matter more than the arithmetic.**

*Measured from the first result, applied only past `minimum_sample`.* Below it, `applied` is `false`
and the offset is still reported. A bias fitted to four slabs is fitted to noise, and silently
correcting for noise makes the model worse without saying so.

*Clamped at `calibration_max_offset`.* A measured two-grade bias is far likelier to be a run of odd
cards than a real one.

*Never pooled across graders.* PSA's bias is not CGC's, and a correction learned across both
describes neither.

The spread multiplier is never allowed below 1.0: claiming *more* precision than the rules engine
does, on the strength of a few dozen cards, is exactly backwards.

**Kept apart from `strictness`.** That per-company setting is an opinion you hold about a grader;
this is an observation measured from your results. Merging them would lose the ability to tell them
apart — and to switch one off.

### Calibration on a card

`grade_prediction.by_company[]` carries the raw model beside the corrected one, because a silent
adjustment is untrustworthy: you could not tell whether a prediction moved because the card differs
or because the model learned something.

```jsonc
{
  "company_code": "CGC",
  "likely_grade": 9,                    // what you are being told now
  "source": "calibrated",               // or "rules_engine" / "user_override"
  "uncalibrated_likely_grade": 10,      // what the model said before your history
  "uncalibrated_probabilities": [ … ],  // the raw distribution, not just its mode
  "calibration_offset": -1.0,
  "calibration_sample_size": 12,
  "calibration_note": "Learned from 12 CGC result(s): the centre moves down by 1.00 grades."
}
```

`calibration_note` is present whether or not a correction was applied — "3 of the 10 results needed
before a correction is applied" is worth reading on the card itself. `calibration_offset` is `null`
when nothing was applied, and `source` stays `rules_engine`.

A **user override still wins.** Learned or not, the model does not overrule a number the user typed.

---

## Client conventions worth keeping

1. **Read `status` before reading values.** A block with `status: "insufficient_data"` has `null`
   figures on purpose; rendering them as `0` or `—%` misrepresents the data.
2. **Show `reason` and `blockers`.** They are written to be shown to a user, not logged.
3. **Never hard-code a vocabulary.** Use `/api/meta/enums`.
4. **Never hard-code a fee.** Read tiers and selling profiles from the API.
