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
  ConditionAssessment,
  ConditionWrite,
  ExplanationItem,
  FaceDefects,
  GradingCompany,
  GradingOption,
  Severity,
} from '@/lib/types'

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

// Mirrors app/services/evaluation.py
const NOTABLE = new Set(['moderate', 'severe'])

export function buildEvaluation(
  card: Card,
  assessment: ConditionAssessment | undefined,
  companies: GradingCompany[],
  currency: string,
  marketSalesStored: number,
): CardEvaluation {
  const options: GradingOption[] = []
  for (const company of companies.filter((c) => c.active)) {
    const priced = company.tiers.filter((tier) => tier.active && tier.price > 0)
    if (!priced.length) {
      options.push({
        company_id: company.id,
        company_code: company.code,
        company_name: company.name,
        tier_id: null,
        tier_name: null,
        currency: company.currency,
        declared_value: null,
        grading_fee: null,
        allocated_overhead: null,
        total_cost: null,
        turnaround_days: null,
        minimum_cards: 1,
        requires_batch: false,
        membership_required: false,
        available: false,
        blockers: [
          `No priced tier configured for ${company.code}. Add current pricing in Settings → Grading.`,
        ],
      })
      continue
    }
    for (const tier of [...priced].sort((a, b) => a.sort_order - b.sort_order || a.price - b.price)) {
      options.push({
        company_id: company.id,
        company_code: company.code,
        company_name: company.name,
        tier_id: tier.id,
        tier_name: tier.tier_name,
        currency: tier.currency,
        declared_value: null,
        grading_fee: tier.price,
        allocated_overhead: null,
        total_cost: null,
        turnaround_days: tier.turnaround_days,
        minimum_cards: tier.minimum_cards,
        requires_batch: tier.minimum_cards > 1,
        membership_required: tier.membership_required,
        available: true,
        blockers: [],
      })
    }
  }

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
    blockers.push('Run the grade probability model (Phase 2).')
  }

  if (marketSalesStored) {
    explanation.push({
      kind: 'pass',
      text: `${marketSalesStored} comparable sale(s) stored locally.`,
      detail: null,
    })
  } else {
    explanation.push({ kind: 'fail', text: 'No market data for this card.', detail: null })
  }
  blockers.push('Add comparable sales for the raw card and each relevant grade.')

  const available = options.filter((option) => option.available)
  if (available.length) {
    const codes = [...new Set(available.map((option) => option.company_code))].sort()
    explanation.push({
      kind: 'pass',
      text: `Grading tiers configured for ${codes.join(', ')}.`,
      detail: `${available.length} priced tier(s) available.`,
    })
  } else {
    explanation.push({ kind: 'fail', text: 'No priced grading tier configured.', detail: null })
    blockers.push('Enter current pricing for at least one grading company.')
  }

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
      market_raw_value: null,
      best_raw_value: card.user_raw_value,
      raw_value_source: card.user_raw_value !== null ? 'user_override' : null,
      net_raw_sale_value: null,
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
    grade_prediction: {
      status: 'not_implemented',
      phase: 2,
      reason: !assessment
        ? 'No condition assessment recorded yet.'
        : 'The grade probability model arrives with the condition engine.',
      company_code: null,
      kind: null,
      source: null,
      probabilities: [],
      likely_grade: null,
      grade_min: null,
      grade_max: null,
      max_grade_cap: null,
      confidence: 'none',
      caps_applied: [],
    },
    market: {
      status: 'insufficient_data',
      phase: 3,
      reason: 'No market data for this card yet. Add sales manually or enable a data source.',
      currency,
      raw: null,
      graded: [],
      computed_at: null,
      sources: [],
    },
    liquidity: {
      status: 'insufficient_data',
      phase: 3,
      reason: 'Liquidity needs sales history.',
      score: null,
      band: 'unknown',
      sales_7d: null,
      sales_30d: null,
      sales_90d: null,
      sales_365d: null,
      days_since_last_sale: null,
      active_listings: null,
      sold_to_active_ratio: null,
      median_days_between_sales: null,
      sales_per_month: null,
    },
    trend: {
      status: 'insufficient_data',
      phase: 3,
      reason: 'A trend needs a series of sales, not a single price.',
      direction: 'insufficient_data',
      confidence: 'none',
      change_7d_pct: null,
      change_30d_pct: null,
      change_90d_pct: null,
      change_180d_pct: null,
      change_365d_pct: null,
      sample_size: 0,
    },
    grading_options: {
      status: options.length ? 'partial' : 'insufficient_data',
      phase: 4,
      reason: options.length
        ? 'Tier availability only. Declared value, batch allocation and total cost per card arrive with the grading-economics engine.'
        : 'No active grading company with a priced tier is configured.',
      options,
    },
    expected_outcomes: {
      status: 'not_implemented',
      phase: 5,
      reason: 'Expected value needs grade probabilities and graded market prices.',
      outcomes: [],
    },
    recommendation,
    explanation,
    blockers,
    data_confidence: 'none',
  }
}

export function buildSummary(
  cards: Card[],
  conditions: Map<string, ConditionAssessment>,
  companies: GradingCompany[],
  currency: string,
): CollectionSummary {
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
  const knownRaw = cards.reduce(
    (sum, card) => sum + (card.user_raw_value ?? card.purchase_price ?? 0) * card.quantity,
    0,
  )
  const cardsWithValue = cards.filter(
    (card) => card.user_raw_value !== null || card.purchase_price !== null,
  ).length

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
      values_reason:
        'Raw value is your own figure or purchase price. Market valuation, graded upside and expected profit need the market-data and decision engines.',
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
        count: 0,
        total: Math.max(totalCards, 1),
        action: 'Import or enter sold comparables',
      },
    ],
    market_sales_stored: 0,
    priced_tiers_configured: pricedTiers,
  }
}
