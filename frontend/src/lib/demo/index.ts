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
  buildSummary,
} from './engine'
import * as market from './market'
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

  const seeded: Store = { cards, conditions, companies, settings, sales, prices: [], listings }
  const params = market.paramsFromSettings(settings)
  const money = (settings.currency as string) ?? 'GBP'
  for (const key of new Set(sales.map((sale) => sale.catalog_key))) {
    market.markOutliers(
      sales.filter((sale) => sale.catalog_key === key),
      params,
    )
    market.recompute(key, sales, seeded.prices, params, money)
  }
  return seeded
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
  )
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
        phase: '3 — market data (demo)',
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
    [method === 'GET' && pathname === '/data-sources', () => fixtures.dataSources],
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
    [method === 'GET' && pathname === '/collection/facets', () => facets()],
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
