# SlabStack

A local-first grading and ROI decision engine for Pokémon cards.

Not a collection tracker with grading bolted on: a decision engine with a collection database
attached. It answers one question per card — *should I grade this, sell it raw, or hold it?* — by
optimising **expected, risk-adjusted, realisable profit after grading, selling, submission and
liquidity costs**, not theoretical card value.

Everything runs on your machine. One SQLite file, one media directory, no accounts, no cloud, and
no outbound network call in this build.

---

## Status: Phases 1–2

| Delivered                                                                     |
| ----------------------------------------------------------------------------- |
| Full database schema for all eight phases (22 tables) with Alembic migrations |
| API contract for the whole surface — later phases registered as `501`s that name their phase |
| Card CRUD, free-text search, filters, sorting, pagination, bulk add, per-copy split |
| Front/back image upload with content validation and thumbnails                |
| Structured condition assessment (16 defects × 2 faces + centering) with derived sub-scores |
| **Grade probability model** — a distribution per grading company, with configurable defect rules |
| Configuration-driven grading companies, tiers, memberships and selling profiles |
| `evaluate_card` — the decision envelope the whole UI renders                  |
| React dashboard, collection view, card detail page and settings               |
| 200 backend tests, contract guards, clean lint, verified end-to-end in a browser |

