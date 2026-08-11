/**
 * Ports of the server's Phase 1 logic, for the GitHub Pages demo only.
 *
 * This is the one place in the codebase that duplicates engine logic, and it
 * exists because GitHub Pages serves static files: there is no Python process
 * and no writable SQLite behind the demo. Everything here is a faithful port of
 * `backend/app/services/`, kept deliberately small.
 *
 * It is never bundled into the real app — `VITE_DEMO` gates the import, so a
 * normal build tree-shakes the whole folder away. The real application always
 * gets these numbers from the API.
 */

import type {
  Card,
  CardEvaluation,
  CollectionSummary,
  CompanyGradePrediction,
  ConditionAssessment,
  ConditionWrite,
  ExplanationItem,
  FaceDefects,
  GradingCompany,
  GradingOption,
  MarketBlock,
  MarketPrice,
  MarketSummary,
  CompanyBestCase,
  GradingOptionsBlock,
  MarketValueRow,
  NetValueRow,
  SellingProfile,
  Severity,
  TrendBlock,
} from '@/lib/types'
import { formatMoney } from '@/lib/utils'
import type { DeclaredValue, SubmissionAssumptions } from './economics'
import {
  assumptionsFrom,
  costForTier,
  eligibleTiers,
  netByGrade,
  netSaleValue,
  suggestDeclaredValue,
} from './economics'
import { premiumVsRawPct, toMajor, toMinor } from './market'

export const DEFECT_FIELDS = [
  'corner_tl',
  'corner_tr',
  'corner_bl',
  'corner_br',
  'edge_condition',
  'surface_condition',
  'holo_condition',
  'scratches',
  'print_lines',
  'silvering',
  'whitening',
  'dents',
  'dimpling',
  'creases',
  'staining',
  'misc_defects',
] as const

const CORNER_GROUP = ['corner_tl', 'corner_tr', 'corner_bl', 'corner_br'] as const
const EDGE_GROUP = ['edge_condition', 'whitening', 'silvering'] as const
const SURFACE_GROUP = [
  'surface_condition',
  'holo_condition',
  'scratches',
  'print_lines',
  'dents',
  'dimpling',
  'creases',
  'staining',
  'misc_defects',
] as const

// Mirrors app/services/condition_service.py
const FRONT_CENTERING_ANCHORS: [number, number][] = [
  [50, 10],
  [52.5, 10],
  [55, 9],
  [60, 8],
  [65, 7],
  [70, 6],
  [75, 5],
  [85, 3],
  [100, 0],
]
const BACK_CENTERING_ANCHORS: [number, number][] = [
  [50, 10],
  [60, 10],
  [70, 9],
  [75, 8.5],
  [80, 7],
  [90, 4],
  [100, 0],
]
const SEVERITY_PENALTY: Record<string, number> = { none: 0, minor: 1.5, moderate: 3.5, severe: 6 }

function interpolate(anchors: [number, number][], value: number): number {
  if (value <= anchors[0][0]) return anchors[0][1]
  for (let i = 0; i < anchors.length - 1; i++) {
    const [x0, y0] = anchors[i]
    const [x1, y1] = anchors[i + 1]
    if (value <= x1) {
      if (x1 === x0) return y1
      return y0 + ((value - x0) / (x1 - x0)) * (y1 - y0)
    }
  }
  return anchors[anchors.length - 1][1]
}

const round2 = (value: number) => Math.round(value * 100) / 100

export function centeringFaceScore(
  left: number | null,
  right: number | null,
  top: number | null,
  bottom: number | null,
  isFront: boolean,
): number | null {
  const anchors = isFront ? FRONT_CENTERING_ANCHORS : BACK_CENTERING_ANCHORS
  const ratios: number[] = []
  for (const [a, b] of [
    [left, right],
    [top, bottom],
  ]) {
    if (a === null || b === null || a === undefined || b === undefined) continue
    const total = a + b
    if (total <= 0) continue
    ratios.push((Math.max(a, b) / total) * 100)
  }
  if (!ratios.length) return null
  // The worse axis decides the face.
  return round2(interpolate(anchors, Math.max(...ratios)))
}

function groupScore(assessment: ConditionAssessment, fields: readonly string[]): number | null {
  const penalties: number[] = []
  for (const face of ['front', 'back'] as const) {
    for (const field of fields) {
      const value = (assessment[face] as unknown as Record<string, Severity>)[field]
      if (value in SEVERITY_PENALTY) penalties.push(SEVERITY_PENALTY[value])
    }
  }
  if (!penalties.length) return null
  penalties.sort((a, b) => b - a)
  const rest = penalties.slice(1).reduce((sum, value) => sum + value, 0)
  return round2(Math.max(0, Math.min(10, 10 - penalties[0] - 0.4 * rest)))
}

function completeness(assessment: ConditionAssessment): number {
  const total = DEFECT_FIELDS.length * 2 + 8
  let answered = 0
  for (const face of ['front', 'back'] as const) {
    for (const field of DEFECT_FIELDS) {
      const value = (assessment[face] as unknown as Record<string, Severity>)[field]
      if (value in SEVERITY_PENALTY) answered++
    }
    for (const edge of ['left', 'right', 'top', 'bottom'] as const) {
      if (assessment.centering[face][edge] !== null && assessment.centering[face][edge] !== undefined)
        answered++
    }
  }
  return Math.round((answered / total) * 10000) / 10000
}

export function recomputeScores(assessment: ConditionAssessment): ConditionAssessment {
  const front = centeringFaceScore(
    assessment.centering.front.left,
    assessment.centering.front.right,
    assessment.centering.front.top,
    assessment.centering.front.bottom,
    true,
  )
  const back = centeringFaceScore(
    assessment.centering.back.left,
    assessment.centering.back.right,
    assessment.centering.back.top,
    assessment.centering.back.bottom,
    false,
  )
  const faces = [front, back].filter((value): value is number => value !== null)

  const corners = groupScore(assessment, CORNER_GROUP)
  const edges = groupScore(assessment, EDGE_GROUP)
  const surface = groupScore(assessment, SURFACE_GROUP)
  const centering = faces.length ? round2(Math.min(...faces)) : null

  const weights: [number | null, number][] = [
    [centering, 0.25],
    [corners, 0.25],
    [edges, 0.2],
    [surface, 0.3],
  ]
  let total = 0
  let totalWeight = 0
  for (const [value, weight] of weights) {
    if (value === null) continue
    total += value * weight
    totalWeight += weight
  }

  assessment.scores = {
    centering,
    centering_front: front,
    centering_back: back,
    corners,
    edges,
    surface,
    overall: totalWeight ? round2(total / totalWeight) : null,
    completeness: completeness(assessment),
  }
  return assessment
}

export const BLANK_FACE: FaceDefects = Object.fromEntries([
  ...DEFECT_FIELDS.map((field) => [field, 'unknown' as Severity]),
  ['notes', null],
  ['defect_notes', null],
]) as unknown as FaceDefects

