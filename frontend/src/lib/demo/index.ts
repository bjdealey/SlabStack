/**
 * The in-browser API used by the GitHub Pages demo.
 *
 * GitHub Pages serves static files: no Python process, no writable SQLite. So
 * for the demo build only, this stands in for the server — same paths, same
 * response shapes, same error envelope — backed by an in-memory store seeded
 * with a sample collection. Reference data (grading tiers, settings
 * definitions, enums, sets) is a fixture generated from the real API, so it
 * cannot drift from what the server actually returns.
 *
 * Everything stays in the tab. Nothing is uploaded, nothing is persisted beyond
 * a page refresh, and a reload restores the sample collection.
 */

import type {
  Card,
  CardEvaluation,
  CardImage,
  CollectionDecisions,
  ConditionAssessment,
  ConditionWrite,
  Facets,
  GradingCompany,
  ImportResult,
  MarketPrice,
  MarketSale,
  MarketSummary,
  Page,
  SellingProfile,
} from '@/lib/types'
import fixtures from './fixtures.json'
import {
  DEMO_RULES,
  buildAssessment,
  buildCatalogKey,
  buildEvaluation,
  buildGradePrediction,
  buildSummary,
} from './engine'
import { netSaleValue, suggestDeclaredValue } from './economics'
import * as market from './market'
import { optimise } from './optimiser'
import type { StoredLine, StoredSubmission } from './submissions'
import { costSubmission, nextReference } from './submissions'
import {
  FILTERS,
  applyFilter,
  buildSellingQueue,
  buildSubmissionReturns,
  rankOpportunities,
} from './analytics'
import type { ResultRow } from './calibration'
import {
  DEFAULT_MAX_OFFSET,
  DEFAULT_MINIMUM_SAMPLE,
  buildAccuracyReport,
  calibrationFor,
} from './calibration'
import { SEED_CARDS, SEED_MARKET } from './seed'

export const DEMO_MODE = import.meta.env.VITE_DEMO === 'true'

interface DemoError {
  code: string
  message: string
  status: number
  details?: Record<string, unknown> | null
}

class DemoFailure extends Error {
  readonly payload: DemoError

  constructor(payload: DemoError) {
    super(payload.message)
    this.payload = payload
  }
}

const fail = (code: string, message: string, status = 400, details?: Record<string, unknown>) => {
  throw new DemoFailure({ code, message, status, details })
}

const nowIso = () => new Date().toISOString()
const newId = () => Math.random().toString(36).slice(2, 12) + Date.now().toString(36)
const asset = (file: string) => `${import.meta.env.BASE_URL}demo/${file}`

// --- Store -------------------------------------------------------------------

interface Store {
  cards: Card[]
  conditions: Map<string, ConditionAssessment>
  companies: GradingCompany[]
  settings: Record<string, unknown>
  /** Keyed by catalog_key, exactly as the server keys them. */
  sales: MarketSale[]
  prices: MarketPrice[]
  listings: Map<string, number>
  submissions: StoredSubmission[]
  /** Toggled in the UI, honoured nowhere: this tab has no network to enable. */
  enabledSources: Record<string, boolean>
}

function blankCard(input: Partial<Card> & { name: string }, id?: string): Card {
  const card: Card = {
    id: id ?? newId(),
    name: input.name,
    set_id: null,
    set_name: input.set_name ?? null,
    set_code: input.set_code ?? null,
    card_number: input.card_number ?? null,
    variant_id: null,
    variant: input.variant ?? null,
    language: input.language ?? 'English',
    printing: input.printing ?? 'Unlimited',
    rarity: input.rarity ?? null,
    pokemon: input.pokemon ?? null,
    card_type: input.card_type ?? null,
    is_promo: input.is_promo ?? false,
    release_date: null,
    catalog_key: null,
    raw_condition: input.raw_condition ?? 'Unknown',
    quantity: input.quantity ?? 1,
    purchase_price: input.purchase_price ?? null,
    purchase_currency: null,
    purchase_date: input.purchase_date ?? null,
    status: input.status ?? 'in_collection',
    user_raw_value: input.user_raw_value ?? null,
    user_declared_value: input.user_declared_value ?? null,
    decision_override: input.decision_override ?? null,
    decision_override_reason: input.decision_override_reason ?? null,
    review_after: input.review_after ?? null,
    notes: input.notes ?? null,
    external_ids: null,
    created_at: nowIso(),
    updated_at: nowIso(),
    images: [],
    primary_image_url: null,
    has_condition_assessment: false,
  }
  card.catalog_key = buildCatalogKey(card)

  // Match the server: a known set code fills in the set name.
  const known = (fixtures.sets as { code: string; name: string; id: string }[]).find(
    (item) => item.code.toLowerCase() === (card.set_code ?? '').toLowerCase(),
  )
  if (known) {
    card.set_id = known.id
    card.set_name = known.name
  }
  return card
}

/**
 * Stable ids for the seeded cards.
 *
 * Random ids would break every link into the demo: the store is rebuilt on each
 * page load, so `#/cards/<random>` would 404 on a refresh or when someone
 * shares a card. Deriving the id from the card's identity makes those links
 * durable. Cards added during a session still vanish on reload — that is what
 * "a refresh starts over" means.
 */
function seedId(entry: (typeof SEED_CARDS)[number]): string {
  const parts = [entry.card.name, entry.card.set_code, entry.card.card_number]
  return (
    'demo-' +
    parts
      .filter(Boolean)
      .join('-')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
  )
}

function seedStore(): Store {
  const cards: Card[] = []
  const conditions = new Map<string, ConditionAssessment>()

  // Reversed so the newest-first default ordering shows the curated cards first.
  for (const entry of [...SEED_CARDS].reverse()) {
    const card = blankCard(entry.card, seedId(entry))
    if (entry.image) {
      const image: CardImage = {
        id: `${card.id}-front`,
        card_id: card.id,
        side: 'front',
        url: asset(entry.image),
        thumbnail_url: asset(entry.image),
        original_filename: entry.image,
        mime_type: 'image/jpeg',
        width: 734,
        height: 1024,
        size_bytes: null,
        is_primary: true,
        sort_order: 0,
        caption: null,
        created_at: nowIso(),
      }
      card.images = [image]
      card.primary_image_url = image.thumbnail_url
    }
    if (entry.condition) {
      const assessment = buildAssessment(card.id, entry.condition)
      assessment.id = `${card.id}-condition`
      conditions.set(card.id, assessment)
      card.has_condition_assessment = true
    }
    cards.push(card)
  }

  const companies = structuredClone(fixtures.gradingCompanies) as GradingCompany[]
  const settings = structuredClone(fixtures.settings.values) as Record<string, unknown>
  const { sales, listings } = seedSales(cards, companies)

  const seeded: Store = {
    cards, conditions, companies, settings, sales, prices: [], listings, submissions: [],
    enabledSources: {},
  }
  const params = market.paramsFromSettings(settings)
  const money = (settings.currency as string) ?? 'GBP'
  for (const key of new Set(sales.map((sale) => sale.catalog_key))) {
    market.markOutliers(
      sales.filter((sale) => sale.catalog_key === key),
      params,
    )
    market.recompute(key, sales, seeded.prices, params, money)
  }
  seedSubmissions(seeded)
  return seeded
}

/**
 * One parcel already back, one still out.
 *
 * Without these the returns view has nothing to show, and "predicted 9.5, got
 * 10" is the one thing on the analytics page no other screen says. The returned
 * parcel deliberately holds a card that graded into a band nobody has sold, so
 * the demo also shows the honest half: a slab that cost money and cannot be
 * valued, and a return that is therefore a floor rather than an estimate.
 */
