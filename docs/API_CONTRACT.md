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
| GET    | `/api/cards/{id}`             | ✅     | One card, with its images.             |
| PATCH  | `/api/cards/{id}`             | ✅     | Sparse update.                         |
| DELETE | `/api/cards/{id}`             | ✅     | Delete the card, its images and its assessments. |
| POST   | `/api/cards/{id}/split`       | ✅     | Split a stack into one row per physical copy. |
| GET    | `/api/cards/{id}/evaluation`  | ✅     | **The decision-engine call.** See below. |
| POST   | `/api/cards/identify`         | ⏳ P3  | Suggest an identity from images.        |

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

  "market": {                       // ⏳ Phase 3
    "status": "insufficient_data", "phase": 3,
    "reason": "No market data for this card yet. Add sales manually or enable a data source.",
    "currency": "GBP",
    "raw": null,                    // MarketValueRow
    "graded": [],                   // one MarketValueRow per grade with enough sales
    "computed_at": null, "sources": []
  },

  "liquidity": {                    // ⏳ Phase 3
    "status": "insufficient_data", "phase": 3, "score": null, "band": "unknown",
    "sales_7d": null, "sales_30d": null, "sales_90d": null, "sales_365d": null,
    "days_since_last_sale": null, "active_listings": null, "sold_to_active_ratio": null,
    "median_days_between_sales": null, "sales_per_month": null
  },

  "trend": {                        // ⏳ Phase 3
    "status": "insufficient_data", "phase": 3,
    "direction": "insufficient_data", "confidence": "none",
    "change_7d_pct": null, "change_30d_pct": null, "change_90d_pct": null,
    "change_180d_pct": null, "change_365d_pct": null, "sample_size": 0
  },

  "grading_options": {              // ✅ availability now, ⏳ costing in Phase 4
    "status": "partial", "phase": 4, "reason": "Tier availability only…",
    "options": [{
      "company_id": "…", "company_code": "CGC", "company_name": "CGC Cards",
      "tier_id": "…", "tier_name": "Bulk", "currency": "GBP",
      "grading_fee": 16.8, "turnaround_days": 45,
      "minimum_cards": 25, "requires_batch": true, "membership_required": false,
      "declared_value": null, "allocated_overhead": null, "total_cost": null,
      "available": true, "blockers": []
    }]
  },

  "expected_outcomes": {            // ⏳ Phase 5
    "status": "not_implemented", "phase": 5, "outcomes": []
  },

  "recommendation": {               // ⏳ Phase 5 (user overrides work now)
    "status": "insufficient_data", "phase": 5,
    "decision": "insufficient_data", "confidence": "none",
    "headline": "Not enough data to recommend a decision yet.",
    "company_code": null, "tier_name": null,
    "expected_profit": null, "roi_pct": null, "probability_of_profit": null,
    "minimum_profitable_grade": null, "opportunity_score": null,
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

### `MarketValueRow` (Phase 3)

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

### `ExpectedOutcome` (Phase 5)

```jsonc
{
  "company_code": "PSA", "tier_name": "Value",
  "expected_gross": 372.0, "expected_net": 294.0, "expected_profit": 84.0,
  "roi_pct": 112.0,
  "probability_of_profit": 0.91,
  "probability_of_target_profit": { "25": 0.84, "50": 0.71, "100": 0.42 },
  "minimum_profitable_grade": 9,
  "downside": -46.0, "upside": 214.0,
  "liquidity_score": 8.9, "opportunity_score": 87,
  "rows": [{ "grade": 10, "label": "PSA 10", "probability": 0.58,
             "gross_value": 500.0, "net_value": 431.0, "profit": 221.0 }]
}
```

---

## Collection

| Method | Path                        | Status | Description                       |
| ------ | --------------------------- | ------ | --------------------------------- |
| GET    | `/api/collection/summary`   | ✅     | Dashboard aggregates.             |
| GET    | `/api/collection/facets`    | ✅     | Distinct values present, for filter menus. |

`summary` returns totals, values, decision counts, per-set breakdown and a `readiness` array. Any
figure that cannot be calculated yet is `null` with a `values_reason` — a `0` would read as "no
upside" rather than "not calculated".

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

## Market data

| Method | Path                          | Status | Description                          |
| ------ | ----------------------------- | ------ | ------------------------------------ |
| GET    | `/api/data-sources`           | ✅     | Adapters, enablement, key presence.  |
| GET    | `/api/market/prices`          | ⏳ P3  | Derived valuations for a card.       |
| POST   | `/api/market/sales/import`    | ⏳ P3  | CSV / provider import.               |
| POST   | `/api/market/refresh`         | ⏳ P3  | Refresh from enabled providers.      |

`/api/data-sources` reports `api_key_present` as a boolean and never returns a key value. Keys are
read from named environment variables; the database stores only the variable's name.

Every network provider ships disabled. `manual` and `csv` are enabled, because they work with no
network, no key and no terms of service — and they are what the application degrades to.

---

## Submissions and analytics

| Method | Path                                | Status | Description                   |
| ------ | ----------------------------------- | ------ | ----------------------------- |
| GET    | `/api/submissions`                  | ⏳ P6  | Grading submissions.          |
| POST   | `/api/submissions/optimise`         | ⏳ P6  | Batch the collection against tier minimums. |
| GET    | `/api/analytics/opportunities`      | ⏳ P7  | Ranked grading opportunities. |
| GET    | `/api/analytics/accuracy`           | ⏳ P8  | Predicted vs actual grades.   |

The tables behind these (`grading_submissions`, `submission_cards`, `prediction_results`,
`price_snapshots`) exist now, and penny-exact shared-cost allocation is implemented and tested in
`app.money.allocate`, so Phase 6 is a planner over data that is already modelled.

---

## Client conventions worth keeping

1. **Read `status` before reading values.** A block with `status: "insufficient_data"` has `null`
   figures on purpose; rendering them as `0` or `—%` misrepresents the data.
2. **Show `reason` and `blockers`.** They are written to be shown to a user, not logged.
3. **Never hard-code a vocabulary.** Use `/api/meta/enums`.
4. **Never hard-code a fee.** Read tiers and selling profiles from the API.