export function buildAssessment(cardId: string, payload: ConditionWrite): ConditionAssessment {
  const now = new Date().toISOString()
  const assessment: ConditionAssessment = {
    id: `cond-${Math.random().toString(36).slice(2, 10)}`,
    card_id: cardId,
    assessed_at: now,
    assessor: payload.assessor ?? 'user',
    is_current: true,
    centering: {
      front: { left: null, right: null, top: null, bottom: null, ...(payload.centering?.front ?? {}) },
      back: { left: null, right: null, top: null, bottom: null, ...(payload.centering?.back ?? {}) },
    },
    front: { ...BLANK_FACE, ...(payload.front ?? {}) },
    back: { ...BLANK_FACE, ...(payload.back ?? {}) },
    notes: payload.notes ?? null,
    scores: {
      centering: null,
      centering_front: null,
      centering_back: null,
      corners: null,
      edges: null,
      surface: null,
      overall: null,
      completeness: null,
    },
    created_at: now,
    updated_at: now,
  }
  return recomputeScores(assessment)
}

// Mirrors app/services/identity.py
const NON_ALNUM = /[^a-z0-9]+/g
const slug = (value: string | null | undefined, fallback = 'unknown') => {
  if (!value) return fallback
  const text = value.trim().toLowerCase().replace(NON_ALNUM, '-').replace(/^-|-$/g, '')
  return text || fallback
}

export function buildCatalogKey(card: Partial<Card>): string {
  const parts = [
    slug(card.language, 'english'),
    slug(card.set_code ?? card.set_name, 'noset'),
    slug(card.card_number, 'nonum'),
    slug(card.variant, 'standard'),
    slug(card.printing, 'unlimited'),
  ]
  if (!card.card_number) parts.splice(3, 0, slug(card.name))
  return parts.join('|')
}

// --- Grade model -------------------------------------------------------------
// Mirrors app/services/prediction_service.py. See that module for why each step
// exists; this is a port, not a second design.

const SEVERITY_RANK: Record<string, number> = { none: 0, minor: 1, moderate: 2, severe: 3 }

const MODEL = {
  weights: { centering: 0.25, corners: 0.25, edges: 0.2, surface: 0.3 } as Record<string, number>,
  worstWeight: 0.45,
  baseSigma: 0.45,
  unknownSigma: 1.6,
  disagreementFactor: 0.25,
  maxSigma: 3.0,
  minProbability: 0.005,
}

export interface DemoRule {
  code: string
  label: string
  field: string
  minSeverity: string
  maxGrade?: number
  multiplier?: number
  fromGrade?: number
  active?: boolean
}

/** The seeded rules from app/services/seed.py, in the same order. */
export const DEMO_RULES: DemoRule[] = [
  { code: 'crease_severe', label: 'Severe crease', field: 'creases', minSeverity: 'severe', maxGrade: 3 },
  { code: 'crease_moderate', label: 'Major crease', field: 'creases', minSeverity: 'moderate', maxGrade: 5 },
  { code: 'crease_minor', label: 'Light crease or bend', field: 'creases', minSeverity: 'minor', maxGrade: 7 },
  { code: 'dent_moderate', label: 'Visible dent', field: 'dents', minSeverity: 'moderate', maxGrade: 7 },
  { code: 'whitening_severe', label: 'Heavy whitening', field: 'whitening', minSeverity: 'severe', maxGrade: 8 },
  { code: 'whitening_minor', label: 'Minor whitening', field: 'whitening', minSeverity: 'minor', multiplier: 0.75, fromGrade: 10 },
  { code: 'corner_severe', label: 'Severe corner damage', field: 'corner_any', minSeverity: 'severe', maxGrade: 6 },
  { code: 'corner_moderate', label: 'Moderate corner wear', field: 'corner_any', minSeverity: 'moderate', maxGrade: 8 },
  { code: 'surface_severe', label: 'Severe surface damage', field: 'surface_condition', minSeverity: 'severe', maxGrade: 6 },
  { code: 'scratches_minor', label: 'Surface scratches', field: 'scratches', minSeverity: 'minor', multiplier: 0.8, fromGrade: 10 },
  { code: 'print_lines_moderate', label: 'Print lines', field: 'print_lines', minSeverity: 'moderate', multiplier: 0.6, fromGrade: 9 },
  { code: 'silvering_moderate', label: 'Silvering', field: 'silvering', minSeverity: 'moderate', multiplier: 0.7, fromGrade: 9 },
  { code: 'edge_severe', label: 'Severe edge wear', field: 'edge_condition', minSeverity: 'severe', maxGrade: 7 },
  { code: 'staining_moderate', label: 'Staining', field: 'staining', minSeverity: 'moderate', maxGrade: 6 },
  { code: 'holo_severe', label: 'Severe holo damage', field: 'holo_condition', minSeverity: 'severe', maxGrade: 8 },
]

const FIELD_GROUPS: Record<string, readonly string[]> = {
  corner_any: ['corner_tl', 'corner_tr', 'corner_bl', 'corner_br'],
}

function normalCdf(x: number, mean: number, sigma: number): number {
  if (sigma <= 0) return x < mean ? 0 : 1
  // Abramowitz & Stegun 7.1.26 — erf is not in the JS standard library.
  const z = (x - mean) / (sigma * Math.SQRT2)
  const sign = z < 0 ? -1 : 1
  const t = 1 / (1 + 0.3275911 * Math.abs(z))
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp(-z * z)
  return 0.5 * (1 + sign * y)
}

function observedSeverity(assessment: ConditionAssessment, field: string): string | null {
  const fields = FIELD_GROUPS[field] ?? [field]
  let worst: string | null = null
  for (const face of ['front', 'back'] as const) {
    for (const name of fields) {
      const value = (assessment[face] as unknown as Record<string, string>)[name]
      if (!(value in SEVERITY_RANK)) continue
      if (worst === null || SEVERITY_RANK[value] > SEVERITY_RANK[worst]) worst = value
    }
  }
  return worst
}