function seedSubmissions(seeded: Store): void {
  const cgc = seeded.companies.find((item) => item.code === 'CGC')
  const psa = seeded.companies.find((item) => item.code === 'PSA')
  if (!cgc) return

  const find = (name: string) => seeded.cards.find((card) => card.name === name)
  const umbreon = find('Umbreon VMAX')
  const promo = find('Eevee Heroes Promo 1')
  const gengar = find('Gengar VMAX')

  const day = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString()
  const economy = cgc.tiers.find((tier) => tier.tier_name === 'Economy') ?? cgc.tiers[0] ?? null

  // The frozen prediction is computed from the model rather than hand-written,
  // so the demo's Brier scores and calibration curve are real arithmetic over a
  // real distribution instead of decorative numbers.
  const frozen = (cardId: string, company: GradingCompany) => {
    const assessment = seeded.conditions.get(cardId)
    if (!assessment) return { likely: null, probabilities: null }
    const row = buildGradePrediction(assessment, [company]).by_company[0]
    if (!row) return { likely: null, probabilities: null }
    return {
      likely: row.likely_grade,
      probabilities: Object.fromEntries(
        row.probabilities.map((item) => [String(item.grade), item.probability]),
      ),
    }
  }

  const line = (
    cardId: string,
    order: number,
    company: GradingCompany,
    actual: number | null,
  ): StoredLine => {
    const prediction = frozen(cardId, company)
    return {
      id: `demo-line-${cardId}-${order}`,
      card_id: cardId,
      tier_id: economy?.id ?? null,
      declared_value_minor: null,
      declared_value_source: 'system',
      declared_value_confidence: null,
      predicted_grade: prediction.likely,
      predicted_probabilities: prediction.probabilities,
      actual_grade: actual,
      cert_number: actual === null ? null : `10${order}9944${order}`,
      status: actual === null ? 'planned' : 'graded',
      sort_order: order,
      notes: null,
    }
  }

  if (umbreon && promo) {
    seeded.submissions.push({
      id: 'demo-submission-returned',
      reference: 'SUB-2026-05-001',
      name: 'Spring bulk',
      company_id: cgc.id,
      tier_id: economy?.id ?? null,
      status: 'returned',
      currency: (seeded.settings.currency as string) ?? 'GBP',
      cost_allocation_method:
        (seeded.settings.cost_allocation_method as string) ?? 'value_weighted',
      shipping_out_minor: 2000,
      shipping_return_minor: 2000,
      handling_minor: 0,
      other_fees_minor: 0,
      membership_allocation_minor: 0,
      submitted_at: day(96),
      received_at: day(88),
      returned_at: day(34),
      tracking_outbound: null,
      tracking_return: null,
      notes: null,
      created_at: day(100),
      cards: [
        // Came back a 10, above what the model expected: the good surprise.
        line(umbreon.id, 0, cgc, 10),
        // Came back a 6. Nobody has sold a CGC 6 of it, so it cannot be valued
        // either — which is the other half of the point of including it.
        line(promo.id, 1, cgc, 6),
      ],
    })
  }

  // An earlier CGC parcel, so the demo crosses the threshold where a correction
  // starts being applied. Without it the learning view only ever shows its
  // least interesting state — measured, never acted on — and the whole point of
  // the phase is what happens once there is enough evidence to act.
  const earlier = [
    ['Eevee Heroes Promo 2', 9],
    ['Eevee Heroes Promo 3', 9],
    ['Eevee Heroes Promo 4', 9.5],
    ['Eevee Heroes Promo 5', 9],
    ['Giratina VSTAR', 8.5],
    ['Pikachu', 9],
    ['Rayquaza VMAX', 9],
    ['Mew ex', 9.5],
  ] as const
  const earlierLines = earlier
    .map(([name, grade], index) => {
      const card = find(name)
      return card ? line(card.id, index, cgc, grade) : null
    })
    .filter((row): row is StoredLine => row !== null)

  if (earlierLines.length) {
    seeded.submissions.push({
      id: 'demo-submission-earlier',
      reference: 'SUB-2026-02-001',
      name: 'Winter bulk',
      company_id: cgc.id,
      tier_id: economy?.id ?? null,
      status: 'returned',
      currency: (seeded.settings.currency as string) ?? 'GBP',
      cost_allocation_method: 'equal',
      shipping_out_minor: 2000,
      shipping_return_minor: 2000,
      handling_minor: 0,
      other_fees_minor: 0,
      membership_allocation_minor: 0,
      submitted_at: day(212),
      received_at: day(204),
      returned_at: day(150),
      tracking_outbound: null,
      tracking_return: null,
      notes: null,
      created_at: day(216),
      cards: earlierLines,
    })
  }

  if (gengar && psa) {
    seeded.submissions.push({
      id: 'demo-submission-open',
      reference: 'SUB-2026-07-002',
      name: 'Still at PSA',
      company_id: psa.id,
      tier_id: psa.tiers[0]?.id ?? null,
      status: 'shipped',
      currency: (seeded.settings.currency as string) ?? 'GBP',
      cost_allocation_method: 'equal',
      shipping_out_minor: 2000,
      shipping_return_minor: 2000,
      handling_minor: 0,
      other_fees_minor: 0,
      membership_allocation_minor: 0,
      submitted_at: day(12),
      received_at: null,
      returned_at: null,
      tracking_outbound: null,
      tracking_return: null,
      notes: null,
      created_at: day(14),
      cards: [line(gengar.id, 0, psa, null)],
    })
  }
}

/**
 * Turn the seed's described series into dated sales.
 *
 * Deterministic: the same card always produces the same prices, so the demo
 * looks identical on every load while still being dated relative to today.
 */
function seedSales(
  cards: Card[],
  companies: GradingCompany[],
): { sales: MarketSale[]; listings: Map<string, number> } {
  const sales: MarketSale[] = []
  const listings = new Map<string, number>()
  const today = Date.now()
  const dayOf = (daysAgo: number) =>
    new Date(today - daysAgo * 86_400_000).toISOString().slice(0, 10)

  // A tiny deterministic hash, so "jitter" is stable across reloads.
  const wobble = (seed: string, index: number, amount: number) => {
    if (!amount) return 0
    let hash = 2166136261
    for (const char of `${seed}:${index}`) {
      hash = Math.imul(hash ^ char.charCodeAt(0), 16777619)
    }
    return (((hash >>> 0) % 2001) / 1000 - 1) * amount
  }

  for (const card of cards) {
    const spec = SEED_MARKET[card.name]
    if (!spec || !card.catalog_key) continue
    if (spec.activeListings) listings.set(card.catalog_key, spec.activeListings)

    for (const series of spec.series) {
      const [code, gradeText] = series.label === 'raw' ? [null, null] : series.label.split(' ')
      const company = code ? companies.find((item) => item.code === code) : undefined
      for (let index = 0; index < series.count; index += 1) {
        const price =
          series.price + index * (series.drift ?? 0) + wobble(card.id + series.label, index, series.jitter ?? 0)
        sales.push(
          makeSale(card, {
            id: `${card.id}-${series.label}-${index}`.replace(/[^a-zA-Z0-9-]/g, '-'),
            sale_date: dayOf((series.offset ?? 0) + index * series.spacing),
            sale_price: Math.max(1, Math.round(price * 100) / 100),
            grade_label: series.label,
            grade: gradeText ? Number(gradeText) : null,
            company_id: company?.id ?? null,
            listing_title:
              series.title ??
              `${card.name} ${card.card_number ?? ''} ${card.variant ?? ''} ${
                series.label === 'raw' ? '' : series.label
              }`.trim(),
          }),
        )
      }
    }

    for (const [index, junk] of (spec.junk ?? []).entries()) {
      sales.push(
        makeSale(card, {
          id: `${card.id}-junk-${index}`,
          sale_date: dayOf(junk.daysAgo),
          sale_price: junk.price,
          grade_label: 'raw',
          grade: null,
          company_id: null,
          listing_title: junk.title,
        }),
      )
    }
  }

  // Classify exactly as an import would, so the demo shows real exclusions.
  for (const sale of sales) {
    const card = cards.find((item) => item.catalog_key === sale.catalog_key)
    const verdict = market.classify(
      sale.listing_title,
      { language: card?.language, variant: card?.variant, printing: card?.printing },
      sale.lot_size,
      sale.grade_label,
    )
    if (verdict) {
      sale.is_excluded = true
      sale.exclusion_reason = verdict.reason
      sale.excluded_by = 'system'
    }
  }

  return { sales, listings }
}

