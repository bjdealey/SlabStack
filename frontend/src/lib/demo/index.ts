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
  Page,
} from '@/lib/types'
import fixtures from './fixtures.json'
import { buildCatalogKey, buildEvaluation, buildSummary, buildAssessment } from './engine'
import { SEED_CARDS } from './seed'

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

  return {
    cards,
    conditions,
    companies: structuredClone(fixtures.gradingCompanies) as GradingCompany[],
    settings: structuredClone(fixtures.settings.values) as Record<string, unknown>,
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

function evaluationFor(card: Card): CardEvaluation {
  return buildEvaluation(card, store.conditions.get(card.id), store.companies, currency(), 0)
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
function route(method: string, pathname: string, params: URLSearchParams, body: unknown): unknown {
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
        phase: '1 — foundation (demo)',
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
      () => buildSummary(store.cards, store.conditions, store.companies, currency()),
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
      () => evaluationFor(requireCard(at(1))),
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