Blocks that need later engines report `not_implemented` or `insufficient_data` with the phase that
delivers them and a reason you can act on. **Nothing returns an invented number.** See
[`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Running it

Two processes: the API on `127.0.0.1:8000` and the UI on `127.0.0.1:5173`.

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

First start creates `backend/data/slabstack.db`, applies the schema and seeds reference data.

- API docs: <http://127.0.0.1:8000/api/docs>
- Health: <http://127.0.0.1:8000/api/health>

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to the backend, so the browser stays on one
origin and no API URL is baked into the build.

### Tests

```bash
cd backend && pytest && ruff check .
cd frontend && npm run typecheck && npm run build
```

---

## Running it for real (one command)

The two-process setup above is for development. For everyday use, build the UI
once and let the API serve it — one process, one port:

```bash
docker compose up --build      # http://localhost:8000
```

Your collection lives in `./data` on the host: `slabstack.db` plus every uploaded
image. Copying that directory is a complete backup, and rebuilding the container
never touches it.

Without Docker, the same thing directly:

```bash
cd frontend && npm run build
cd ../backend && SLABSTACK_STATIC_DIR=../frontend/dist uvicorn app.main:app --port 8000
```

The API serves the built UI whenever a build exists, and falls back to pointing at
the Vite dev server when one does not, so the same command works either way.

**Compose binds to `127.0.0.1` deliberately.** SlabStack has no authentication —
it is a single-user local application. Changing the port mapping to `8000:8000`
puts your collection on your network with nothing in front of it.

---

## The demo

A browser-only build is published to GitHub Pages so the UI can be looked at
without installing anything:

**<https://bjdealey.github.io/SlabStack/>**

GitHub Pages serves static files, so there is no API and no database behind it.
The demo answers its own requests in the tab against a sample collection —
nothing is uploaded, and a refresh starts over. The card images there are
generated placeholders, not card scans.

```bash
cd frontend && npm run build:demo    # same build the Pages workflow runs
```

`.github/workflows/pages.yml` builds it and pushes the output to the `gh-pages`
branch on every change to `frontend/`. That branch is build output — never edit
it by hand.

It publishes by pushing a branch rather than using `actions/deploy-pages`,
because that action needs the Pages source set to "GitHub Actions" and changing
that source needs admin rights the workflow's `GITHUB_TOKEN` does not have. A
`gh-pages` branch enables Pages on its own and needs only `contents: write`.

The demo code lives in `src/lib/demo/` and is the only place engine logic is
duplicated. `VITE_DEMO` is a compile-time constant, so a normal build drops the
branch and never bundles it.

---

## Configuration

Environment variables, all prefixed `SLABSTACK_` (see `.env.example`):

| Variable                | Default              | Purpose                                   |
| ----------------------- | -------------------- | ----------------------------------------- |
| `SLABSTACK_DATA_DIR`    | `backend/data`       | Database and media. Back this up.         |
| `SLABSTACK_DATABASE_URL`| derived              | Override the SQLAlchemy URL.              |
| `SLABSTACK_PORT`        | `8000`               |                                           |
| `SLABSTACK_MAX_IMAGE_BYTES` | `26214400`       | Upload limit.                             |

Everything the decision engine assumes — fee rates, ROI thresholds, risk tolerance, score weights —
lives in the database and is editable in **Settings**, not in code.

Market-data API keys are read from named environment variables at call time. The database stores
the *name* of the variable, never a key.

---

## Architecture

```
React + TypeScript (Vite, Tailwind, TanStack Query)
  │  /api
FastAPI
  ├── cards, images, condition, groups        (collection)
  ├── grading companies / tiers / memberships (configuration)
  ├── selling profiles, settings              (economics)
  ├── evaluation  ← the decision engine
  └── market data providers                   (Phase 3, behind an interface)
        │
     SQLite  ← the source of truth
```

Four decisions worth knowing about:

**The local database is the source of truth.** Providers import *into* it; every calculation reads
only from it. An API going away costs future updates, never your collection, your price history or
past analysis. Your own `price_snapshots` become the thing you cannot buy back.

**Money is integer minor units.** Expected value sums probability-weighted outcomes across every
grade, every card, every submission. In floats that drifts. `allocate()` splits shared costs so the
parts always sum exactly back to the total.

**One row per physical card.** Copy A can grade a 10 and copy B an 8. `quantity` exists for
interchangeable bulk; `POST /api/cards/{id}/split` breaks a stack into individual rows before it
enters a submission.

**Nothing about a grading company is hard-coded.** Companies, tiers, minimums, value ceilings and
memberships are rows with effective dates. Changing a price is an edit the engine picks up on the
next evaluation — and historical submissions keep costing what they actually cost.

---

## `evaluate_card` — the seam

One call returns everything needed to decide, and the UI only visualises it:

```jsonc
{
  "raw": { … },               // identity, ownership, the raw value in use
  "condition": { … },         // sub-scores, completeness, notable defects
  "grade_prediction": { … },  // a distribution per grading company
  "market": { … },            // Phase 3
  "liquidity": { … },         // Phase 3
  "trend": { … },             // Phase 3
  "grading_options": { … },   // routes now, full costing in Phase 4
  "expected_outcomes": { … }, // Phase 5
  "recommendation": { … },    // Phase 5 (your overrides work today)
  "explanation": [ … ],       // the "Why?" panel
  "blockers": [ … ]           // what is still missing, in order
}
```

The shape is fixed. Later phases fill in blocks without changing it, and a client written against
it today keeps working as they land.

---

## Two rules this build takes seriously

**No false precision.** Every derived value carries its evidence — sample size, window, last sale,
confidence. A card with two sales in nine months does not get the same presentation as one with
thirty-seven in ninety days. Where a figure cannot be calculated it is `null` with a reason, never
`0`: a zero reads as "no upside" rather than "not calculated".

**No invented grades.** The model never returns a point estimate, because graders are not
deterministic — the same card submitted twice can come back a 9 and a 10. It refuses to predict
from an assessment with nothing answered, and never treats an unanswered field as perfect: a
half-finished assessment produces a wide range and says so. Its defect rules are SlabStack's
estimates, not any grader's published standard, which is why they are editable rows.

**No invented prices.** Grading tiers are seeded `active` only where the price can be attributed —
CGC and ACE from the figures in the specification, flagged for you to verify. PSA ships as tier
structure with no price and `active: false`, so the engine skips it until you enter your own. An
unpriced tier left active would cost a submission at £0 and make everything look profitable.

---

## Documentation

| Document                                     | Contents                                              |
| -------------------------------------------- | ----------------------------------------------------- |
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | Every endpoint across all phases, envelopes, errors. |
| [`docs/SCHEMA.md`](docs/SCHEMA.md)           | Table-by-table design and the reasoning behind it.    |
| [`docs/schema.sql`](docs/schema.sql)         | Generated DDL.                                        |
| [`docs/ROADMAP.md`](docs/ROADMAP.md)         | Phases 2–8: what exists, what to build, what to watch. |

---

## Data and third parties

Your collection never leaves your machine. When market-data providers arrive in Phase 3 they will
use official, permitted APIs under each service's terms — a source that requires scraping a site
that forbids it does not belong in this application. Every network provider ships disabled;
`manual` and `csv` import work with no network, no key and no terms of service, and are what the
application degrades to.