function makeSale(card: Card, input: Partial<MarketSale> & { id: string }): MarketSale {
  return {
    catalog_key: card.catalog_key!,
    card_id: card.id,
    company_id: null,
    grade: null,
    grade_label: 'raw',
    platform: 'eBay',
    sale_date: nowIso().slice(0, 10),
    sale_price: 0,
    currency: 'GBP',
    shipping: 3.95,
    total_paid: null,
    condition_note: null,
    listing_title: null,
    source_url: null,
    seller: null,
    bid_count: null,
    lot_size: 1,
    is_auction: null,
    is_excluded: false,
    exclusion_reason: null,
    excluded_by: null,
    is_outlier: false,
    source_id: 'demo-manual',
    external_id: input.id,
    imported_at: nowIso(),
    ...input,
  }
}

let store: Store = seedStore()

export function resetDemo(): void {
  store = seedStore()
}

// --- Helpers -----------------------------------------------------------------

const currency = () => (store.settings.currency as string) ?? 'GBP'

function requireCard(id: string): Card {
  const card = store.cards.find((item) => item.id === id)
  if (!card) fail('not_found', `Card '${id}' was not found.`, 404, { resource: 'Card', id })
  return card!
}

function marketParams() {
  return market.paramsFromSettings(store.settings)
}

function summaryFor(card: Card): MarketSummary {
  return market.summarise(card.catalog_key, store.sales, store.prices, marketParams(), currency())
}

function repriceKey(catalogKey: string | null): MarketPrice[] {
  if (!catalogKey) return []
  const forKey = store.sales.filter((sale) => sale.catalog_key === catalogKey)
  market.markOutliers(forKey, marketParams())
  return market.recompute(catalogKey, store.sales, store.prices, marketParams(), currency())
}

function sellingProfile(): SellingProfile | null {
  const profiles = (fixtures.sellingProfiles as SellingProfile[]).filter((item) => item.active)
  return profiles.find((item) => item.is_default) ?? profiles[0] ?? null
}

/** Sales counted per grade label, for per-company slab liquidity. */
function salesByLabel(card: Card): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const sale of store.sales) {
    if (sale.catalog_key !== card.catalog_key || sale.is_excluded) continue
    counts[sale.grade_label] = (counts[sale.grade_label] ?? 0) + 1
  }
  return counts
}

function evaluationFor(card: Card, batchSize = 1): CardEvaluation {
  return buildEvaluation(
    card,
    store.conditions.get(card.id),
    store.companies,
    currency(),
    summaryFor(card),
    store.settings,
    sellingProfile(),
    batchSize,
    salesByLabel(card),
    calibrationInput,
  )
}

/** Decisions that mean money would actually be spent on grading. */
const GRADING_DECISIONS = new Set(['grade', 'grade_if_batch_filled'])

/**
 * Data sources as the demo can honestly present them.
 *
 * The rows are real — same codes, same adapter flags — but every network source
 * stays off, because there is no server in this tab to make a request from. A
 * switch that flipped and did nothing would be worse than one that explains.
 */
function demoSources() {
  return (
    fixtures.dataSources as { code: string; has_adapter?: boolean; enabled?: boolean }[]
  ).map((source) => ({
    ...source,
    enabled: store.enabledSources[source.code] ?? source.enabled ?? false,
    last_sync_at: null,
    last_sync_status: null,
    last_sync_error: null,
  }))
}

/**
 * The analytics adapters.
 *
 * Each one gathers what the ported engine needs and hands off; none of them
 * computes a figure of its own. That mirrors the server, where analytics is a
 * view over answers other engines already gave.
 */
function minimumLiquidity(): number {
  const base = Number(store.settings.minimum_liquidity_score ?? 3) || 3
  const risk = String(store.settings.risk_tolerance ?? 'balanced')
  if (risk === 'conservative') return base + 1.5
  if (risk === 'aggressive') return Math.max(0, base - 1)
  return base
}

function sellingQueue() {
  return buildSellingQueue(collectionDecisions(1), (cardId) => {
    const card = store.cards.find((item) => item.id === cardId)
    const summary = card ? summaryFor(card) : null
    const raw = summary?.prices.find((price) => price.grade_label === 'raw') ?? null
    return {
      realisticMinor:
        raw === null
          ? null
          : market.toMinor(raw.realistic_sale ?? raw.median ?? 0) || null,
      highQuartileMinor: raw?.high_quartile ? market.toMinor(raw.high_quartile) : null,
      confidence: raw?.confidence ?? 'none',
      liquidityScore: summary?.liquidity.score ?? null,
      liquidityBand: summary?.liquidity.band ?? null,
      daysSinceLastSale: summary?.liquidity.days_since_last_sale ?? null,
      trendDirection: summary?.trend.direction ?? null,
      purchasePrice: card?.purchase_price ?? null,
    }
  })
}

/**
 * Every graded card that had a prediction frozen behind it, ready to be marked.
 *
 * Read from the submission lines rather than a separate results store: the
 * lines *are* the record, and a second copy would be the one that drifts.
 */
function resultRows(): ResultRow[] {
  const rows: ResultRow[] = []
  for (const submission of store.submissions) {
    const company = store.companies.find((item) => item.id === submission.company_id)
    if (!company) continue
    for (const line of submission.cards) {
      if (line.actual_grade === null || !line.predicted_probabilities) continue
      const card = store.cards.find((item) => item.id === line.card_id)
      rows.push({
        cardId: line.card_id,
        name: card ? (card.card_number ? `${card.name} ${card.card_number}` : card.name) : line.card_id,
        companyId: company.id,
        companyCode: company.code,
        actualGrade: line.actual_grade,
        predictedGrade: line.predicted_grade,
        predictedProbabilities: line.predicted_probabilities,
        gradedAt: submission.returned_at?.slice(0, 10) ?? null,
      })
    }
  }
  return rows
}

/** Graded but never scoreable: no prediction was frozen when they were sent. */
function unscoreableCount(): number {
  let count = 0
  for (const submission of store.submissions) {
    for (const line of submission.cards) {
      if (line.actual_grade !== null && !line.predicted_probabilities) count += 1
    }
  }
  return count
}

function calibrationOptions() {
  return {
    minimumSample: Number(store.settings.calibration_minimum_sample ?? DEFAULT_MINIMUM_SAMPLE) ||
      DEFAULT_MINIMUM_SAMPLE,
    maxOffset: Number(store.settings.calibration_max_offset ?? DEFAULT_MAX_OFFSET) ||
      DEFAULT_MAX_OFFSET,
    enabled: store.settings.calibration_enabled !== false,
  }
}

function calibrationState() {
  const options = calibrationOptions()
  const rows = resultRows()
  return {
    enabled: options.enabled,
    minimum_sample: options.minimumSample,
    max_offset: options.maxOffset,
    companies: store.companies
      .filter((company) => company.active !== false)
      .map((company) => calibrationFor(company, rows, options)),
  }
}

/** What the user's results taught the model about one grader, for `predictGrade`. */
function calibrationInput(companyId: string | null) {
  if (!companyId) return undefined
  const company = store.companies.find((item) => item.id === companyId)
  if (!company) return undefined
  const entry = calibrationFor(company, resultRows(), calibrationOptions())
  return {
    offset: entry.applied ? entry.grade_offset : 0,
    spreadMultiplier: entry.applied ? entry.spread_multiplier : 1,
    sampleSize: entry.sample_size,
    note: entry.reason,
  }
}

function submissionReturns() {
  const profile = sellingProfile()
  return buildSubmissionReturns(
    currency(),
    store.submissions.map((stored) => {
      const submission = costed(stored)
      return {
        submission,
        gradedValueMinor: (cardId: string, grade: number) => {
          const card = store.cards.find((item) => item.id === cardId)
          if (!card || !submission.company_code) return null
          const label = market.gradeLabel(submission.company_code, grade)
          const price = store.prices.find(
            (row) => row.catalog_key === card.catalog_key && row.grade_label === label,
          )
          if (!price) return null
          return market.toMinor(price.realistic_sale ?? price.median ?? 0) || null
        },
        netOf: (grossMinor: number) => netSaleValue(grossMinor, profile, true)?.netMinor ?? null,
      }
    }),
  )
}

/**
 * The decision engine across the whole collection (spec sections 32, 37).
 *
 * Only cards that can actually be decided are evaluated — a card with no
 * assessment or no comparable sales has no decision to compute. The ones
 * skipped are counted and reported, so "expected profit £2,140" is always read
 * next to "across 3 of your 214 cards".
 */