function predictGrade(
  assessment: ConditionAssessment,
  company: GradingCompany | null,
): CompanyGradePrediction & { baseGrade: number } {
  const entries = Object.entries(MODEL.weights)
    .map(([key, weight]) => {
      const score = assessment.scores[key as keyof typeof assessment.scores] as number | null
      return score === null || score === undefined ? null : { score, weight }
    })
    .filter((item): item is { score: number; weight: number } => item !== null)

  const scores = entries.map((item) => item.score)
  const totalWeight = entries.reduce((sum, item) => sum + item.weight, 0)
  const mean = totalWeight
    ? entries.reduce((sum, item) => sum + item.score * item.weight, 0) / totalWeight
    : scores.reduce((sum, score) => sum + score, 0) / Math.max(scores.length, 1)
  const worst = Math.min(...scores)
  let centre = (1 - MODEL.worstWeight) * mean + MODEL.worstWeight * worst
  const baseGrade = centre

  const average = scores.reduce((sum, score) => sum + score, 0) / scores.length
  const disagreement =
    scores.length > 1
      ? Math.sqrt(scores.reduce((sum, s) => sum + (s - average) ** 2, 0) / scores.length)
      : 0
  const completeness = assessment.scores.completeness ?? 0
  const sigma = Math.min(
    MODEL.baseSigma + (1 - completeness) * MODEL.unknownSigma + disagreement * MODEL.disagreementFactor,
    MODEL.maxSigma,
  )

  const caps: string[] = []
  let cap: number | null = null
  const multipliers: { multiplier: number; fromGrade: number }[] = []
  for (const rule of DEMO_RULES) {
    if (rule.active === false) continue
    const severity = observedSeverity(assessment, rule.field)
    if (!severity || SEVERITY_RANK[severity] === 0) continue
    if (SEVERITY_RANK[severity] < SEVERITY_RANK[rule.minSeverity]) continue
    if (rule.maxGrade !== undefined) {
      cap = cap === null ? rule.maxGrade : Math.min(cap, rule.maxGrade)
      caps.push(rule.label)
    }
    if (rule.multiplier !== undefined && rule.fromGrade !== undefined) {
      multipliers.push({ multiplier: rule.multiplier, fromGrade: rule.fromGrade })
    }
  }
  if (cap !== null) centre = Math.min(centre, cap)
  if (company?.strictness) centre += company.strictness

  const step = company?.supports_half_grades ? 0.5 : 1
  const top = company?.grade_scale_max ?? 10
  const ladder: number[] = []
  for (let value = 1; value <= top + 1e-9; value += step) ladder.push(Number(value.toFixed(1)))

  // The top grade absorbs everything above it; the bottom grade deliberately
  // does not absorb everything below it, or a capped card would report the
  // lowest grade as its most likely outcome. See the Python module for why.
  const weights = new Map<number, number>()
  ladder.forEach((grade, index) => {
    const low = grade - step / 2
    const high = index === ladder.length - 1 ? Infinity : grade + step / 2
    const lower = normalCdf(low, centre, sigma)
    const upper = high === Infinity ? 1 : normalCdf(high, centre, sigma)
    weights.set(grade, Math.max(0, upper - lower))
  })

  if (cap !== null) {
    for (const grade of weights.keys()) if (grade > cap + 1e-9) weights.set(grade, 0)
  }
  for (const { multiplier, fromGrade } of multipliers) {
    for (const [grade, value] of weights) {
      if (grade >= fromGrade - 1e-9) weights.set(grade, value * multiplier)
    }
  }

  const normalise = (source: Map<number, number>) => {
    const total = [...source.values()].reduce((sum, value) => sum + value, 0)
    if (total <= 0) return new Map([...source.keys()].map((g) => [g, 1 / source.size]))
    return new Map([...source].map(([g, v]) => [g, v / total]))
  }

  let probabilities = normalise(weights)
  const trimmed = new Map([...probabilities].filter(([, v]) => v >= MODEL.minProbability))
  probabilities = normalise(trimmed.size ? trimmed : probabilities)

  const sorted = [...probabilities].sort((a, b) => b[0] - a[0])
  const likely = sorted.reduce((best, item) => (item[1] > best[1] ? item : best), sorted[0])

  // Narrowest run of adjacent grades holding 80% of the mass.
  let range: [number, number] = [sorted[sorted.length - 1][0], sorted[0][0]]
  let width = Infinity
  for (let start = 0; start < sorted.length; start++) {
    let total = 0
    for (let end = start; end < sorted.length; end++) {
      total += sorted[end][1]
      if (total >= 0.8 - 1e-9) {
        const span = sorted[start][0] - sorted[end][0]
        if (span < width) {
          width = span
          range = [sorted[end][0], sorted[start][0]]
        }
        break
      }
    }
  }

  const confidence: CompanyGradePrediction['confidence'] =
    completeness >= 0.9 && sigma <= MODEL.baseSigma + 0.35
      ? 'high'
      : completeness >= 0.6
        ? 'medium'
        : completeness >= 0.25
          ? 'low'
          : 'none'

  return {
    company_id: company?.id ?? null,
    company_code: company?.code ?? 'physical',
    company_name: company?.name ?? null,
    probabilities: sorted.map(([grade, probability]) => ({
      grade,
      label: company ? `${company.code} ${grade}` : `Grade ${grade}`,
      probability,
    })),
    likely_grade: likely[0],
    grade_min: range[0],
    grade_max: range[1],
    max_grade_cap: cap,
    confidence,
    caps_applied: caps,
    is_user_override: false,
    baseGrade,
  }
}

function buildGradePrediction(
  assessment: ConditionAssessment | undefined,
  companies: GradingCompany[],
): CardEvaluation['grade_prediction'] {
  const empty = {
    company_code: null,
    kind: null,
    source: null,
    probabilities: [],
    likely_grade: null,
    grade_min: null,
    grade_max: null,
    max_grade_cap: null,
    confidence: 'none' as const,
    caps_applied: [],
    physical: null,
    by_company: [],
    model_version: null,
    base_grade: null,
  }

  if (!assessment) {
    return { status: 'not_assessed', reason: 'No condition assessment recorded yet.', phase: null, ...empty }
  }

  const hasScores = Object.keys(MODEL.weights).some(
    (key) => assessment.scores[key as keyof typeof assessment.scores] !== null,
  )
  if (!hasScores) {
    return {
      status: 'insufficient_data',
      reason: 'This assessment has no answered fields, so there is nothing to predict from.',
      phase: null,
      ...empty,
    }
  }

  const active = companies.filter((company) => company.active)
  const physical = predictGrade(assessment, null)
  const byCompany = active.map((company) => predictGrade(assessment, company))
  const headline = byCompany[0]
  const completeness = assessment.scores.completeness ?? 0

  return {
    status: completeness >= 0.6 ? 'ok' : 'partial',
    reason:
      completeness >= 0.6
        ? null
        : `Only ${Math.round(completeness * 100)}% of the assessment is answered, so the range is wide. Finish it to narrow the estimate.`,
    phase: null,
    company_code: headline?.company_code ?? null,
    kind: 'market',
    source: 'rules_engine',
    probabilities: headline?.probabilities ?? [],
    likely_grade: headline?.likely_grade ?? null,
    grade_min: headline?.grade_min ?? null,
    grade_max: headline?.grade_max ?? null,
    max_grade_cap: headline?.max_grade_cap ?? null,
    confidence: headline?.confidence ?? 'none',
    caps_applied: headline?.caps_applied ?? [],
    physical,
    by_company: byCompany,
    model_version: 'rules-1.0-demo',
    base_grade: Number(physical.baseGrade.toFixed(2)),
  }
}

// Mirrors app/services/evaluation.py
const NOTABLE = new Set(['moderate', 'severe'])

const CONFIDENCE_ORDER = ['none', 'low', 'medium', 'high'] as const
const GOOD_CONFIDENCE = new Set(['medium', 'high'])
const rank = (confidence: string) => CONFIDENCE_ORDER.indexOf(confidence as 'none')

