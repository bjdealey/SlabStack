import type {
  AccuracyReport,
  ApiErrorBody,
  Card,
  CardEvaluation,
  CardImage,
  CardSet,
  CardVariant,
  CardWrite,
  CalibrationState,
  CatalogLookup,
  CollectionDecisions,
  CollectionFilter,
  CollectionSummary,
  ConditionAssessment,
  ConditionWrite,
  DataSource,
  EnumsResponse,
  Facets,
  FilterResult,
  GradeRule,
  GradingCompany,
  Group,
  HealthResponse,
  ImportResult,
  MarketPrice,
  Listing,
  MarketSale,
  MarketSummary,
  Page,
  RankedOpportunities,
  SaleWrite,
  SellingProfile,
  SellingQueue,
  OptimiserPlan,
  SettingsResponse,
  LinkReport,
  SnapshotSeries,
  SourceState,
  Submission,
  SubmissionReturns,
  SubmissionWrite,
  SyncReport,
} from './types'

const BASE = '/api'

/**
 * The GitHub Pages demo has no server behind it, so requests are answered by an
 * in-browser stand-in. This is a compile-time constant, so a normal build drops
 * the branch and never bundles the demo code.
 */
const DEMO = import.meta.env.VITE_DEMO === 'true'

/** An error that carries the API's structured code, so callers can branch. */
export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: Record<string, unknown> | null

  constructor(
    code: string,
    message: string,
    status: number,
    details?: Record<string, unknown> | null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }

  /** True for endpoints whose engine ships in a later phase. */
  get isNotImplemented() {
    return this.code === 'not_implemented'
  }

  get phase(): number | null {
    const phase = this.details?.phase
    return typeof phase === 'number' ? phase : null
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (DEMO) {
    const { demoRequest } = await import('./demo')
    const result = await demoRequest(path, init)
    if (!result.ok) {
      const error = (result.body as ApiErrorBody | null)?.error
      throw new ApiError(
        error?.code ?? 'unknown_error',
        error?.message ?? `Request failed with status ${result.status}`,
        result.status,
        error?.details,
      )
    }
    return result.body as T
  }

  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers:
        init?.body instanceof FormData
          ? init?.headers
          : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    })
  } catch {
    throw new ApiError(
      'network_error',
      'Could not reach the SlabStack API. Is the backend running on port 8000?',
      0,
    )
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const body = text ? JSON.parse(text) : null

  if (!response.ok) {
    const error = (body as ApiErrorBody | null)?.error
    throw new ApiError(
      error?.code ?? 'unknown_error',
      error?.message ?? `Request failed with status ${response.status}`,
      response.status,
      error?.details,
    )
  }
  return body as T
}

type QueryValue = string | number | boolean | string[] | null | undefined