function collectionDecisions(batchSize: number): CollectionDecisions {
  const analysable = store.cards.filter(
    (card) =>
      store.conditions.get(card.id) !== undefined &&
      store.prices.some((price) => price.catalog_key === card.catalog_key),
  )

  const result: CollectionDecisions = {
    status: 'ok',
    reason: null,
    currency: currency(),
    analysed: 0,
    total_cards: store.cards.length,
    skipped_not_ready: store.cards.length - analysable.length,
    truncated: false,
    batch_size: batchSize,
    expected_profit: null,
    potential_graded_value: null,
    potential_uplift: null,
    total_grading_cost: null,
    counts: {},
    opportunities: [],
  }

  let profitMinor = 0
  let gradedMinor = 0
  let rawMinor = 0
  let costMinor = 0
  let counted = 0

  for (const card of analysable) {
    const evaluated = evaluationFor(card, batchSize)
    const recommendation = evaluated.recommendation
    result.counts[recommendation.decision] = (result.counts[recommendation.decision] ?? 0) + 1
    result.analysed += 1
    result.opportunities.push({
      card_id: card.id,
      name: evaluated.raw.display_name,
      set_label: evaluated.raw.set_label,
      decision: recommendation.decision,
      headline: recommendation.headline,
      confidence: recommendation.confidence,
      company_code: recommendation.company_code,
      tier_name: recommendation.tier_name,
      expected_profit: recommendation.expected_profit,
      roi_pct: recommendation.roi_pct,
      probability_of_profit: recommendation.probability_of_profit,
      opportunity_score: recommendation.opportunity_score,
      grading_cost: recommendation.grading_cost,
      net_raw_alternative: recommendation.net_raw_alternative,
      coverage: recommendation.coverage,
      is_user_override: recommendation.is_user_override,
      liquidity_score: evaluated.liquidity.score,
      liquidity_band: evaluated.liquidity.band,
      trend_direction: evaluated.trend.direction,
    })

    // Only cards the engine would actually grade contribute to the totals.
    // Summing the profit of cards it told you *not* to grade would describe a
    // plan nobody is going to carry out.
    if (!GRADING_DECISIONS.has(recommendation.decision)) continue
    if (recommendation.expected_profit === null) continue
    const quantity = Math.max(1, card.quantity)
    profitMinor += market.toMinor(recommendation.expected_profit) * quantity
    gradedMinor += market.toMinor(recommendation.expected_net ?? 0) * quantity
    rawMinor += market.toMinor(recommendation.net_raw_alternative ?? 0) * quantity
    costMinor += market.toMinor(recommendation.grading_cost ?? 0) * quantity
    counted += 1
  }

  result.opportunities.sort(
    (a: CollectionDecisions['opportunities'][number], b: CollectionDecisions['opportunities'][number]) =>
      (b.opportunity_score ?? -1) - (a.opportunity_score ?? -1) ||
      (b.expected_profit ?? 0) - (a.expected_profit ?? 0),
  )

  if (counted) {
    result.expected_profit = profitMinor / 100
    result.potential_graded_value = gradedMinor / 100
    result.potential_uplift = (gradedMinor - rawMinor) / 100
    result.total_grading_cost = costMinor / 100
  }

  if (!analysable.length) {
    result.status = 'insufficient_data'
    result.reason =
      'No card has both a condition assessment and comparable sales yet, so there is nothing ' +
      'to decide. Assess a card and add its sales.'
  } else if (result.skipped_not_ready) {
    result.status = 'partial'
    result.reason =
      `${result.skipped_not_ready} of ${result.total_cards} cards were skipped: they need a ` +
      'condition assessment and comparable sales before they can be decided.'
  }
  return result
}


/* --- Submissions ---------------------------------------------------------- */

const SEALED = new Set(['shipped', 'received', 'grading', 'returned', 'cancelled'])

function cardMap(): Map<string, Card> {
  return new Map(store.cards.map((card) => [card.id, card]))
}

/**
 * The declared value for one card against one grader.
 *
 * Computed straight from that grader's ladder and prices rather than by running
 * a whole evaluation: costing a parcel touches every card, and a full
 * evaluation per line would make the cost of opening a submission grow with
 * the square of its size.
 */
function declaredValueFor(card: Card, companyCode: string | null): number | null {
  if (card.user_declared_value !== null) return market.toMinor(card.user_declared_value)
  const company = store.companies.find((item) => item.code === companyCode) ?? null
  const prediction = company
    ? buildGradePrediction(store.conditions.get(card.id), [company], calibrationInput).by_company[0]
    : null
  const probabilities = prediction?.probabilities.length
    ? Object.fromEntries(prediction.probabilities.map((item) => [item.grade, item.probability]))
    : null
  const prices = store.prices.filter((price) => price.catalog_key === card.catalog_key)
  return suggestDeclaredValue(card, prices, probabilities, companyCode ?? null).valueMinor
}

function costed(submission: StoredSubmission) {
  return costSubmission({
    submission,
    cards: cardMap(),
    companies: store.companies,
    declaredFor: declaredValueFor,
    insurancePct: Number(store.settings.default_submission_insurance_pct ?? 0) || 0,
    today: new Date().toISOString().slice(0, 10),
  })
}

function requireSubmission(id: string): StoredSubmission {
  const found = store.submissions.find((item) => item.id === id)
  if (!found) fail('not_found', `Submission '${id}' was not found.`, 404)
  return found!
}

function requireEditable(submission: StoredSubmission): void {
  if (SEALED.has(submission.status)) {
    fail(
      'conflict',
      `This submission is ${submission.status.replace(/_/g, ' ')}, so its cards can no longer ` +
        'be changed. What you sent is a record, not a draft.',
      409,
      { status: submission.status },
    )
  }
}

/**
 * The grade the model expects from this grader, right now.
 *
 * ``null`` when the card has no assessment, which is honest: no prediction was
 * made, so there is nothing to be right or wrong about.
 */
function predictedGradeFor(
  cardId: string,
  companyId: string | null,
): { likely: number | null; probabilities: Record<string, number> | null } {
  if (!companyId) return { likely: null, probabilities: null }
  const company = store.companies.find((item) => item.id === companyId)
  const assessment = store.conditions.get(cardId)
  if (!company || !assessment) return { likely: null, probabilities: null }
  const prediction = buildGradePrediction(assessment, [company])
  const row = prediction.by_company[0]
  if (!row) return { likely: null, probabilities: null }
  return {
    likely: row.likely_grade,
    // The whole belief, not just the mode: a Brier score marks the distribution.
    probabilities: Object.fromEntries(
      row.probabilities.map((item) => [String(item.grade), item.probability]),
    ),
  }
}

function newLine(
  cardId: string,
  tierId: string | null,
  order: number,
  companyId: string | null,
): StoredLine {
  return {
    id: newId(),
    card_id: cardId,
    tier_id: tierId,
    declared_value_minor: null,
    declared_value_source: 'system',
    declared_value_confidence: null,
    // Frozen now, not read back later: the prediction worth scoring is the one
    // you held when you sent the card, not one computed after the grade is in.
    predicted_grade: predictedGradeFor(cardId, companyId).likely,
    predicted_probabilities: predictedGradeFor(cardId, companyId).probabilities,
    actual_grade: null,
    cert_number: null,
    status: 'planned',
    sort_order: order,
    notes: null,
  }
}

const SORTERS: Record<string, (a: Card, b: Card) => number> = {
  created_at: (a, b) => a.created_at.localeCompare(b.created_at),
  updated_at: (a, b) => a.updated_at.localeCompare(b.updated_at),
  name: (a, b) => a.name.localeCompare(b.name),
  set_code: (a, b) => (a.set_code ?? '').localeCompare(b.set_code ?? ''),
  card_number: (a, b) => (a.card_number ?? '').localeCompare(b.card_number ?? ''),
  purchase_price: (a, b) => (a.purchase_price ?? 0) - (b.purchase_price ?? 0),
  quantity: (a, b) => a.quantity - b.quantity,
  release_date: (a, b) => (a.release_date ?? '').localeCompare(b.release_date ?? ''),
}