/** "none confidence" is not English. Say what it means instead. */
const confidencePhrase = (confidence: string) =>
  confidence === 'none' ? 'no confidence' : `${confidence} confidence`

const NO_MARKET_REASON =
  'No market data for this card yet. Add sales manually, import a CSV, or connect a data source.'

function valueRow(price: MarketSummary['prices'][number]): MarketValueRow {
  return {
    grade_label: price.grade_label,
    company_code: null,
    grade: price.grade,
    median: price.median,
    weighted_median: price.weighted_median,
    low_quartile: price.low_quartile,
    high_quartile: price.high_quartile,
    last_sale: price.last_sale,
    // The user's own figure is what they will act on (spec section 35).
    realistic_sale: price.user_value ?? price.realistic_sale,
    quick_sale: price.quick_sale,
    sample_size: price.sample_size,
    window_days: price.window_days,
    last_sale_at: price.last_sale_at,
    confidence: price.confidence,
    premium_vs_raw_pct: price.premium_vs_raw_pct,
    is_user_override: price.user_value !== null,
  }
}

function buildMarketBlock(market: MarketSummary, currency: string): MarketBlock {
  const blank = { currency, raw: null, graded: [], computed_at: null, sources: [] }

  if (!market.catalog_key) {
    return {
      ...blank,
      status: 'insufficient_data',
      phase: 3,
      reason: 'This card has no catalog key, so sales cannot be matched to it.',
    }
  }
  if (!market.prices.length) {
    return {
      ...blank,
      status: 'insufficient_data',
      phase: 3,
      reason: market.excluded_count
        ? `${market.excluded_count} sale(s) stored, all excluded as non-comparable (lots, damage, wrong language or variant). Review the exclusions if that looks wrong — every one is reversible.`
        : NO_MARKET_REASON,
    }
  }

  const rawPrice = market.prices.find((price) => price.grade_label === 'raw')
  const raw = rawPrice ? valueRow(rawPrice) : null
  const graded = market.prices
    .filter((price) => price.grade_label !== 'raw')
    .sort((a, b) => (b.grade ?? 0) - (a.grade ?? 0))
    .map((price) => ({ ...valueRow(price), premium_vs_raw_pct: premiumVsRawPct(rawPrice, price) }))

  const best = market.prices.reduce(
    (top, price) => (rank(price.confidence) > rank(top) ? price.confidence : top),
    'none' as string,
  )
  let status: MarketBlock['status'] = GOOD_CONFIDENCE.has(best) ? 'ok' : 'partial'
  let reason: string | null = null
  if (status !== 'ok') {
    const thin = rawPrice ?? market.prices[0]
    reason = `Thin evidence: ${thin.sample_size} sale(s) in the last ${thin.window_days} days. Treat these figures as indicative.`
  }
  if (!raw) {
    const note = 'No raw sales stored, so there is nothing to compare a slab against.'
    reason = reason ? `${reason} ${note}` : note
    status = 'partial'
  }

  return {
    status,
    phase: null,
    reason,
    currency,
    raw,
    graded,
    computed_at: market.computed_at,
    sources: market.sale_count ? ['Manual entry'] : [],
  }
}

function buildTrendBlock(market: MarketSummary): TrendBlock {
  const trend = market.trend
  const grade = trend.grade_label ?? 'raw'
  if (trend.direction === 'insufficient_data') {
    return {
      status: 'insufficient_data',
      phase: 3,
      reason: `A trend needs sales in two comparable periods, not a single price. ${trend.sample_size} sale(s) stored.`,
      ...trend,
    }
  }
  const status = GOOD_CONFIDENCE.has(trend.confidence) ? 'ok' : 'partial'
  return {
    status,
    phase: null,
    reason:
      status === 'ok'
        ? `${grade === 'raw' ? 'Raw' : grade} prices only — a trend across pooled grades measures which grades happened to sell, not whether prices moved.`
        : `Direction from ${trend.sample_size} ${grade} sale(s). A 25% move off three sales is not the same claim as a 12% move off a hundred and fifty.`,
    ...trend,
  }
}

/** The market half of the "Why?" panel, and what it still needs. */
function marketExplanation(
  market: MarketSummary,
  block: MarketBlock,
  blockers: string[],
): ExplanationItem[] {
  const items: ExplanationItem[] = []

  if (market.sale_count === 0) {
    if (market.excluded_count) {
      items.push({
        kind: 'fail',
        text: `All ${market.excluded_count} stored sale(s) were filtered out.`,
        detail: 'Open the sales list to see why, and include any that were wrong.',
      })
      blockers.push(
        'Every stored sale was excluded as non-comparable. Review the exclusions or add sales of the card itself.',
      )
    } else {
      items.push({ kind: 'fail', text: 'No comparable sales stored.', detail: null })
      blockers.push('Add comparable sales for the raw card and each relevant grade.')
    }
    return items
  }

  let detail = `${market.sale_count} counted`
  if (market.excluded_count) detail += `, ${market.excluded_count} excluded as non-comparable`
  items.push({
    kind: block.status === 'ok' ? 'pass' : 'warn',
    text: `${market.sale_count} comparable sale(s) stored locally.`,
    detail: `${detail}. Every exclusion is listed and reversible.`,
  })

  const raw = block.raw
  if (raw && raw.realistic_sale !== null) {
    items.push({
      kind: GOOD_CONFIDENCE.has(raw.confidence) ? 'pass' : 'warn',
      text: `Raw value ${formatMoney(raw.realistic_sale, block.currency)} (${confidencePhrase(raw.confidence)}).`,
      detail: `${raw.sample_size} sale(s) in ${raw.window_days} days.`,
    })
  } else if (!raw) {
    items.push({ kind: 'warn', text: 'No raw sales stored for this card.', detail: null })
    blockers.push('Add raw sales — grading profit is measured against selling it raw.')
  }

  if (!block.graded.length) {
    blockers.push(
      'Add graded sales for the grades this card could realistically get, so the upside can be measured rather than assumed.',
    )
  } else {
    const best = block.graded.reduce((top, row) =>
      (row.premium_vs_raw_pct ?? -Infinity) > (top.premium_vs_raw_pct ?? -Infinity) ? row : top,
    )
    if (best.premium_vs_raw_pct !== null) {
      items.push({
        kind: 'info',
        text: `${best.grade_label} sells ${best.premium_vs_raw_pct > 0 ? '+' : ''}${best.premium_vs_raw_pct.toFixed(0)}% against raw.`,
        detail: `${best.sample_size} sale(s) behind that figure.`,
      })
    }
  }

  const liquidity = market.liquidity
  if (liquidity.score !== null) {
    items.push({
      kind: liquidity.score < 5 ? 'warn' : 'pass',
      text: `Liquidity ${liquidity.score.toFixed(1)}/10 — ${liquidity.band.replace(/_/g, ' ')}.`,
      detail: liquidity.median_days_between_sales
        ? `Median ${Math.round(liquidity.median_days_between_sales)} days between sales.`
        : null,
    })
    if (liquidity.score < 3) {
      blockers.push(
        'This card barely trades. Check you could actually sell the slab before spending on grading.',
      )
    }
  }

  const trend = market.trend
  if (trend.direction !== 'insufficient_data') {
    const horizons: [number, number | null][] = [
      [90, trend.change_90d_pct],
      [180, trend.change_180d_pct],
      [30, trend.change_30d_pct],
      [365, trend.change_365d_pct],
      [7, trend.change_7d_pct],
    ]
    const found = horizons.find(([, change]) => change !== null)
    items.push({
      kind: 'info',
      text: `Trend ${trend.direction.replace(/_/g, ' ')} (${confidencePhrase(trend.confidence)}).`,
      detail: found
        ? `${found[1]! > 0 ? '+' : ''}${found[1]!.toFixed(1)}% over ${found[0]} days on ${trend.grade_label ?? 'raw'} sales.`
        : null,
    })
  }

  return items
}


