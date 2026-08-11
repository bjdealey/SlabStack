import type {
  ApiErrorBody,
  Card,
  CardEvaluation,
  CardImage,
  CardSet,
  CardVariant,
  CardWrite,
  CollectionSummary,
  ConditionAssessment,
  ConditionWrite,
  DataSource,
  EnumsResponse,
  Facets,
  GradeRule,
  GradingCompany,
  Group,
  HealthResponse,
  Page,
  SellingProfile,
  SettingsResponse,
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
  evaluateCard: (id: string) => request<CardEvaluation>(`/cards/${id}/evaluation`),

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
  evaluation: (id: string) => ['evaluation', id] as const,
  condition: (id: string) => ['condition', id] as const,
  summary: ['summary'] as const,
  facets: ['facets'] as const,
  sets: (q?: string) => ['sets', q ?? ''] as const,
  variants: ['variants'] as const,
  groups: ['groups'] as const,
  companies: ['grading-companies'] as const,
  gradeRules: ['grade-rules'] as const,
  sellingProfiles: ['selling-profiles'] as const,
  dataSources: ['data-sources'] as const,
  settings: ['settings'] as const,
}