function listCards(params: URLSearchParams): Page<Card> {
  let items = [...store.cards]

  const q = params.get('q')?.trim().toLowerCase()
  if (q) {
    items = items.filter((card) =>
      [card.name, card.set_name, card.set_code, card.card_number, card.pokemon, card.variant, card.notes]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(q)),
    )
  }

  const equals = (value: string | null | undefined, key: string) => {
    const wanted = params.get(key)
    return !wanted || (value ?? '').toLowerCase() === wanted.toLowerCase()
  }
  items = items.filter(
    (card) =>
      equals(card.set_code, 'set_code') &&
      equals(card.language, 'language') &&
      equals(card.variant, 'variant') &&
      equals(card.rarity, 'rarity') &&
      equals(card.pokemon, 'pokemon'),
  )

  const bool = (key: string) => {
    const raw = params.get(key)
    return raw === null ? undefined : raw === 'true'
  }
  const hasImages = bool('has_images')
  if (hasImages !== undefined) {
    items = items.filter((card) => (card.images.length > 0) === hasImages)
  }
  const hasCondition = bool('has_condition')
  if (hasCondition !== undefined) {
    items = items.filter((card) => store.conditions.has(card.id) === hasCondition)
  }
  const isPromo = bool('is_promo')
  if (isPromo !== undefined) items = items.filter((card) => card.is_promo === isPromo)

  const statuses = params.getAll('status')
  if (statuses.length) items = items.filter((card) => statuses.includes(card.status))

  const sorter = SORTERS[params.get('sort') ?? 'created_at'] ?? SORTERS.created_at
  items.sort(sorter)
  if ((params.get('order') ?? 'desc') === 'desc') items.reverse()

  const page = Number(params.get('page') ?? 1)
  const pageSize = Number(params.get('page_size') ?? 50)
  const total = items.length
  return {
    items: items.slice((page - 1) * pageSize, page * pageSize),
    total,
    page,
    page_size: pageSize,
    total_pages: pageSize ? Math.ceil(total / pageSize) : 0,
  }
}

function facets(): Facets {
  const distinct = (pick: (card: Card) => string | null) =>
    [...new Set(store.cards.map(pick).filter((value): value is string => Boolean(value)))].sort()
  return {
    sets: distinct((card) => card.set_code),
    languages: distinct((card) => card.language),
    variants: distinct((card) => card.variant),
    rarities: distinct((card) => card.rarity),
    statuses: distinct((card) => card.status),
  }
}

function applyCardPayload(card: Card, payload: Record<string, unknown>): Card {
  Object.assign(card, payload)
  card.updated_at = nowIso()
  card.catalog_key = buildCatalogKey(card)
  const known = (fixtures.sets as { code: string; name: string; id: string }[]).find(
    (item) => item.code.toLowerCase() === (card.set_code ?? '').toLowerCase(),
  )
  if (known) {
    card.set_id = known.id
    card.set_name = known.name
  }
  return card
}

function refreshPrimaryImage(card: Card): void {
  const front = card.images.filter((image) => image.side === 'front')
  const primary = front.find((image) => image.is_primary) ?? front[0] ?? card.images[0]
  card.primary_image_url = primary ? (primary.thumbnail_url ?? primary.url) : null
}

// --- Router ------------------------------------------------------------------

type Handler = () => unknown