/**
 * The best each company could do, priced in its own slabs.
 *
 * Strictly within a company: pairing ACE's fee with PSA's slab price would
 * describe a route that does not exist.
 */
function bestCasePerCompany(
  options: GradingOption[],
  netRows: NetValueRow[],
  rawNet: NetValueRow | null,
): CompanyBestCase[] {
  const byCompany = new Map<string, GradingOption[]>()
  for (const option of options) {
    const group = byCompany.get(option.company_code) ?? []
    group.push(option)
    byCompany.set(option.company_code, group)
  }

  const results: CompanyBestCase[] = []
  for (const [code, group] of byCompany) {
    const row: CompanyBestCase = {
      company_id: group[0].company_id,
      company_code: code,
      tier_name: null,
      grading_cost: null,
      best_grade_label: null,
      best_grade: null,
      best_net: null,
      upside_vs_raw: null,
      reason: null,
    }

    const usable = group.filter((item) => item.available && item.total_cost !== null)
    if (!usable.length) {
      row.reason = `No usable ${code} tier for this card.`
      results.push(row)
      continue
    }

    const cheapest = usable.reduce((low, item) =>
      (item.total_cost ?? Infinity) < (low.total_cost ?? Infinity) ? item : low,
    )
    row.tier_name = cheapest.tier_name
    row.grading_cost = cheapest.total_cost

    const owned = netRows.filter(
      (item) => item.is_graded && item.grade_label.split(' ')[0].toUpperCase() === code.toUpperCase(),
    )
    if (!owned.length) {
      row.reason = `No ${code} sales stored, so a ${code} slab cannot be priced.`
      results.push(row)
      continue
    }

    const best = owned.reduce((top, item) => ((item.net ?? -Infinity) > (top.net ?? -Infinity) ? item : top))
    row.best_grade_label = best.grade_label
    row.best_grade = best.grade
    row.best_net = best.net
    if (best.net !== null && rawNet?.net !== null && rawNet?.net !== undefined) {
      row.upside_vs_raw = Math.round((best.net - rawNet.net - (cheapest.total_cost ?? 0)) * 100) / 100
    }
    results.push(row)
  }

  return results.sort((a, b) => (b.upside_vs_raw ?? -1e9) - (a.upside_vs_raw ?? -1e9))
}

function buildOptionsBlock(input: {
  options: GradingOption[]
  currency: string
  declared: DeclaredValue
  assumptions: SubmissionAssumptions
  profile: SellingProfile | null
  netRows: NetValueRow[]
  bestCase: CompanyBestCase[]
  cheapestCost: number | null
}): GradingOptionsBlock {
  const { options, currency, declared, assumptions, profile, netRows, bestCase } = input
  const usable = options.filter((option) => option.available && option.total_cost !== null)

  const reasons: string[] = []
  let status: GradingOptionsBlock['status']
  if (!options.length) {
    status = 'insufficient_data'
    reasons.push('No active grading company is configured.')
  } else if (declared.valueMinor === null) {
    status = 'partial'
    reasons.push(
      'Costs shown without a declared value, so tier ceilings and any percentage-of-value fees ' +
        'are not applied. Add comparable sales or your own estimate.',
    )
  } else if (!profile) {
    status = 'partial'
    reasons.push(
      'No selling profile is configured, so net proceeds cannot be worked out. ' +
        'Add one in Settings → Selling.',
    )
  } else if (!usable.length) {
    status = 'partial'
    reasons.push('No tier is usable for this card as things stand — see the reasons below.')
  } else {
    status = 'ok'
  }

  if (assumptions.allocationNote) reasons.push(assumptions.allocationNote)
  if (options.length && (declared.confidence === 'none' || declared.confidence === 'low')) {
    reasons.push(
      `Declared value is a ${confidencePhrase(declared.confidence)} estimate, and it drives tier ` +
        'eligibility — check it before submitting.',
    )
  }

  return {
    status,
    phase: status === 'ok' ? null : 4,
    reason: reasons.join(' ') || null,
    currency,
    options,
    best_case: bestCase,
    declared_value: toMajor(declared.valueMinor),
    declared_value_source: declared.source,
    declared_value_confidence: declared.confidence,
    declared_value_basis: declared.basis,
    declared_value_coverage: declared.coverage,
    assumed_batch_size: assumptions.batchSize,
    allocation_method: assumptions.allocationMethod,
    allocation_note: assumptions.allocationNote,
    selling_profile_code: profile?.code ?? null,
    selling_profile_name: profile?.name ?? null,
    net_values: netRows,
    cheapest_available_cost: input.cheapestCost,
  }
}