function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item))
    else search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export interface CardListParams {
  q?: string
  set_code?: string
  language?: string
  variant?: string
  rarity?: string
  status?: string[]
  is_promo?: boolean
  group_id?: string
  has_images?: boolean
  has_condition?: boolean
  sort?: string
  order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  enums: () => request<EnumsResponse>('/meta/enums'),

  // --- Cards ---------------------------------------------------------------
  listCards: (params: CardListParams = {}) =>
    request<Page<Card>>(`/cards${query(params as Record<string, QueryValue>)}`),
  getCard: (id: string) => request<Card>(`/cards/${id}`),
  createCard: (payload: CardWrite) =>
    request<Card>('/cards', { method: 'POST', body: JSON.stringify(payload) }),
  updateCard: (id: string, payload: Partial<CardWrite>) =>
    request<Card>(`/cards/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteCard: (id: string) => request<void>(`/cards/${id}`, { method: 'DELETE' }),
  splitCard: (id: string, count?: number) =>
    request<Card[]>(`/cards/${id}/split`, {
      method: 'POST',
      body: JSON.stringify(count ? { count } : {}),
    }),
  evaluateCard: (id: string, batchSize = 1) =>
    request<CardEvaluation>(`/cards/${id}/evaluation${query({ batch_size: batchSize })}`),

  // --- Images --------------------------------------------------------------
  uploadImages: (cardId: string, files: File[], side: string) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    form.append('side', side)
    return request<CardImage[]>(`/cards/${cardId}/images`, { method: 'POST', body: form })
  },
  updateImage: (imageId: string, payload: Partial<Pick<CardImage, 'side' | 'caption' | 'is_primary' | 'sort_order'>>) =>
    request<CardImage>(`/images/${imageId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteImage: (imageId: string) => request<void>(`/images/${imageId}`, { method: 'DELETE' }),

  // --- Condition -----------------------------------------------------------
  getCondition: (cardId: string) => request<ConditionAssessment>(`/cards/${cardId}/condition`),
  putCondition: (cardId: string, payload: ConditionWrite) =>
    request<ConditionAssessment>(`/cards/${cardId}/condition`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  conditionHistory: (cardId: string) =>
    request<ConditionAssessment[]>(`/cards/${cardId}/condition/history`),

  // --- Collection ----------------------------------------------------------
  summary: () => request<CollectionSummary>('/collection/summary'),
  facets: () => request<Facets>('/collection/facets'),
  // Separate from the summary on purpose: this one runs the engine over every
  // ready card, so the dashboard renders first and this arrives when it can.
  collectionDecisions: (batchSize = 1) =>
    request<CollectionDecisions>(`/collection/decisions${query({ batch_size: batchSize })}`),

  // --- Submissions ---------------------------------------------------------
  listSubmissions: () => request<Submission[]>('/submissions'),
  getSubmission: (id: string) => request<Submission>(`/submissions/${id}`),
  createSubmission: (payload: SubmissionWrite & { company_id: string; card_ids?: string[] }) =>
    request<Submission>('/submissions', { method: 'POST', body: JSON.stringify(payload) }),
  updateSubmission: (id: string, payload: SubmissionWrite) =>
    request<Submission>(`/submissions/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteSubmission: (id: string) =>
    request<void>(`/submissions/${id}`, { method: 'DELETE' }),
  addSubmissionCards: (id: string, cardIds: string[], tierId?: string | null) =>
    request<Submission>(`/submissions/${id}/cards`, {
      method: 'POST',
      body: JSON.stringify({ card_ids: cardIds, tier_id: tierId ?? null }),
    }),
  updateSubmissionCard: (
    id: string,
    lineId: string,
    payload: {
      tier_id?: string | null
      declared_value?: number | null
      actual_grade?: number | null
      cert_number?: string | null
      status?: string | null
      notes?: string | null
    },
  ) =>
    request<Submission>(`/submissions/${id}/cards/${lineId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  removeSubmissionCard: (id: string, lineId: string) =>
    request<Submission>(`/submissions/${id}/cards/${lineId}`, { method: 'DELETE' }),
  optimiseSubmissions: (limit?: number) =>
    request<OptimiserPlan>(`/submissions/optimise${query({ limit })}`, { method: 'POST' }),

  // --- Analytics -----------------------------------------------------------
  // Every one of these is a projection of an answer another engine already
  // gave. Nothing here computes a verdict, a value or a cost of its own.
  rankedOpportunities: (batchSize = 1) =>
    request<RankedOpportunities>(`/analytics/opportunities${query({ batch_size: batchSize })}`),
  sellingQueue: () => request<SellingQueue>('/analytics/selling-queue'),
  submissionReturns: () => request<SubmissionReturns>('/analytics/submission-returns'),
  collectionFilters: () => request<CollectionFilter[]>('/analytics/filters'),
  applyFilter: (key: string, batchSize = 1) =>
    request<FilterResult>(`/analytics/filters/${key}${query({ batch_size: batchSize })}`),

  // --- Learning ------------------------------------------------------------
  accuracy: () => request<AccuracyReport>('/analytics/accuracy'),
  calibration: () => request<CalibrationState>('/calibration'),

  // --- Reference data ------------------------------------------------------
  listSets: (q?: string) => request<CardSet[]>(`/sets${query({ q })}`),
  listVariants: () => request<CardVariant[]>('/variants'),
  listGroups: () => request<Group[]>('/groups'),
  createGroup: (payload: { name: string; description?: string; color?: string }) =>
    request<Group>('/groups', { method: 'POST', body: JSON.stringify(payload) }),
  addCardsToGroup: (groupId: string, cardIds: string[]) =>
    request<{ ok: boolean; message: string | null }>(`/groups/${groupId}/cards`, {
      method: 'POST',
      body: JSON.stringify({ card_ids: cardIds }),
    }),

  // --- Predictions ---------------------------------------------------------
  runPrediction: (cardId: string) =>
    request<unknown[]>(`/cards/${cardId}/grade-prediction`, { method: 'POST' }),

  // --- Market --------------------------------------------------------------
  cardMarket: (cardId: string) => request<MarketSummary>(`/cards/${cardId}/market`),
  cardSales: (cardId: string, includeExcluded = true) =>
    request<MarketSale[]>(
      `/cards/${cardId}/market/sales${query({ include_excluded: includeExcluded })}`,
    ),
  cardListings: (cardId: string) => request<Listing[]>(`/cards/${cardId}/market/listings`),
  createSale: (cardId: string, payload: SaleWrite) =>
    request<MarketSale>(`/cards/${cardId}/market/sales`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteSale: (saleId: string) => request<void>(`/market/sales/${saleId}`, { method: 'DELETE' }),
  setSaleExclusion: (saleId: string, excluded: boolean, reason?: string) =>
    request<MarketSale>(`/market/sales/${saleId}/exclusion`, {
      method: 'PUT',
      body: JSON.stringify({ excluded, reason: reason ?? null }),
    }),
  importSales: (cardId: string, csv: string, options: { day_first?: boolean } = {}) =>
    request<ImportResult>(`/cards/${cardId}/market/sales/import`, {
      method: 'POST',
      body: JSON.stringify({ csv, day_first: options.day_first ?? true }),
    }),
  recomputeMarket: (cardId: string) =>
    request<MarketSummary>(`/cards/${cardId}/market/recompute`, { method: 'POST' }),
  reclassifySales: (cardId: string) =>
    request<Record<string, number>>(`/cards/${cardId}/market/reclassify`, { method: 'POST' }),
  marketHistory: (cardId: string, days = 365) =>
    request<SnapshotSeries[]>(`/cards/${cardId}/market/history${query({ days })}`),
  overridePrice: (priceId: string, value: number | null, note?: string | null) =>
    request<MarketPrice>(`/market/prices/${priceId}/override`, {
      method: 'PUT',
      body: JSON.stringify({ value, note: note ?? null }),
    }),

  // --- Configuration -------------------------------------------------------
  listGradingCompanies: () => request<GradingCompany[]>('/grading/companies'),
  updateCompany: (companyId: string, payload: Record<string, unknown>) =>
    request<GradingCompany>(`/grading/companies/${companyId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  listGradeRules: () => request<GradeRule[]>('/grading/rules'),
  updateGradeRule: (ruleId: string, payload: Record<string, unknown>) =>
    request<GradeRule>(`/grading/rules/${ruleId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  updateTier: (tierId: string, payload: Record<string, unknown>) =>
    request<unknown>(`/grading/tiers/${tierId}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  listSellingProfiles: () => request<SellingProfile[]>('/selling-profiles'),
  listDataSources: () => request<DataSource[]>('/data-sources'),

  // --- Live market data ----------------------------------------------------
  // The only calls here that make the server reach the internet.
  updateDataSource: (code: string, payload: { enabled?: boolean; config?: Record<string, unknown> }) =>
    request<SourceState>(`/data-sources/${code}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  lookupCard: (params: {
    name?: string
    set_code?: string
    card_number?: string
    /** Which provider's catalogue to search. Defaults to the card catalogue. */
    source_code?: string
  }) =>
    request<CatalogLookup>(`/catalog/lookup${query(params)}`),
  linkCard: (
    cardId: string,
    payload: {
      external_id: string
      source_code?: string
      apply_fields?: string[]
      set_code?: string | null
      set_name?: string | null
      card_number?: string | null
      rarity?: string | null
    },
  ) =>
    request<{ card_id: string; applied_fields: string[]; catalog_key: string | null }>(
      `/cards/${cardId}/catalog-link`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  linkAll: (params: { source_code?: string; dry_run?: boolean; relink?: boolean; limit?: number }) =>
    request<LinkReport>(`/catalog/link-all${query(params as Record<string, string | undefined>)}`, {
      method: 'POST',
    }),
  refreshMarket: (cardId?: string) =>
    request<SyncReport[]>(`/market/refresh${query({ card_id: cardId })}`, { method: 'POST' }),
  getSettings: () => request<SettingsResponse>('/settings'),
  updateSettings: (values: Record<string, unknown>) =>
    request<SettingsResponse>('/settings', { method: 'PATCH', body: JSON.stringify({ values }) }),
}

/** Query keys, kept in one place so invalidation cannot drift from fetching. */
export const keys = {
  health: ['health'] as const,
  enums: ['enums'] as const,
  cards: (params: CardListParams) => ['cards', params] as const,
  card: (id: string) => ['card', id] as const,
  evaluation: (id: string, batchSize = 1) => ['evaluation', id, batchSize] as const,
  condition: (id: string) => ['condition', id] as const,
  market: (id: string) => ['market', id] as const,
  sales: (id: string) => ['sales', id] as const,
  listings: (id: string) => ['listings', id] as const,
  marketHistory: (id: string) => ['market-history', id] as const,
  submissions: ['submissions'] as const,
  submission: (id: string) => ['submission', id] as const,
  optimiserPlan: (limit?: number) => ['optimiser-plan', limit ?? null] as const,
  summary: ['summary'] as const,
  collectionDecisions: (batchSize = 1) => ['collection-decisions', batchSize] as const,
  rankedOpportunities: (batchSize = 1) => ['ranked-opportunities', batchSize] as const,
  sellingQueue: ['selling-queue'] as const,
  submissionReturns: ['submission-returns'] as const,
  collectionFilters: ['collection-filters'] as const,
  accuracy: ['accuracy'] as const,
  calibration: ['calibration'] as const,
  filterResult: (key: string, batchSize = 1) => ['filter-result', key, batchSize] as const,
  facets: ['facets'] as const,
  sets: (q?: string) => ['sets', q ?? ''] as const,
  variants: ['variants'] as const,
  groups: ['groups'] as const,
  companies: ['grading-companies'] as const,
  gradeRules: ['grade-rules'] as const,
  sellingProfiles: ['selling-profiles'] as const,
  dataSources: ['data-sources'] as const,
  catalogLookup: (params: Record<string, string | undefined>) => ['catalog-lookup', params] as const,
  settings: ['settings'] as const,
}