/** Matches the server's routes. Returns the same JSON the API would. */
function route(method: string, pathname: string, params: URLSearchParams, raw: unknown): unknown {
  const body = (raw ?? {}) as Record<string, unknown>
  const segments = pathname.split('/').filter(Boolean)
  const at = (index: number) => segments[index]
  const payload = (body ?? {}) as Record<string, never>

  const routes: [boolean, Handler][] = [
    [
      method === 'GET' && pathname === '/health',
      () => ({
        status: 'ok',
        app: 'SlabStack',
        version: '0.1.0',
        database: 'in-browser demo',
        database_ready: true,
        data_dir: 'memory',
        cards: store.cards.length,
        grading_companies: store.companies.length,
        market_sales: 0,
        phase: 'live market data (demo)',
      }),
    ],
    [method === 'GET' && pathname === '/meta/enums', () => fixtures.enums],
    [
      method === 'GET' && pathname === '/settings',
      () => ({ values: store.settings, definitions: fixtures.settings.definitions }),
    ],
    [
      method === 'PATCH' && pathname === '/settings',
      () => {
        store.settings = { ...store.settings, ...((payload as { values?: object }).values ?? {}) }
        return { values: store.settings, definitions: fixtures.settings.definitions }
      },
    ],
    [method === 'GET' && pathname === '/sets', () => fixtures.sets],
    [method === 'GET' && pathname === '/variants', () => fixtures.variants],
    [method === 'GET' && pathname === '/groups', () => fixtures.groups],
    [method === 'GET' && pathname === '/grading/companies', () => store.companies],
    [
      method === 'GET' && pathname === '/grading/rules',
      () =>
        DEMO_RULES.map((rule) => ({
          id: rule.code,
          code: rule.code,
          label: rule.label,
          company_id: null,
          company_code: null,
          field: rule.field,
          face: 'any',
          min_severity: rule.minSeverity,
          max_grade: rule.maxGrade ?? null,
          probability_multiplier: rule.multiplier ?? null,
          penalty_from_grade: rule.fromGrade ?? null,
          notes: null,
          is_builtin: true,
          active: rule.active !== false,
          sort_order: 100,
        })),
    ],
    [
      method === 'PATCH' && at(0) === 'grading' && at(1) === 'rules',
      () => {
        const rule = DEMO_RULES.find((item) => item.code === at(2))
        if (!rule) return fail('not_found', `Grade rule '${at(2)}' was not found.`, 404)
        const changes = payload as { max_grade?: number; active?: boolean }
        if (changes.max_grade !== undefined) rule.maxGrade = changes.max_grade
        if (changes.active !== undefined) rule.active = changes.active
        return { ...rule, id: rule.code }
      },
    ],
    [method === 'GET' && pathname === '/selling-profiles', () => fixtures.sellingProfiles],
    [method === 'GET' && pathname === '/data-sources', () => demoSources()],
    [
      method === 'PATCH' && at(0) === 'data-sources',
      () => {
        const source = demoSources().find((item) => item.code === at(1))
        if (!source) fail('not_found', `Data source '${at(1)}' was not found.`, 404)
        if (!source!.has_adapter && (payload as { enabled?: boolean }).enabled) {
          fail(
            'conflict',
            `'${source!.code}' has no adapter, so enabling it would do nothing. Sources without ` +
              'one are listed to show what is planned, not to be switched on.',
            409,
          )
        }
        // Recorded, but it changes nothing: there is no network in this tab to
        // enable. Saying so is better than a switch that appears to work.
        store.enabledSources[source!.code] = Boolean((payload as { enabled?: boolean }).enabled)
        return { ...source!, enabled: store.enabledSources[source!.code] }
      },
    ],
    [
      method === 'GET' && pathname === '/catalog/lookup',
      () => ({
        source_code: 'pokemontcg_io',
        source_name: 'Pokémon TCG API',
        query: params.get('name'),
        matches: [],
        status: 'unavailable',
        reason:
          'The demo runs entirely in this browser tab, so it cannot reach a card catalogue. ' +
          'Run SlabStack locally and enable a source to look cards up for real.',
      }),
    ],
    [
      method === 'POST' && pathname === '/market/refresh',
      () =>
        fail(
          'conflict',
          'The demo has no server and no network, so there is nothing to refresh from. This ' +
            'works in the real app: enable a data source, link a card to the catalogue, and ' +
            'prices are fetched into your own database.',
          409,
        ),
    ],
    [
      method === 'PATCH' && at(0) === 'grading' && at(1) === 'tiers',
      () => {
        for (const company of store.companies) {
          const tier = company.tiers.find((item) => item.id === at(2))
          if (tier) {
            Object.assign(tier, payload)
            return tier
          }
        }
        return fail('not_found', `Grading tier '${at(2)}' was not found.`, 404)
      },
    ],
    [
      method === 'GET' && pathname === '/submissions',
      () =>
        [...store.submissions]
          .sort((a, b) => b.created_at.localeCompare(a.created_at))
          .map(costed),
    ],
    [
      method === 'POST' && pathname === '/submissions',
      () => {
        const body = payload as Record<string, unknown>
        const companyId = String(body.company_id ?? '')
        if (!store.companies.some((company) => company.id === companyId)) {
          fail('not_found', `Grading company '${companyId}' was not found.`, 404)
        }
        const submission: StoredSubmission = {
          id: newId(),
          reference: nextReference(
            store.submissions.map((item) => item.reference),
            new Date().toISOString().slice(0, 10),
          ),
          name: (body.name as string) ?? null,
          company_id: companyId,
          tier_id: (body.tier_id as string) ?? null,
          status: 'draft',
          currency: (store.settings.currency as string) ?? 'GBP',
          cost_allocation_method:
            (body.cost_allocation_method as string) ??
            (store.settings.cost_allocation_method as string) ??
            'equal',
          shipping_out_minor: market.toMinor(Number(body.shipping_out ?? 0)),
          shipping_return_minor: market.toMinor(Number(body.shipping_return ?? 0)),
          handling_minor: market.toMinor(Number(body.handling ?? 0)),
          other_fees_minor: market.toMinor(Number(body.other_fees ?? 0)),
          membership_allocation_minor: 0,
          submitted_at: null,
          received_at: null,
          returned_at: null,
          tracking_outbound: null,
          tracking_return: null,
          notes: (body.notes as string) ?? null,
          created_at: nowIso(),
          cards: [],
        }
        const ids = (body.card_ids as string[]) ?? []
        ids.forEach((cardId, index) => {
          if (!store.cards.some((card) => card.id === cardId)) {
            fail('not_found', `Card '${cardId}' was not found.`, 404)
          }
          submission.cards.push(newLine(cardId, submission.tier_id, index, submission.company_id))
        })
        store.submissions.push(submission)
        return costed(submission)
      },
    ],
    [
      method === 'POST' && at(0) === 'submissions' && at(1) === 'optimise',
      () => {
        const candidates = store.cards
          .filter(
            (card) =>
              store.conditions.has(card.id) &&
              store.prices.some((price) => price.catalog_key === card.catalog_key),
          )
          .map((card) => card.id)
        const limit = Number(params.get('limit') ?? 150) || 150
        return optimise({
          candidates,
          totalCards: store.cards.length,
          companies: store.companies,
          currency: currency(),
          limit,
          evaluate: (cardId, batchSize) => {
            const card = store.cards.find((item) => item.id === cardId)!
            return evaluationFor(card, batchSize)
          },
        })
      },
    ],
    [
      method === 'GET' && at(0) === 'submissions' && segments.length === 2,
      () => costed(requireSubmission(at(1))),
    ],
    [
      method === 'PATCH' && at(0) === 'submissions' && segments.length === 2,
      () => {
        const submission = requireSubmission(at(1))
        const body = payload as Record<string, unknown>
        const statuses = new Set([
          'draft', 'planned', 'shipped', 'received', 'grading', 'returned', 'cancelled',
        ])
        if (body.status !== undefined && !statuses.has(String(body.status))) {
          fail('conflict', `'${body.status}' is not a submission status.`, 409)
        }
        if (
          body.cost_allocation_method !== undefined &&
          !['equal', 'value_weighted', 'custom'].includes(String(body.cost_allocation_method))
        ) {
          fail(
            'conflict',
            `'${body.cost_allocation_method}' is not a cost allocation method.`,
            409,
          )
        }
        for (const [key, column] of [
          ['shipping_out', 'shipping_out_minor'],
          ['shipping_return', 'shipping_return_minor'],
          ['handling', 'handling_minor'],
          ['other_fees', 'other_fees_minor'],
        ] as const) {
          if (body[key] !== undefined) {
            ;(submission as unknown as Record<string, unknown>)[column] = market.toMinor(
              Number(body[key] ?? 0),
            )
          }
        }
        for (const key of [
          'name', 'company_id', 'tier_id', 'status', 'cost_allocation_method',
          'submitted_at', 'received_at', 'returned_at', 'tracking_outbound',
          'tracking_return', 'notes',
        ] as const) {
          if (body[key] !== undefined) {
            ;(submission as unknown as Record<string, unknown>)[key] = body[key]
          }
        }
        return costed(submission)
      },
    ],
    [
      method === 'DELETE' && at(0) === 'submissions' && segments.length === 2,
      () => {
        const submission = requireSubmission(at(1))
        if (!['draft', 'cancelled'].includes(submission.status)) {
          fail(
            'conflict',
            'Only a draft or cancelled submission can be deleted. Cancel it instead, so the ' +
              'record of what you sent survives.',
            409,
            { status: submission.status },
          )
        }
        store.submissions = store.submissions.filter((item) => item.id !== submission.id)
        // undefined, not null: the dispatcher turns that into a 204, matching
        // the real API's empty response for a delete.
        return undefined
      },
    ],
    [
      method === 'POST' && at(0) === 'submissions' && at(2) === 'cards',
      () => {
        const submission = requireSubmission(at(1))
        requireEditable(submission)
        const ids = ((payload as Record<string, unknown>).card_ids as string[]) ?? []
        const tierId =
          ((payload as Record<string, unknown>).tier_id as string) ?? submission.tier_id
        const existing = new Set(submission.cards.map((line) => line.card_id))
        let order = Math.max(-1, ...submission.cards.map((line) => line.sort_order)) + 1
        for (const cardId of ids) {
          if (!store.cards.some((card) => card.id === cardId)) {
            fail('not_found', `Card '${cardId}' was not found.`, 404)
          }
          if (existing.has(cardId)) continue
          submission.cards.push(newLine(cardId, tierId, order, submission.company_id))
          order += 1
        }
        return costed(submission)
      },
    ],
    [
      method === 'PATCH' && at(0) === 'submissions' && at(2) === 'cards',
      () => {
        const submission = requireSubmission(at(1))
        const line = submission.cards.find((item) => item.id === at(3))
        if (!line) fail('not_found', `Submission card '${at(3)}' was not found.`, 404)
        const body = payload as Record<string, unknown>
        if (body.declared_value !== undefined) {
          line!.declared_value_minor =
            body.declared_value === null ? null : market.toMinor(Number(body.declared_value))
          line!.declared_value_source = 'user'
          line!.declared_value_confidence = 'high'
        }
        for (const key of ['tier_id', 'actual_grade', 'cert_number', 'status', 'notes'] as const) {
          if (body[key] !== undefined) {
            ;(line as unknown as Record<string, unknown>)[key] = body[key]
          }
        }
        if (body.sort_order !== undefined) line!.sort_order = Number(body.sort_order)
        return costed(submission)
      },
    ],
    [
      method === 'DELETE' && at(0) === 'submissions' && at(2) === 'cards',
      () => {
        const submission = requireSubmission(at(1))
        requireEditable(submission)
        const before = submission.cards.length
        submission.cards = submission.cards.filter((item) => item.id !== at(3))
        if (submission.cards.length === before) {
          fail('not_found', `Submission card '${at(3)}' was not found.`, 404)
        }
        return costed(submission)
      },
    ],
    [method === 'GET' && pathname === '/collection/facets', () => facets()],
    [
      method === 'GET' && pathname === '/collection/decisions',
      () => collectionDecisions(Number(params.get('batch_size') ?? 1) || 1),
    ],
    [
      method === 'GET' && pathname === '/analytics/opportunities',
      () => rankOpportunities(collectionDecisions(Number(params.get('batch_size') ?? 1) || 1)),
    ],
    [method === 'GET' && pathname === '/analytics/selling-queue', () => sellingQueue()],
    [method === 'GET' && pathname === '/analytics/submission-returns', () => submissionReturns()],
    [method === 'GET' && pathname === '/analytics/filters', () => FILTERS],
    [
      method === 'GET' && pathname === '/analytics/accuracy',
      () =>
        buildAccuracyReport(
          resultRows(),
          store.companies,
          calibrationOptions().minimumSample,
          unscoreableCount(),
        ),
    ],
    [method === 'GET' && pathname === '/calibration', () => calibrationState()],
    [
      method === 'GET' && at(0) === 'analytics' && at(1) === 'filters' && segments.length === 3,
      () => {
        try {
          return applyFilter(
            at(2),
            collectionDecisions(Number(params.get('batch_size') ?? 1) || 1),
            minimumLiquidity(),
          )
        } catch {
          return fail(
            'not_found',
            `'${at(2)}' is not a collection filter. Available: ${FILTERS.map((f) => f.key).join(', ')}.`,
            404,
            { key: at(2), available: FILTERS.map((f) => f.key) },
          )
        }
      },
    ],
    [
      method === 'GET' && pathname === '/collection/summary',
      () =>
        buildSummary(
          store.cards,
          store.conditions,
          store.companies,
          currency(),
          store.prices,
          store.sales.filter((sale) => !sale.is_excluded).length,
        ),
    ],
    [method === 'GET' && pathname === '/cards', () => listCards(params)],
    [
      method === 'POST' && pathname === '/cards',
      () => {
        const card = blankCard(payload as unknown as Partial<Card> & { name: string })
        store.cards.push(card)
        return card
      },
    ],
    [
      method === 'GET' && at(0) === 'cards' && segments.length === 2,
      () => requireCard(at(1)),
    ],
    [
      method === 'PATCH' && at(0) === 'cards' && segments.length === 2,
      () => applyCardPayload(requireCard(at(1)), payload),
    ],
    [
      method === 'DELETE' && at(0) === 'cards' && segments.length === 2,
      () => {
        store.cards = store.cards.filter((card) => card.id !== at(1))
        store.conditions.delete(at(1))
        return undefined
      },
    ],
    [
      method === 'POST' && at(0) === 'cards' && at(2) === 'split',
      () => {
        const card = requireCard(at(1))
        if (card.quantity <= 1) {
          fail(
            'cannot_split',
            'This card is already a single copy. Grading decisions are made per physical card.',
          )
        }
        const requested = (payload as { count?: number }).count
        const toSplit = Math.min(requested ?? card.quantity, card.quantity)
        const created: Card[] = []
        for (let i = 0; i < toSplit - 1; i++) {
          const clone = blankCard({ ...card, quantity: 1 })
          clone.images = []
          clone.primary_image_url = null
          clone.has_condition_assessment = false
          store.cards.push(clone)
          created.push(clone)
        }
        card.quantity -= toSplit - 1
        return [card, ...created]
      },
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'evaluation',
      () => evaluationFor(requireCard(at(1)), Number(params.get('batch_size') ?? 1) || 1),
    ],

    // --- Market ------------------------------------------------------------
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'market' && segments.length === 3,
      () => summaryFor(requireCard(at(1))),
    ],
    [
      method === 'POST' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'recompute',
      () => {
        const card = requireCard(at(1))
        repriceKey(card.catalog_key)
        return summaryFor(card)
      },
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'sales',
      () => {
        const card = requireCard(at(1))
        const includeExcluded = params.get('include_excluded') !== 'false'
        return store.sales
          .filter((sale) => sale.catalog_key === card.catalog_key)
          .filter((sale) => includeExcluded || !sale.is_excluded)
          .sort((a, b) => b.sale_date.localeCompare(a.sale_date))
      },
    ],
    [
      method === 'POST' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'sales',
      () => createSale(requireCard(at(1)), body),
    ],
    [
      method === 'POST' &&
        at(0) === 'cards' &&
        at(2) === 'market' &&
        at(3) === 'sales' &&
        at(4) === 'import',
      () => importSales(requireCard(at(1)), body),
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'history',
      // The demo store is rebuilt on every load, so there is no history to
      // show. Saying so beats an empty chart that looks like a flat market.
      () => [],
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'listings',
      () => [],
    ],
    [
      method === 'POST' && at(0) === 'cards' && at(2) === 'market' && at(3) === 'reclassify',
      () => reclassify(requireCard(at(1))),
    ],
    [
      method === 'PUT' && at(0) === 'market' && at(1) === 'sales' && at(3) === 'exclusion',
      () => setExclusion(at(2), body),
    ],
    [
      method === 'DELETE' && at(0) === 'market' && at(1) === 'sales' && segments.length === 3,
      () => {
        const index = store.sales.findIndex((sale) => sale.id === at(2))
        if (index < 0) fail('not_found', `Sale '${at(2)}' was not found.`, 404)
        const [removed] = store.sales.splice(index, 1)
        repriceKey(removed.catalog_key)
        return null
      },
    ],
    [
      method === 'PUT' && at(0) === 'market' && at(1) === 'prices' && at(3) === 'override',
      () => {
        const price = store.prices.find((row) => row.id === at(2))
        if (!price) fail('not_found', `Market price '${at(2)}' was not found.`, 404)
        price!.user_value = (body.value as number | null) ?? null
        price!.user_value_note = (body.note as string | null) ?? null
        return price
      },
    ],
    [
      method === 'GET' && pathname === '/market/prices',
      () =>
        store.prices.filter((price) => price.catalog_key === params.get('catalog_key')),
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'images',
      () => requireCard(at(1)).images,
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'condition',
      () => {
        const assessment = store.conditions.get(at(1))
        if (!assessment) {
          fail('not_found', `Condition assessment for card '${at(1)}' was not found.`, 404)
        }
        return assessment
      },
    ],
    [
      method === 'PUT' && at(0) === 'cards' && at(2) === 'condition',
      () => {
        const card = requireCard(at(1))
        const assessment = buildAssessment(card.id, payload as unknown as ConditionWrite)
        store.conditions.set(card.id, assessment)
        card.has_condition_assessment = true
        return assessment
      },
    ],
    [
      method === 'GET' && at(0) === 'cards' && at(2) === 'condition' && at(3) === 'history',
      () => {
        const assessment = store.conditions.get(at(1))
        return assessment ? [assessment] : []
      },
    ],
  ]

  for (const [matches, handler] of routes) {
    if (matches) return handler()
  }

  return fail('not_found', `No API route matches ${pathname} in the demo.`, 404)
}