/** The money half of the "Why?" panel: what grading costs, and what a sale keeps. */
function economicsExplanation(
  options: GradingOptionsBlock,
  market: MarketBlock,
  currency: string,
  blockers: string[],
): ExplanationItem[] {
  const items: ExplanationItem[] = []
  const usable = options.options.filter((option) => option.available)

  if (!usable.length) {
    items.push({ kind: 'fail', text: 'No usable grading tier for this card.', detail: null })
    blockers.push(
      'Enter current pricing for at least one grading company, or check the tier restrictions ' +
        'listed under Grading routes.',
    )
  } else {
    const cheapest = usable.reduce((low, item) =>
      (item.total_cost ?? Infinity) < (low.total_cost ?? Infinity) ? item : low,
    )
    const batch =
      cheapest.assumed_batch_size > 1
        ? ` in a batch of ${cheapest.assumed_batch_size}`
        : ' sending it on its own'
    items.push({
      kind: 'pass',
      text: `Cheapest route ${cheapest.company_code} ${cheapest.tier_name} at ${formatMoney(
        cheapest.total_cost,
        currency,
      )}${batch}.`,
      detail: `${formatMoney(cheapest.grading_fee, currency)} fee plus ${formatMoney(
        cheapest.allocated_overhead,
        currency,
      )} share of shipping and insurance.`,
    })
  }

  if (options.declared_value !== null) {
    const source = options.declared_value_source === 'user' ? 'yours' : 'estimated'
    items.push({
      kind: GOOD_CONFIDENCE.has(options.declared_value_confidence) ? 'pass' : 'warn',
      text: `Declared value ${formatMoney(options.declared_value, currency)} (${source}, ${confidencePhrase(
        options.declared_value_confidence,
      )}).`,
      detail: options.declared_value_basis,
    })
  }

  const rawNet = options.net_values.find((row) => !row.is_graded)
  if (rawNet?.net !== null && rawNet?.net !== undefined) {
    const kept = rawNet.gross ? rawNet.net / rawNet.gross : null
    items.push({
      kind: 'info',
      text: `Selling it raw nets ${formatMoney(rawNet.net, currency)} after fees and postage.`,
      detail:
        kept !== null
          ? `You keep ${Math.round(kept * 100)}% of the sale price on ${options.selling_profile_name}.`
          : null,
    })
  }

  // Best case, strictly within one company: an ACE 10 does not sell for what a
  // PSA 10 sells for.
  const priced = options.best_case.filter((row) => row.upside_vs_raw !== null)
  if (priced.length) {
    const best = priced[0]
    items.push({
      kind: 'info',
      text:
        `Best case ${best.best_grade_label} nets ${formatMoney(best.best_net, currency)} — ` +
        `${formatMoney(best.upside_vs_raw, currency)} over selling raw, after ${best.company_code} ` +
        `${best.tier_name} at ${formatMoney(best.grading_cost, currency)}.`,
      detail:
        `Best case only: it assumes the top grade you have ${best.company_code} sales data for. ` +
        'The probability-weighted figure arrives with the decision engine.',
    })
  } else if (options.best_case.length && market.status !== 'insufficient_data') {
    const missing = options.best_case.filter((row) => row.best_net === null).map((row) => row.company_code)
    blockers.push(
      `Add graded sales for ${[...missing].sort().join(', ')} so the upside can be netted rather ` +
        'than guessed at.',
    )
  }

  return items
}

export function buildEvaluation(
  card: Card,
  assessment: ConditionAssessment | undefined,
  companies: GradingCompany[],
  currency: string,
  market: MarketSummary,
  settings: Record<string, unknown> = {},
  profile: SellingProfile | null = null,
  batchSize = 1,
): CardEvaluation {
  const today = new Date().toISOString().slice(0, 10)
  const gradeBlock = buildGradePrediction(assessment, companies)

  // Value the declared figure against the headline grader's own ladder and
  // prices — the same company, or the number describes a card that does not
  // exist.
  const headline = gradeBlock.by_company[0]
  const probabilities = headline?.probabilities.length
    ? Object.fromEntries(headline.probabilities.map((item) => [item.grade, item.probability]))
    : null
  let declared = suggestDeclaredValue(card, market.prices, probabilities, headline?.company_code ?? null)
  if (card.user_declared_value !== null) {
    declared = {
      valueMinor: toMinor(card.user_declared_value),
      source: 'user',
      confidence: 'high',
      coverage: null,
      basis: "Your own figure. The engine's suggestion is kept alongside it, not replaced.",
    }
  }

  const assumptions = assumptionsFrom(settings, batchSize)
  const options: GradingOption[] = []

  for (const company of companies.filter((c) => c.active)) {
    const tiers = eligibleTiers(company, declared.valueMinor, assumptions.batchSize, today)
    if (!tiers.length) {
      options.push({
        company_id: company.id,
        company_code: company.code,
        company_name: company.name,
        tier_id: null,
        tier_name: null,
        currency: company.currency,
        declared_value: toMajor(declared.valueMinor),
        base_fee: null,
        membership_discount: null,
        grading_fee: null,
        per_card_fees: null,
        declared_value_fee: null,
        allocated_overhead: null,
        total_cost: null,
        shared_total: null,
        assumed_batch_size: assumptions.batchSize,
        membership_code: null,
        turnaround_days: null,
        minimum_cards: 1,
        requires_batch: false,
        membership_required: false,
        available: false,
        blockers: [
          `No active tier configured for ${company.code}. Add current pricing in Settings → Grading.`,
        ],
      })
      continue
    }

    for (const { tier, blockers } of tiers) {
      const cost = costForTier(tier, company, declared.valueMinor, assumptions, today)
      const priced = tier.price > 0
      options.push({
        company_id: company.id,
        company_code: company.code,
        company_name: company.name,
        tier_id: tier.id,
        tier_name: tier.tier_name,
        currency: tier.currency,
        declared_value: toMajor(declared.valueMinor),
        base_fee: priced ? toMajor(cost.baseFeeMinor) : null,
        membership_discount: toMajor(cost.membershipDiscountMinor) || null,
        grading_fee: priced ? toMajor(cost.gradingFeeMinor) : null,
        per_card_fees: toMajor(cost.perCardFeesMinor) || null,
        declared_value_fee: toMajor(cost.declaredValueFeeMinor) || null,
        allocated_overhead: toMajor(cost.allocatedOverheadMinor),
        // An unpriced tier gets no total: costing it at the overhead alone
        // would read as a cheap route.
        total_cost: priced ? toMajor(cost.totalMinor) : null,
        shared_total: toMajor(cost.sharedTotalMinor) || null,
        assumed_batch_size: assumptions.batchSize,
        membership_code: cost.membershipCode,
        turnaround_days: tier.turnaround_days,
        minimum_cards: tier.minimum_cards,
        requires_batch: tier.minimum_cards > 1,
        membership_required: tier.membership_required,
        available: blockers.length === 0,
        blockers,
      })
    }
  }

  const nets = netByGrade(market.prices, profile)
  const netRows: NetValueRow[] = [...nets.entries()]
    .sort(([a], [b]) => (a === 'raw' ? -1 : b === 'raw' ? 1 : a.localeCompare(b)))
    .map(([label, value]) => ({
      grade_label: label,
      grade: market.prices.find((price) => price.grade_label === label)?.grade ?? null,
      gross: toMajor(value.grossMinor),
      shipping_income: toMajor(value.shippingIncomeMinor) || null,
      platform_fee: toMajor(value.platformFeeMinor) || null,
      payment_fee: toMajor(value.paymentFeeMinor) || null,
      listing_fee: toMajor(value.listingFeeMinor) || null,
      postage_cost: toMajor(value.postageCostMinor) || null,
      packaging_cost: toMajor(value.packagingCostMinor) || null,
      total_costs: toMajor(value.totalCostsMinor),
      net: toMajor(value.netMinor),
      is_graded: value.isGraded,
    }))

  const rawNetRow = netRows.find((row) => !row.is_graded) ?? null
  const bestCase = bestCasePerCompany(options, netRows, rawNetRow)
  const usable = options.filter((o) => o.available && o.total_cost !== null)
  const cheapestCost = usable.length ? Math.min(...usable.map((o) => o.total_cost!)) : null
  const optionsBlock = buildOptionsBlock({
    options,
    currency,
    declared,
    assumptions,
    profile,
    netRows,
    bestCase,
    cheapestCost,
  })

  const notable: string[] = []
  if (assessment) {
    for (const face of ['front', 'back'] as const) {
      for (const field of DEFECT_FIELDS) {
        const severity = (assessment[face] as unknown as Record<string, Severity>)[field]
        if (NOTABLE.has(severity)) {
          notable.push(`${face[0].toUpperCase()}${face.slice(1)} ${field.replace(/_/g, ' ')}: ${severity}`)
        }
      }
    }
  }

  const complete = assessment?.scores.completeness ?? 0
  const conditionStatus = !assessment ? 'not_assessed' : complete >= 0.5 ? 'ok' : 'partial'

  const explanation: ExplanationItem[] = []
  const blockers: string[] = []

  const front = card.images.filter((image) => image.side === 'front')
  const back = card.images.filter((image) => image.side === 'back')
  if (front.length && back.length) {
    explanation.push({ kind: 'pass', text: 'Front and back photographs on file.', detail: null })
  } else {
    const missing = [
      front.length ? null : 'front',
      back.length ? null : 'back',
    ]
      .filter(Boolean)
      .join(' and ')
    explanation.push({
      kind: 'warn',
      text: `No ${missing} photograph.`,
      detail: 'Photographs are what make a condition assessment checkable later.',
    })
  }

  const marketBlock = buildMarketBlock(market, currency)
  const rawPrice = market.prices.find((price) => price.grade_label === 'raw')
  const marketRawValue = rawPrice
    ? (rawPrice.user_value ?? rawPrice.realistic_sale ?? rawPrice.median)
    : null

  if (!assessment) {
    explanation.push({ kind: 'fail', text: 'Condition not assessed.', detail: null })
    blockers.push("Assess the card's condition.")
  } else {
    explanation.push({
      kind: conditionStatus === 'ok' ? 'pass' : 'warn',
      text: `Condition assessed (${Math.round(complete * 100)}% complete).`,
      detail:
        assessment.scores.overall !== null
          ? `Overall condition score ${assessment.scores.overall.toFixed(1)}/10.`
          : null,
    })
    if (notable.length) {
      explanation.push({
        kind: 'warn',
        text: `${notable.length} notable defect(s) recorded.`,
        detail: notable.slice(0, 4).join('; '),
      })
    }
  }

  if (gradeBlock.status === 'ok' || gradeBlock.status === 'partial') {
    const top = gradeBlock.probabilities[0]
    explanation.push({
      kind: gradeBlock.status === 'ok' ? 'pass' : 'warn',
      text: `Likely ${gradeBlock.company_code} ${gradeBlock.likely_grade} (${confidencePhrase(gradeBlock.confidence)}).`,
      detail: top
        ? `${top.label} at ${Math.round(top.probability * 100)}%, range ${gradeBlock.grade_min}–${gradeBlock.grade_max}.`
        : null,
    })
    if (gradeBlock.caps_applied.length) {
      explanation.push({
        kind: 'warn',
        text: `Capped at ${gradeBlock.max_grade_cap} by ${gradeBlock.caps_applied.length} rule(s).`,
        detail: gradeBlock.caps_applied.slice(0, 3).join('; '),
      })
    }
    if (gradeBlock.status === 'partial') {
      blockers.push('Finish the condition assessment to narrow the grade estimate.')
    }
  }

  explanation.push(...marketExplanation(market, marketBlock, blockers))

  explanation.push(
    ...economicsExplanation(optionsBlock, marketBlock, currency, blockers),
  )

  if (card.user_raw_value === null && card.purchase_price === null) {
    explanation.push({
      kind: 'info',
      text: 'No raw value recorded.',
      detail: 'A purchase price or your own raw estimate gives the engine a floor to beat.',
    })
  }

  const displayName = card.card_number ? `${card.name} ${card.card_number}` : card.name
  const setLabel =
    card.set_name && card.set_code
      ? `${card.set_name} (${card.set_code})`
      : (card.set_name ?? card.set_code ?? null)

  const recommendation = card.decision_override
    ? {
        status: 'ok' as const,
        reason: null,
        phase: null,
        decision: card.decision_override,
        confidence: 'none' as const,
        headline: `Set by you: ${card.decision_override.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())}`,
        company_code: null,
        tier_name: null,
        expected_profit: null,
        roi_pct: null,
        probability_of_profit: null,
        minimum_profitable_grade: null,
        opportunity_score: null,
        alternative: null,
        alternative_note: null,
        is_user_override: true,
        reasons: [
          {
            kind: 'info' as const,
            text: 'Your decision overrides the engine.',
            detail: card.decision_override_reason,
          },
          ...explanation,
        ],
      }
    : {
        status: 'insufficient_data' as const,
        reason: blockers.join('; ') || null,
        phase: 5,
        decision: 'insufficient_data' as const,
        confidence: 'none' as const,
        headline: 'Not enough data to recommend a decision yet.',
        company_code: null,
        tier_name: null,
        expected_profit: null,
        roi_pct: null,
        probability_of_profit: null,
        minimum_profitable_grade: null,
        opportunity_score: null,
        alternative: null,
        alternative_note: null,
        is_user_override: false,
        reasons: explanation,
      }

  return {
    card_id: card.id,
    evaluated_at: new Date().toISOString(),
    engine_version: '0.1.0-demo',
    currency,
    raw: {
      status: 'ok',
      reason: null,
      phase: null,
      card_id: card.id,
      display_name: displayName,
      set_label: setLabel,
      number: card.card_number,
      variant: card.variant,
      language: card.language,
      quantity: card.quantity,
      currency,
      purchase_price: card.purchase_price,
      user_raw_value: card.user_raw_value,
      market_raw_value: marketRawValue,
      best_raw_value: card.user_raw_value ?? marketRawValue,
      raw_value_source:
        card.user_raw_value !== null ? 'user_override' : marketRawValue !== null ? 'market' : null,
      // Selling it raw is the alternative every grading decision is measured
      // against, so it is netted the same way a graded sale is.
      net_raw_sale_value: toMajor(
        netSaleValue(toMinor(card.user_raw_value ?? marketRawValue ?? 0) || null, profile, false)
          ?.netMinor ?? null,
      ),
    },
    condition: {
      status: conditionStatus,
      reason:
        conditionStatus === 'partial'
          ? `Only ${Math.round(complete * 100)}% of the assessment is filled in — the grade estimate will be wide until the rest is answered.`
          : conditionStatus === 'not_assessed'
            ? 'No condition assessment recorded yet.'
            : null,
      phase: null,
      assessment_id: assessment?.id ?? null,
      assessed_at: assessment?.assessed_at ?? null,
      assessor: assessment?.assessor ?? null,
      completeness: assessment?.scores.completeness ?? null,
      scores: {
        centering: assessment?.scores.centering ?? null,
        centering_front: assessment?.scores.centering_front ?? null,
        centering_back: assessment?.scores.centering_back ?? null,
        corners: assessment?.scores.corners ?? null,
        edges: assessment?.scores.edges ?? null,
        surface: assessment?.scores.surface ?? null,
        overall: assessment?.scores.overall ?? null,
      },
      notable_defects: notable,
    },
    grade_prediction: gradeBlock,
    market: marketBlock,
    liquidity:
      market.liquidity.score === null
        ? {
            status: 'insufficient_data',
            phase: 3,
            reason:
              'Liquidity needs sales history. No comparable sales are stored for this card.',
            ...market.liquidity,
          }
        : {
            status: market.liquidity.sales_365d >= 6 ? 'ok' : 'partial',
            phase: null,
            reason:
              market.liquidity.sales_365d >= 6
                ? null
                : `Based on ${market.liquidity.sales_365d} sale(s) in a year — a thin basis for a score.`,
            ...market.liquidity,
          },
    trend: buildTrendBlock(market),
    grading_options: optionsBlock,
    expected_outcomes: {
      status: 'not_implemented',
      phase: 5,
      reason: 'Expected value needs grade probabilities and graded market prices.',
      outcomes: [],
    },
    recommendation,
    explanation,
    blockers,
    // The weakest link, not the average: a perfect assessment with two sales
    // behind it is still a two-sale answer.
    data_confidence: [
      complete >= 0.85 ? 'high' : complete >= 0.5 ? 'medium' : assessment ? 'low' : 'none',
      gradeBlock.confidence,
      marketBlock.raw?.confidence ?? 'none',
    ].sort((a, b) => rank(a) - rank(b))[0] as CardEvaluation['data_confidence'],
  }
}