// --- Market ------------------------------------------------------------------

function saleContext(card: Card) {
  return { language: card.language, variant: card.variant, printing: card.printing }
}

function createSale(card: Card, body: Record<string, unknown>): MarketSale {
  if (!card.catalog_key) {
    fail('no_catalog_key', 'This card has no catalog key, so its sales cannot be matched.')
  }
  const companyId = (body.company_id as string | null) ?? null
  const grade = body.grade === null || body.grade === undefined ? null : Number(body.grade)
  const company = companyId ? store.companies.find((item) => item.id === companyId) : undefined
  if (companyId && grade === null) {
    fail(
      'missing_grade',
      'A grading company was given without a grade. A slab is a company and a number; either ' +
        'send both or send neither for a raw sale.',
    )
  }
  const label = company ? market.gradeLabel(company.code, grade) : 'raw'

  const sale = makeSale(card, {
    id: newId(),
    company_id: company?.id ?? null,
    grade: company ? grade : null,
    grade_label: label,
    platform: (body.platform as string) ?? null,
    sale_date: (body.sale_date as string) ?? nowIso().slice(0, 10),
    sale_price: Number(body.sale_price ?? 0),
    currency: (body.currency as string) ?? currency(),
    shipping: (body.shipping as number | null) ?? null,
    listing_title: (body.listing_title as string) ?? null,
    source_url: (body.source_url as string) ?? null,
    seller: (body.seller as string) ?? null,
    lot_size: Number(body.lot_size ?? 1),
    condition_note: (body.condition_note as string) ?? null,
    external_id: null,
  })

  if (body.apply_filters !== false) {
    const verdict = market.classify(sale.listing_title, saleContext(card), sale.lot_size, label)
    if (verdict) {
      sale.is_excluded = true
      sale.exclusion_reason = verdict.reason
      sale.excluded_by = 'system'
    }
  }

  store.sales.push(sale)
  repriceKey(card.catalog_key)
  return sale
}