export function buildSummary(
  cards: Card[],
  conditions: Map<string, ConditionAssessment>,
  companies: GradingCompany[],
  currency: string,
  prices: MarketPrice[] = [],
  salesStored = 0,
): CollectionSummary {
  // Best raw value per card, in order of how close each source is to what the
  // user would actually get. Purchase price is last on purpose: it is what the
  // card cost, which says nothing about what it is worth now.
  const rawPrice = (card: Card) =>
    prices.find((price) => price.catalog_key === card.catalog_key && price.grade_label === 'raw')
  const marketValue = (card: Card) => {
    const price = rawPrice(card)
    return price ? (price.user_value ?? price.realistic_sale ?? price.median) : null
  }
  const bestValue = (card: Card) =>
    card.user_raw_value ?? marketValue(card) ?? card.purchase_price ?? null
  const marketValued = cards.filter(
    (card) => card.user_raw_value === null && marketValue(card) !== null,
  ).length
  const totalCards = cards.length
  const copies = cards.reduce((sum, card) => sum + card.quantity, 0)
  const withImages = cards.filter((card) => card.images.length > 0).length
  const withCondition = cards.filter((card) => conditions.has(card.id)).length
  const readyToAnalyse = cards.filter(
    (card) => (conditions.get(card.id)?.scores.completeness ?? 0) >= 0.5,
  ).length

  const purchaseTotal = cards.reduce(
    (sum, card) => sum + (card.purchase_price ?? 0) * card.quantity,
    0,
  )
  const userTotal = cards.reduce((sum, card) => sum + (card.user_raw_value ?? 0) * card.quantity, 0)
  const knownRaw = cards.reduce((sum, card) => sum + (bestValue(card) ?? 0) * card.quantity, 0)
  const cardsWithValue = cards.filter((card) => bestValue(card) !== null).length

  const byStatus: Record<string, number> = {}
  for (const card of cards) byStatus[card.status] = (byStatus[card.status] ?? 0) + 1

  const bySetMap = new Map<string, { cards: number; value: number }>()
  for (const card of cards) {
    const label = card.set_name ?? card.set_code ?? 'Unassigned'
    const entry = bySetMap.get(label) ?? { cards: 0, value: 0 }
    entry.cards += 1
    entry.value += card.user_raw_value ?? card.purchase_price ?? 0
    bySetMap.set(label, entry)
  }
  const bySet = [...bySetMap.entries()]
    .map(([set, entry]) => ({ set, cards: entry.cards, value: entry.value }))
    .sort((a, b) => b.cards - a.cards)
    .slice(0, 12)

  const decisions = {
    grade: 0,
    grade_if_batch_filled: 0,
    sell_raw: 0,
    keep_raw: 0,
    hold: 0,
    do_not_grade: 0,
    insufficient_data: 0,
  } as CollectionSummary['decisions']
  let overridden = 0
  for (const card of cards) {
    if (card.decision_override) {
      decisions[card.decision_override] = (decisions[card.decision_override] ?? 0) + 1
      overridden++
    }
  }
  decisions.insufficient_data = Math.max(totalCards - overridden, 0)
  decisions.status = 'insufficient_data'
  decisions.reason =
    'Decisions shown are your own overrides. Engine-generated decisions need grade probabilities and market data.'

  const today = new Date()
  const weekAgo = new Date(today.getTime() - 7 * 86_400_000)
  const pricedTiers = companies.reduce(
    (sum, company) => sum + company.tiers.filter((tier) => tier.active && tier.price > 0).length,
    0,
  )

  return {
    totals: {
      cards: totalCards,
      copies,
      distinct_sets: new Set(cards.map((card) => card.set_code).filter(Boolean)).size,
      with_images: withImages,
      with_condition: withCondition,
      ready_to_analyse: readyToAnalyse,
    },
    values: {
      currency,
      purchase_total: Math.round(purchaseTotal * 100) / 100,
      user_valued_total: Math.round(userTotal * 100) / 100,
      known_raw_value: Math.round(knownRaw * 100) / 100,
      cards_with_value: cardsWithValue,
      potential_graded_value: null,
      potential_uplift: null,
      expected_profit: null,
      values_status: 'partial',
      values_reason: marketValued
        ? `Raw value uses your own figure where you set one, a market valuation for ${marketValued} card(s), and the purchase price otherwise. Graded upside and expected profit need the grading-cost and decision engines.`
        : 'Raw value is your own figure or purchase price — no card has comparable sales yet. Graded upside and expected profit need the grading-cost and decision engines.',
    },
    decisions,
    by_status: byStatus,
    by_set: bySet,
    recent_additions: cards.filter((card) => new Date(card.created_at) >= weekAgo).length,
    review_due: cards.filter((card) => card.review_after && new Date(card.review_after) <= today)
      .length,
    readiness: [
      {
        key: 'photographed',
        label: 'Photographed',
        count: withImages,
        total: totalCards,
        action: 'Upload front and back images',
      },
      {
        key: 'assessed',
        label: 'Condition assessed',
        count: withCondition,
        total: totalCards,
        action: 'Record centering and defects',
      },
      {
        key: 'valued',
        label: 'Raw value known',
        count: cardsWithValue,
        total: totalCards,
        action: 'Add a purchase price or your own estimate',
      },
      {
        key: 'market_data',
        label: 'Comparable sales stored',
        // Cards covered, not sales counted: readiness is "how much of the
        // collection can be analysed".
        count: cards.filter((card) => rawPrice(card) !== undefined).length,
        total: totalCards,
        action: 'Import or enter sold comparables',
      },
    ],
    market_sales_stored: salesStored,
    priced_tiers_configured: pricedTiers,
  }
}