function importSales(card: Card, body: Record<string, unknown>): ImportResult {
  if (!card.catalog_key) {
    fail('no_catalog_key', 'This card has no catalog key, so its sales cannot be matched.')
  }
  const parsed = market.parseCsv(String(body.csv ?? ''), body.day_first !== false)
  const applyFilters = body.apply_filters !== false
  const result = market.blankImportResult()
  result.errors = parsed.errors

  for (const row of parsed.rows) {
    // Deduplicated on external id, exactly as the server does on
    // (source_id, external_id): re-importing an overlapping export updates.
    const existing = row.externalId
      ? store.sales.find(
          (sale) => sale.external_id === row.externalId && sale.source_id === 'demo-csv',
        )
      : undefined

    let code = row.companyCode
    let grade = row.grade
    if (!(code && grade !== null)) {
      const fromTitle = market.parseGradeFromTitle(row.listingTitle)
      if (fromTitle) [code, grade] = fromTitle
    }
    const company = code ? store.companies.find((item) => item.code === code) : undefined
    const label = company && grade !== null ? market.gradeLabel(company.code, grade) : 'raw'

    const sale =
      existing ??
      makeSale(card, { id: newId(), external_id: row.externalId, source_id: 'demo-csv' })
    Object.assign(sale, {
      catalog_key: card.catalog_key,
      company_id: company?.id ?? null,
      grade: company ? grade : null,
      grade_label: label,
      platform: row.platform,
      sale_date: row.saleDate,
      sale_price: market.toMajor(row.salePriceMinor)!,
      currency: row.currency ?? currency(),
      shipping: market.toMajor(row.shippingMinor),
      listing_title: row.listingTitle,
      source_url: row.sourceUrl,
      seller: row.seller,
      lot_size: row.lotSize,
      condition_note: row.conditionNote,
      source_id: 'demo-csv',
      external_id: row.externalId,
    })

    // A user's decision is a decision: re-importing must not overwrite it.
    if (applyFilters && sale.excluded_by !== 'user') {
      const verdict = market.classify(sale.listing_title, saleContext(card), sale.lot_size, label)
      if (verdict) {
        sale.is_excluded = true
        sale.exclusion_reason = verdict.reason
        sale.excluded_by = 'system'
        result.excluded += 1
        result.exclusions[verdict.reason] = (result.exclusions[verdict.reason] ?? 0) + 1
      } else {
        sale.is_excluded = false
        sale.exclusion_reason = null
        sale.excluded_by = null
      }
    }

    if (existing) result.updated += 1
    else {
      store.sales.push(sale)
      result.imported += 1
    }
  }

  const before = store.sales.filter((sale) => sale.is_outlier).length
  result.prices = repriceKey(card.catalog_key)
  result.outliers_flagged = Math.max(
    0,
    store.sales.filter((sale) => sale.is_outlier).length - before,
  )
  return result
}

function setExclusion(saleId: string, body: Record<string, unknown>): MarketSale {
  const sale = store.sales.find((item) => item.id === saleId)
  if (!sale) fail('not_found', `Sale '${saleId}' was not found.`, 404)
  sale!.is_excluded = Boolean(body.excluded)
  sale!.excluded_by = 'user'
  if (sale!.is_excluded) {
    sale!.exclusion_reason = (body.reason as MarketSale['exclusion_reason']) ?? 'user_excluded'
  } else {
    sale!.exclusion_reason = null
    sale!.is_outlier = false
  }
  repriceKey(sale!.catalog_key)
  return sale!
}

function reclassify(card: Card): Record<string, number> {
  const counts = { kept: 0, excluded: 0, unchanged: 0, skipped_user: 0, outliers_flagged: 0, outliers_cleared: 0 }
  for (const sale of store.sales.filter((item) => item.catalog_key === card.catalog_key)) {
    if (sale.excluded_by === 'user') {
      counts.skipped_user += 1
      continue
    }
    const was = sale.is_excluded
    if (sale.exclusion_reason === 'price_outlier') {
      counts.unchanged += 1
      continue
    }
    const verdict = market.classify(
      sale.listing_title,
      saleContext(card),
      sale.lot_size,
      sale.grade_label,
    )
    sale.is_excluded = Boolean(verdict)
    sale.exclusion_reason = verdict?.reason ?? null
    sale.excluded_by = verdict ? 'system' : null
    if (sale.is_excluded === was) counts.unchanged += 1
    else if (sale.is_excluded) counts.excluded += 1
    else counts.kept += 1
  }
  repriceKey(card.catalog_key)
  return counts
}

// --- Images ------------------------------------------------------------------

function uploadImages(cardId: string, form: FormData): CardImage[] {
  const card = requireCard(cardId)
  const side = (form.get('side') as string) ?? 'front'
  const files = form.getAll('files').filter((entry): entry is File => entry instanceof File)

  const stored: CardImage[] = []
  for (const file of files) {
    if (!file.type.startsWith('image/')) {
      fail('invalid_image', 'That file is not a readable image.', 400, { filename: file.name })
    }
    // Object URLs live for the life of the tab, which is exactly as long as the
    // rest of the demo's data.
    const url = URL.createObjectURL(file)
    const image: CardImage = {
      id: newId(),
      card_id: card.id,
      side: side as CardImage['side'],
      url,
      thumbnail_url: url,
      original_filename: file.name,
      mime_type: file.type,
      width: null,
      height: null,
      size_bytes: file.size,
      is_primary: !card.images.some((existing) => existing.side === side),
      sort_order: card.images.filter((existing) => existing.side === side).length,
      caption: null,
      created_at: nowIso(),
    }
    card.images.push(image)
    stored.push(image)
  }
  refreshPrimaryImage(card)
  return stored
}

function updateImage(imageId: string, payload: Record<string, unknown>): CardImage {
  for (const card of store.cards) {
    const image = card.images.find((item) => item.id === imageId)
    if (!image) continue
    if (payload.is_primary) {
      for (const sibling of card.images.filter((item) => item.side === image.side)) {
        sibling.is_primary = sibling.id === image.id
      }
    }
    Object.assign(image, payload)
    refreshPrimaryImage(card)
    return image
  }
  return fail('not_found', `Image '${imageId}' was not found.`, 404) as never
}

function deleteImage(imageId: string): void {
  for (const card of store.cards) {
    const index = card.images.findIndex((item) => item.id === imageId)
    if (index === -1) continue
    const [removed] = card.images.splice(index, 1)
    if (removed.is_primary) {
      const replacement = card.images.find((item) => item.side === removed.side)
      if (replacement) replacement.is_primary = true
    }
    refreshPrimaryImage(card)
    return
  }
  fail('not_found', `Image '${imageId}' was not found.`, 404)
}

// --- Entry point -------------------------------------------------------------

export interface DemoResponse {
  ok: boolean
  status: number
  body: unknown
}

/** Same signature the real `request()` needs: a path and a fetch init. */
export async function demoRequest(path: string, init?: RequestInit): Promise<DemoResponse> {
  // A touch of latency so loading states are visible, as they are against a
  // real server.
  await new Promise((resolve) => setTimeout(resolve, 90))

  const [rawPath, rawQuery] = path.split('?')
  const params = new URLSearchParams(rawQuery ?? '')
  const method = (init?.method ?? 'GET').toUpperCase()

  try {
    const segments = rawPath.split('/').filter(Boolean)

    if (init?.body instanceof FormData) {
      if (segments[0] === 'cards' && segments[2] === 'images') {
        return { ok: true, status: 201, body: uploadImages(segments[1], init.body) }
      }
      return { ok: false, status: 400, body: { error: { code: 'bad_request', message: 'Unsupported upload.' } } }
    }

    if (segments[0] === 'images' && segments.length === 2) {
      const body = init?.body ? JSON.parse(init.body as string) : {}
      if (method === 'PATCH') return { ok: true, status: 200, body: updateImage(segments[1], body) }
      if (method === 'DELETE') {
        deleteImage(segments[1])
        return { ok: true, status: 204, body: undefined }
      }
    }

    const body = init?.body ? JSON.parse(init.body as string) : undefined
    const result = route(method, rawPath, params, body)
    return { ok: true, status: result === undefined ? 204 : 200, body: result }
  } catch (error) {
    if (error instanceof DemoFailure) {
      const { code, message, status, details } = error.payload
      return { ok: false, status, body: { error: { code, message, details: details ?? null } } }
    }
    return {
      ok: false,
      status: 500,
      body: { error: { code: 'demo_error', message: String(error), details: null } },
    }
  }
}
