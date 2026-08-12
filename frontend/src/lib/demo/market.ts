/**
 * The market engine, ported for the browser-only demo.
 *
 * A line-for-line companion to `backend/app/services/market_service.py` and
 * `sales_import.py`. It exists because GitHub Pages has no server to run the
 * Python, and it is the only place engine logic is duplicated — `VITE_DEMO` is
 * a compile-time constant, so a normal build never bundles this file.
 *
 * Money is in **minor units** here, exactly as it is server-side, so the same
 * rounding produces the same pennies. The demo API converts at its edge.
 *
 * If the Python changes, this must change with it. The behaviours worth keeping
 * in step are the ones the tests name: excluded is never deleted, only positive
 * evidence excludes, the outlier fence needs a sample, the reported window
 * widens when the valuation falls back outside it, and a trend is measured
 * within one grade.
 */

import type {
  ExclusionReason,
  ImportResult,
  MarketLiquidity,
  MarketPrice,
  MarketSale,
  MarketSummary,
  MarketTrend,
} from '@/lib/types'

export interface MarketParameters {
  windowDays: number
  halfLifeDays: number
  outlierIqrMultiplier: number
  minSalesHigh: number
  minSalesMedium: number
  quickSaleDiscountPct: number
  minSalesForOutliers: number
}

export const DEFAULT_PARAMS: MarketParameters = {
  windowDays: 90,
  halfLifeDays: 45,
  outlierIqrMultiplier: 1.5,
  minSalesHigh: 20,
  minSalesMedium: 8,
  quickSaleDiscountPct: 10,
  minSalesForOutliers: 8,
}

export function paramsFromSettings(values: Record<string, unknown>): MarketParameters {
  const num = (key: string, fallback: number) => {
    const value = Number(values[key])
    return Number.isFinite(value) ? value : fallback
  }
  return {
    windowDays: num('market_window_days', 90),
    halfLifeDays: num('recency_half_life_days', 45),
    outlierIqrMultiplier: num('outlier_iqr_multiplier', 1.5),
    minSalesHigh: num('min_sales_high_confidence', 20),
    minSalesMedium: num('min_sales_medium_confidence', 8),
    quickSaleDiscountPct: num('quick_sale_discount_pct', 10),
    minSalesForOutliers: 8,
  }
}

const DAY = 86_400_000

export const toMinor = (major: number) => Math.round(major * 100)
export const toMajor = (minor: number | null) => (minor === null ? null : minor / 100)

const dayNumber = (iso: string) => Math.floor(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / DAY)
const todayNumber = () => dayNumber(new Date().toISOString())
const daysBetween = (laterIso: string, earlierIso: string) =>
  dayNumber(laterIso) - dayNumber(earlierIso)

/* --- Statistics ----------------------------------------------------------- */

export function percentile(values: number[], fraction: number): number | null {
  if (!values.length) return null
  const ordered = [...values].sort((a, b) => a - b)
  if (ordered.length === 1) return ordered[0]
  const position = fraction * (ordered.length - 1)
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  if (lower === upper) return ordered[lower]
  return Math.round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))
}

export const median = (values: number[]) => percentile(values, 0.5)

/** A sale from last week says more about today's price than one from March. */
export function recencyWeight(saleDate: string, today: string, halfLifeDays: number): number {
  const age = Math.max(daysBetween(today, saleDate), 0)
  return 0.5 ** (age / Math.max(halfLifeDays, 1))
}

export function weightedMedian(pairs: [number, number][]): number | null {
  if (!pairs.length) return null
  const ordered = [...pairs].sort((a, b) => a[0] - b[0])
  const total = ordered.reduce((sum, [, weight]) => sum + weight, 0)
  if (total <= 0) return median(ordered.map(([value]) => value))
  let running = 0
  for (const [value, weight] of ordered) {
    running += weight
    if (running >= total / 2) return value
  }
  return ordered[ordered.length - 1][0]
}

/** Null below four values: a fence drawn by three points is drawn by nothing. */
export function iqrBounds(values: number[], multiplier: number): [number, number] | null {
  if (values.length < 4) return null
  const low = percentile(values, 0.25)
  const high = percentile(values, 0.75)
  if (low === null || high === null) return null
  const spread = high - low
  return [Math.round(low - spread * multiplier), Math.round(high + spread * multiplier)]
}

/* --- Valuation ------------------------------------------------------------ */

export type Confidence = 'none' | 'low' | 'medium' | 'high'

function confidenceFor(
  sampleSize: number,
  lastSaleAt: string | null,
  today: string,
  params: MarketParameters,
): Confidence {
  if (sampleSize === 0) return 'none'
  const stale = lastSaleAt ? daysBetween(today, lastSaleAt) : 10_000
  if (sampleSize >= params.minSalesHigh && stale <= 45) return 'high'
  if (sampleSize >= params.minSalesMedium && stale <= 120) return 'medium'
  if (sampleSize >= 3) return 'low'
  return 'none'
}

export interface Valuation {
  medianMinor: number | null
  weightedMedianMinor: number | null
  meanMinor: number | null
  lowQuartileMinor: number | null
  highQuartileMinor: number | null
  lastSaleMinor: number | null
  realisticMinor: number | null
  quickMinor: number | null
  sampleSize: number
  windowDays: number
  lastSaleAt: string | null
  confidence: Confidence
}

export function valueSales(
  sales: MarketSale[],
  params: MarketParameters,
  today: string,
): Valuation {
  const empty: Valuation = {
    medianMinor: null,
    weightedMedianMinor: null,
    meanMinor: null,
    lowQuartileMinor: null,
    highQuartileMinor: null,
    lastSaleMinor: null,
    realisticMinor: null,
    quickMinor: null,
    sampleSize: 0,
    windowDays: params.windowDays,
    lastSaleAt: null,
    confidence: 'none',
  }
  if (!sales.length) return empty

  const inWindow = sales.filter((sale) => daysBetween(today, sale.sale_date) <= params.windowDays)
  const considered = inWindow.length ? inWindow : sales

  let windowDays = params.windowDays
  if (!inWindow.length) {
    // The window is evidence, not configuration. Reporting 90 days while
    // valuing sales that span nine months is the false precision this engine
    // exists to avoid.
    const oldest = considered.reduce(
      (max, sale) => Math.max(max, daysBetween(today, sale.sale_date)),
      0,
    )
    windowDays = Math.max(oldest, params.windowDays)
  }

  const prices = considered.map((sale) => toMinor(sale.sale_price))
  const latest = considered.reduce((newest, sale) =>
    sale.sale_date > newest.sale_date ? sale : newest,
  )
  const weighted = weightedMedian(
    considered.map(
      (sale) =>
        [toMinor(sale.sale_price), recencyWeight(sale.sale_date, today, params.halfLifeDays)] as [
          number,
          number,
        ],
    ),
  )
  const medianMinor = median(prices)
  const lowQuartile = percentile(prices, 0.25)
  const realistic = weighted ?? medianMinor

  let quick: number | null = null
  if (realistic !== null) {
    const discounted = realistic - Math.round((realistic * params.quickSaleDiscountPct) / 100)
    quick = Math.min(discounted, lowQuartile ?? discounted)
  }

  return {
    medianMinor,
    weightedMedianMinor: weighted,
    meanMinor: Math.round(prices.reduce((sum, price) => sum + price, 0) / prices.length),
    lowQuartileMinor: lowQuartile,
    highQuartileMinor: percentile(prices, 0.75),
    lastSaleMinor: toMinor(latest.sale_price),
    realisticMinor: realistic,
    quickMinor: quick,
    sampleSize: considered.length,
    windowDays,
    lastSaleAt: latest.sale_date,
    confidence: confidenceFor(considered.length, latest.sale_date, today, params),
  }
}

/* --- Liquidity ------------------------------------------------------------ */

const FREQUENCY_ANCHORS: [number, number][] = [
  [0, 0],
  [1, 2],
  [3, 4],
  [6, 5.5],
  [12, 7],
  [25, 8.5],
  [50, 10],
]
const RECENCY_ANCHORS: [number, number][] = [
  [0, 10],
  [7, 9],
  [14, 8],
  [30, 6],
  [60, 4],
  [120, 2],
  [365, 0],
]
const DEPTH_ANCHORS: [number, number][] = [
  [0, 0],
  [0.25, 4],
  [0.5, 6],
  [1, 8],
  [2, 10],
]

function interpolate(anchors: [number, number][], value: number): number {
  if (value <= anchors[0][0]) return anchors[0][1]
  for (let index = 0; index < anchors.length - 1; index += 1) {
    const [x0, y0] = anchors[index]
    const [x1, y1] = anchors[index + 1]
    if (value <= x1) {
      if (x1 === x0) return y1
      return y0 + ((value - x0) / (x1 - x0)) * (y1 - y0)
    }
  }
  return anchors[anchors.length - 1][1]
}

function band(score: number): string {
  if (score >= 9) return 'very_liquid'
  if (score >= 7) return 'liquid'
  if (score >= 5) return 'moderate'
  if (score >= 3) return 'illiquid'
  return 'very_illiquid'
}

/**
 * Score frequency alone, from a source's yearly count.
 *
 * The counts stay at zero deliberately: `sales_90d` means "sales you hold
 * records for", and filling it from an annual figure would turn a derivation
 * into a claim about your data. What gets derived is the reading, and it says
 * so.
 *
 * Recency is simply absent — an annual total cannot tell a card selling
 * steadily all year from one that sold forty times in January and has not moved
 * since. The score is computed over the components that exist, exactly as it
 * already is when active listings are unknown.
 */
/**
 * The most a reading with no recency behind it may score — one notch under
 * `very_liquid`, which begins at 9.
 *
 * Normalising over the components that exist makes a partial reading easier to
 * max out than a complete one: with sales you need frequency and recency both
 * perfect to reach ten, and with a yearly count you need only frequency. "Very
 * liquid" here means you can realise the money now, which is precisely what
 * recency would evidence and an annual total cannot.
 */
const NO_RECENCY_CEILING = 8.9

function fromReportedVolume(
  result: MarketLiquidity,
  annualVolume: number | null,
): MarketLiquidity {
  if (annualVolume === null) return result

  result.basis = 'reported_volume'
  result.sales_per_month = Math.round((annualVolume / 12) * 100) / 100
  result.median_days_between_sales = annualVolume
    ? Math.round((365 / annualVolume) * 10) / 10
    : null

  // The frequency anchors are calibrated on a 90-day count, so the annual
  // figure is put on that scale rather than the anchors being duplicated.
  const implied90d = (annualVolume * 90) / 365
  const components: [number, number][] = [[interpolate(FREQUENCY_ANCHORS, implied90d), 0.45]]
  if (result.active_listings) {
    result.sold_to_active_ratio = Math.round((implied90d / result.active_listings) * 1000) / 1000
    components.push([interpolate(DEPTH_ANCHORS, result.sold_to_active_ratio), 0.2])
  }

  const weight = components.reduce((sum, [, share]) => sum + share, 0)
  const score = components.reduce((sum, [value, share]) => sum + value * share, 0) / weight
  result.score = Math.round(Math.min(Math.max(score, 0), NO_RECENCY_CEILING) * 10) / 10
  result.band = band(result.score)
  return result
}

export function measureLiquidity(
  sales: MarketSale[],
  today: string,
  activeListings: number | null = null,
  annualVolume: number | null = null,
): MarketLiquidity {
  const blank: MarketLiquidity = {
    score: null,
    band: 'unknown',
    sales_7d: 0,
    sales_30d: 0,
    sales_90d: 0,
    sales_365d: 0,
    days_since_last_sale: null,
    active_listings: activeListings,
    sold_to_active_ratio: null,
    median_days_between_sales: null,
    sales_per_month: null,
    basis: null,
    annual_volume: annualVolume,
  }
  // Your own sales win, the same precedence prices follow. They carry a date
  // each, so they answer both how often this trades and whether it traded
  // recently; a yearly count answers the first better and the second not at all.
  if (!sales.length) return fromReportedVolume(blank, annualVolume)

  const within = (days: number) =>
    sales.filter((sale) => daysBetween(today, sale.sale_date) <= days).length

  const dates = sales.map((sale) => sale.sale_date).sort()
  const gaps: number[] = []
  for (let index = 1; index < dates.length; index += 1) {
    gaps.push(daysBetween(dates[index], dates[index - 1]))
  }

  const result: MarketLiquidity = {
    ...blank,
    basis: 'sales',
    sales_7d: within(7),
    sales_30d: within(30),
    sales_90d: within(90),
    sales_365d: within(365),
    days_since_last_sale: daysBetween(today, dates[dates.length - 1]),
    median_days_between_sales: gaps.length ? (percentile(gaps, 0.5) ?? 0) : null,
  }
  result.sales_per_month = result.sales_365d
    ? Math.round((result.sales_365d / 12) * 100) / 100
    : 0

  const components: [number, number][] = [
    [interpolate(FREQUENCY_ANCHORS, result.sales_90d), 0.45],
    [interpolate(RECENCY_ANCHORS, result.days_since_last_sale ?? 365), 0.35],
  ]
  if (activeListings !== null && activeListings > 0) {
    result.sold_to_active_ratio = Math.round((result.sales_90d / activeListings) * 100) / 100
    components.push([interpolate(DEPTH_ANCHORS, result.sold_to_active_ratio), 0.2])
  }

  const weight = components.reduce((sum, [, share]) => sum + share, 0)
  const score = components.reduce((sum, [value, share]) => sum + value * share, 0) / weight
  result.score = Math.round(score * 10) / 10
  result.band = band(result.score)
  return result
}

/* --- Trend ---------------------------------------------------------------- */

const HORIZONS = [7, 30, 90, 180, 365] as const

/**
 * Pick the one grade a trend can honestly be measured on. Pooling grades makes
 * the trend measure *sales mix* rather than price.
 */
export function trendSales(sales: MarketSale[]): { sales: MarketSale[]; label: string | null } {
  if (!sales.length) return { sales: [], label: null }
  const byLabel = new Map<string, MarketSale[]>()
  for (const sale of sales) {
    const bucket = byLabel.get(sale.grade_label) ?? []
    bucket.push(sale)
    byLabel.set(sale.grade_label, bucket)
  }
  const raw = byLabel.get('raw') ?? []
  if (raw.length >= 4) return { sales: raw, label: 'raw' }

  let best: [string, MarketSale[]] | null = null
  for (const entry of byLabel) {
    if (!best || entry[1].length > best[1].length) best = entry
  }
  return { sales: best![1], label: best![0] }
}

function changeOver(
  sales: MarketSale[],
  horizon: number,
  today: string,
): number | null {
  const recent = sales.filter((sale) => daysBetween(today, sale.sale_date) <= horizon)
  const prior = sales.filter((sale) => {
    const age = daysBetween(today, sale.sale_date)
    return age > horizon && age <= horizon * 2
  })
  if (recent.length < 2 || prior.length < 2) return null

  const recentValue = median(recent.map((sale) => toMinor(sale.sale_price)))
  const priorValue = median(prior.map((sale) => toMinor(sale.sale_price)))
  if (!recentValue || !priorValue) return null
  return Math.round(((recentValue - priorValue) / priorValue) * 100 * 100) / 100
}

function direction(change: number): string {
  if (change >= 15) return 'strong_up'
  if (change >= 5) return 'up'
  if (change > -5) return 'stable'
  if (change > -15) return 'down'
  return 'strong_down'
}

export function measureTrend(
  sales: MarketSale[],
  today: string,
  params: MarketParameters,
): MarketTrend {
  const result: MarketTrend = {
    direction: 'insufficient_data',
    confidence: 'none',
    grade_label: sales.length ? sales[0].grade_label : null,
    change_7d_pct: null,
    change_30d_pct: null,
    change_90d_pct: null,
    change_180d_pct: null,
    change_365d_pct: null,
    sample_size: sales.length,
  }
  if (!sales.length) return result

  const changes = new Map<number, number | null>()
  for (const horizon of HORIZONS) {
    const change = changeOver(sales, horizon, today)
    changes.set(horizon, change)
  }
  result.change_7d_pct = changes.get(7) ?? null
  result.change_30d_pct = changes.get(30) ?? null
  result.change_90d_pct = changes.get(90) ?? null
  result.change_180d_pct = changes.get(180) ?? null
  result.change_365d_pct = changes.get(365) ?? null

  // The longest horizon with real data leads, so a quiet fortnight is not a crash.
  const headline = [90, 180, 30, 365, 7]
    .map((horizon) => changes.get(horizon))
    .find((change) => change !== null && change !== undefined)
  if (headline === undefined || headline === null) return result

  result.direction = direction(headline)
  result.confidence =
    sales.length >= params.minSalesHigh
      ? 'high'
      : sales.length >= params.minSalesMedium
        ? 'medium'
        : 'low'
  return result
}

/* --- Exclusion heuristics ------------------------------------------------- */

interface TitleRule {
  reason: ExclusionReason
  label: string
  pattern: RegExp
}

const rule = (reason: ExclusionReason, label: string, source: string): TitleRule => ({
  reason,
  label,
  pattern: new RegExp(source, 'i'),
})

const TITLE_RULES: TitleRule[] = [
  rule(
    'lot_or_bundle',
    'mentions a lot, bundle or multiple cards',
    [
      '\\bjob[ -]?lots?\\b',
      '\\blots?\\s+of\\b',
      '\\bbundles?\\b',
      '\\bbulk\\b',
      '\\bmystery\\b',
      '\\brandom\\b',
      '\\bwholesale\\b',
      '\\b\\d{2,}\\s*(?:x\\s*)?cards?\\b',
      '\\bx\\s?\\d{2,}\\b',
      '\\bset\\s+of\\s+\\d+\\b',
      '\\bplaysets?\\b',
      '\\bmaster\\s+set\\b',
      '\\bcomplete\\s+set\\b',
      '\\bbooster\\s+box\\b',
      '\\betb\\b',
      '\\belite\\s+trainer\\b',
      '\\bsealed\\b',
    ].join('|'),
  ),
  rule(
    'damaged',
    'describes damage or heavy play',
    [
      '\\bdamaged?\\b',
      '\\bdmg\\b',
      '\\bcreased?\\b',
      '\\bcreasing\\b',
      '\\bbent\\b',
      '\\btorn\\b',
      '\\bripped\\b',
      '\\bwater\\s?damage',
      '\\bheavily\\s+played\\b',
      '\\bpoor\\s+condition\\b',
      '\\bplayed\\s+condition\\b',
      '\\bfor\\s+parts\\b',
      '\\bas\\s+is\\b',
    ].join('|'),
  ),
  rule(
    'suspected_fake',
    'looks like a custom, proxy or counterfeit',
    [
      '\\bfakes?\\b',
      '\\bproxy\\b',
      '\\bproxies\\b',
      '\\breplica\\b',
      '\\bcounterfeit\\b',
      '\\bcustom\\b',
      '\\borica\\b',
      '\\bfan\\s?made\\b',
      '\\bnot\\s+official\\b',
      '\\bmetal\\s+card\\b',
    ].join('|'),
  ),
  rule(
    'best_offer_unknown',
    'sold via best offer, so the price shown is not the price paid',
    '\\bbest\\s+offer\\b|\\bobo\\b',
  ),
]

const LANGUAGE_PATTERNS: [string, RegExp][] = [
  ['japanese', /\b(?:japanese|japan|jpn|jp)\b/i],
  ['korean', /\b(?:korean|korea|kor)\b/i],
  ['chinese', /\b(?:chinese|china|s-chinese|t-chinese)\b/i],
  ['german', /\b(?:german|deutsch)\b/i],
  ['french', /\b(?:french|francais|français)\b/i],
  ['spanish', /\b(?:spanish|espanol|español)\b/i],
  ['italian', /\b(?:italian|italiano)\b/i],
  ['portuguese', /\b(?:portuguese|portugues|português)\b/i],
  ['russian', /\brussian\b/i],
  ['thai', /\bthai\b/i],
  ['indonesian', /\bindonesian\b/i],
  ['english', /\benglish\b/i],
]

const VARIANT_PATTERNS: [string, RegExp][] = [
  ['reverse-holo', /\breverse\s*(?:holo|foil)?\b|\brev\s+holo\b/i],
  ['alternate-art', /\balt(?:ernate)?\s*art\b|\balt\b/i],
  ['full-art', /\bfull\s*art\b|\bfa\b/i],
  ['secret-rare', /\bsecret\s*rare\b/i],
  ['rainbow-rare', /\brainbow\b/i],
  ['gold', /\bgold\s+(?:card|rare|secret)\b/i],
  ['promo', /\bpromo\b/i],
  ['first-edition', /\b1st\s*ed(?:ition)?\b|\bfirst\s+edition\b/i],
  ['shadowless', /\bshadowless\b/i],
]

const PRINTING_PATTERNS: [string, RegExp][] = [
  ['first-edition', /\b1st\s*ed(?:ition)?\b|\bfirst\s+edition\b/i],
  ['shadowless', /\bshadowless\b/i],
]

const VARIANT_ALIASES: Record<string, string> = {
  '1st-edition': 'first-edition',
  '1st-ed': 'first-edition',
  'first-ed': 'first-edition',
  reverse: 'reverse-holo',
  'reverse-foil': 'reverse-holo',
  'alt-art': 'alternate-art',
  alt: 'alternate-art',
  fa: 'full-art',
  secret: 'secret-rare',
  rainbow: 'rainbow-rare',
  'gold-secret': 'gold',
}

const GENERIC_VARIANTS = new Set(['', 'standard', 'normal', 'regular', 'unlimited', 'unknown'])

const GRADE_IN_TITLE = /\b(psa|cgc|bgs|beckett|sgc|ace|tag|gma|hga)\s*[-:]?\s*(10|[1-9](?:\.5)?)\b/i
const RAW_IN_TITLE = /\b(?:raw|ungraded|un-graded)\b/i

const normalise = (value: string | null | undefined) => (value ?? '').trim().toLowerCase()

function variantToken(value: string | null | undefined): string {
  const slug = normalise(value).replace(/\s+/g, '-')
  return VARIANT_ALIASES[slug] ?? slug
}

export function parseGradeFromTitle(title: string | null): [string, number] | null {
  if (!title) return null
  const match = GRADE_IN_TITLE.exec(title)
  if (!match) return null
  const company = match[1].toUpperCase()
  return [company === 'BECKETT' ? 'BGS' : company, Number(match[2])]
}

export function gradeLabel(companyCode: string | null, grade: number | null): string {
  if (grade === null || !companyCode) return 'raw'
  return `${companyCode.toUpperCase()} ${grade}`
}

export interface SaleContext {
  language?: string | null
  variant?: string | null
  printing?: string | null
}

export function classify(
  title: string | null,
  context: SaleContext,
  lotSize = 1,
  label = 'raw',
): { reason: ExclusionReason; explanation: string } | null {
  if (lotSize > 1) {
    return {
      reason: 'lot_or_bundle',
      explanation: `Listed as ${lotSize} cards, so the price is not for one card.`,
    }
  }
  const text = title ?? ''
  if (!text.trim()) return null

  for (const item of TITLE_RULES) {
    const match = item.pattern.exec(text)
    if (match) {
      return { reason: item.reason, explanation: `Title ${item.label} (matched “${match[0]}”).` }
    }
  }

  // Language: only a positively named *other* language counts. Silence is not
  // evidence, and a title naming several is ambiguous.
  const wantLanguage = normalise(context.language)
  if (wantLanguage) {
    const found = LANGUAGE_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([name]) => name)
    if (found.length && !found.includes(wantLanguage)) {
      return {
        reason: 'wrong_language',
        explanation: `Title names a different language; this card is ${context.language}.`,
      }
    }
  }

  const declared = new Set([variantToken(context.printing), variantToken(context.variant)])
  const printings = PRINTING_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([n]) => n)
  if (printings.length && !printings.some((name) => declared.has(name))) {
    return {
      reason: 'wrong_variant',
      explanation: `Title names a different printing; this card is ${context.printing ?? 'unlimited'}.`,
    }
  }

  const wantVariant = variantToken(context.variant)
  if (wantVariant) {
    const found = VARIANT_PATTERNS.filter(([, pattern]) => pattern.test(text)).map(([name]) => name)
    const accepted = new Set([wantVariant, variantToken(context.printing)].filter(Boolean))
    if (found.length && !found.some((name) => accepted.has(name))) {
      if (GENERIC_VARIANTS.has(wantVariant) || found.length === 1) {
        return {
          reason: 'wrong_variant',
          explanation: `Title names a different variant; this card is ${context.variant ?? 'standard'}.`,
        }
      }
    }
  }

  const parsed = parseGradeFromTitle(text)
  const wanted = normalise(label)
  if (parsed) {
    if (gradeLabel(parsed[0], parsed[1]).toLowerCase() !== wanted) {
      return {
        reason: 'wrong_grade',
        explanation: `Title names a different grade; this comparison is for ${label}.`,
      }
    }
  } else if (RAW_IN_TITLE.test(text) && wanted !== 'raw') {
    return {
      reason: 'wrong_grade',
      explanation: `Title names a different grade; this comparison is for ${label}.`,
    }
  }

  return null
}

/* --- CSV ------------------------------------------------------------------ */

const COLUMN_ALIASES: Record<string, string[]> = {
  sale_date: ['saledate', 'date', 'solddate', 'datesold', 'enddate', 'endedon', 'soldon'],
  sale_price: ['saleprice', 'price', 'soldprice', 'soldfor', 'amount', 'total', 'pricesold'],
  shipping: ['shipping', 'postage', 'shippingcost', 'delivery', 'deliverycost', 'pandp'],
  currency: ['currency', 'ccy'],
  listing_title: ['listingtitle', 'title', 'item', 'itemtitle', 'name', 'description'],
  platform: ['platform', 'site', 'marketplace', 'venue'],
  grade_label: ['gradelabel', 'slab'],
  grade: ['grade', 'gradevalue'],
  company: ['company', 'grader', 'gradingcompany', 'gradingco', 'gradedby'],
  seller: ['seller', 'sellername', 'vendor'],
  source_url: ['sourceurl', 'url', 'link', 'itemurl', 'listingurl'],
  external_id: ['externalid', 'id', 'itemid', 'listingid', 'orderid', 'itemnumber'],
  lot_size: ['lotsize', 'quantity', 'qty', 'count'],
  bid_count: ['bidcount', 'bids', 'numberofbids'],
  condition_note: ['conditionnote', 'condition', 'conditiondescription'],
  is_auction: ['isauction', 'auction', 'format', 'listingtype', 'buyingformat'],
}

const headerKey = (header: string) => header.toLowerCase().replace(/[^a-z0-9]/g, '')

function canonicalHeader(header: string): string | null {
  const key = headerKey(header)
  for (const [canonical, aliases] of Object.entries(COLUMN_ALIASES)) {
    if (key === headerKey(canonical) || aliases.includes(key)) return canonical
  }
  return null
}

export function parseMoney(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') return toMinor(value)

  let text = String(value)
    .replace(/[£$€¥]|\b(?:gbp|usd|eur|cad|aud|jpy)\b/gi, '')
    .replace(/[^0-9.,-]/g, '')
    .trim()
  if (!text || ['-', '.', ','].includes(text)) return null

  const lastDot = text.lastIndexOf('.')
  const lastComma = text.lastIndexOf(',')
  if (lastDot >= 0 && lastComma >= 0) {
    text = lastComma > lastDot ? text.replace(/\./g, '').replace(',', '.') : text.replace(/,/g, '')
  } else if (lastComma >= 0) {
    const decimals = text.length - lastComma - 1
    text = decimals === 2 ? text.replace(',', '.') : text.replace(/,/g, '')
  }

  const parsed = Number(text)
  return Number.isFinite(parsed) ? toMinor(parsed) : null
}

const MONTHS: Record<string, number> = {
  jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
  jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
}

/** Returns an ISO date string, or null. `dayFirst` decides `03/04/2025`. */
export function parseDate(value: string | null | undefined, dayFirst = true): string | null {
  if (!value) return null
  const text = String(value).replace(/,/g, '').split(/[T ]\d{1,2}:/)[0].trim()
  if (!text) return null

  const iso = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/.exec(text)
  if (iso) return build(Number(iso[1]), Number(iso[2]), Number(iso[3]))

  const named = /^(\d{1,2})[ -]([a-z]{3,})[ -](\d{4})$/i.exec(text)
  if (named) {
    const month = MONTHS[named[2].slice(0, 3).toLowerCase()]
    if (month) return build(Number(named[3]), month, Number(named[1]))
  }
  const namedFirst = /^([a-z]{3,})[ -](\d{1,2})[ -](\d{4})$/i.exec(text)
  if (namedFirst) {
    const month = MONTHS[namedFirst[1].slice(0, 3).toLowerCase()]
    if (month) return build(Number(namedFirst[3]), month, Number(namedFirst[2]))
  }

  const numeric = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$/.exec(text)
  if (numeric) {
    const first = Number(numeric[1])
    const second = Number(numeric[2])
    let year = Number(numeric[3])
    if (year < 100) year += 2000
    // When one number cannot be a month, it decides — otherwise `dayFirst` does.
    const dayIsFirst = first > 12 ? true : second > 12 ? false : dayFirst
    return dayIsFirst ? build(year, second, first) : build(year, first, second)
  }
  return null

  function build(year: number, month: number, day: number): string | null {
    if (month < 1 || month > 12 || day < 1 || day > 31) return null
    const stamp = new Date(Date.UTC(year, month - 1, day))
    if (stamp.getUTCMonth() !== month - 1 || stamp.getUTCDate() !== day) return null
    return stamp.toISOString().slice(0, 10)
  }
}

/** Minimal RFC-4180 reader: quoted fields, doubled quotes, one delimiter. */
function splitRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index]
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"'
          index += 1
        } else quoted = false
      } else field += char
      continue
    }
    if (char === '"') quoted = true
    else if (char === delimiter) {
      row.push(field)
      field = ''
    } else if (char === '\n') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else if (char !== '\r') field += char
  }
  if (field || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows
}

export interface ParsedRow {
  lineNumber: number
  saleDate: string
  salePriceMinor: number
  shippingMinor: number | null
  currency: string | null
  listingTitle: string | null
  platform: string | null
  seller: string | null
  sourceUrl: string | null
  externalId: string | null
  gradeLabel: string | null
  grade: number | null
  companyCode: string | null
  lotSize: number
  conditionNote: string | null
}

export interface RowError {
  line_number: number | null
  message: string
  values: Record<string, string>
}

export function parseCsv(
  text: string,
  dayFirst = true,
): { rows: ParsedRow[]; errors: RowError[] } {
  const stripped = text.replace(/^﻿/, '')
  if (!stripped.trim()) {
    return { rows: [], errors: [{ line_number: null, message: 'The file is empty.', values: {} }] }
  }

  const firstLine = stripped.split('\n')[0]
  const delimiter = [';', '\t', '|'].find(
    (candidate) => firstLine.split(candidate).length > firstLine.split(',').length,
  ) ?? ','

  const grid = splitRows(stripped, delimiter)
  const headers = grid.shift() ?? []
  const mapping = headers.map(canonicalHeader)

  if (!mapping.some(Boolean)) {
    return {
      rows: [],
      errors: [
        {
          line_number: 1,
          message:
            'No recognised columns. A sale date and a price are the minimum; understood names include: ' +
            Object.keys(COLUMN_ALIASES).sort().join(', ') +
            '.',
          values: {},
        },
      ],
    }
  }
  const missing = (['sale_date', 'sale_price'] as const).filter(
    (name) => !mapping.includes(name),
  )
  if (missing.length) {
    return {
      rows: [],
      errors: [
        { line_number: 1, message: `Missing required column(s): ${missing.join(', ')}.`, values: {} },
      ],
    }
  }

  const rows: ParsedRow[] = []
  const errors: RowError[] = []

  grid.forEach((cells, index) => {
    const lineNumber = index + 2
    if (!cells.some((cell) => cell.trim())) return

    const values: Record<string, string> = {}
    mapping.forEach((canonical, column) => {
      if (canonical && column < cells.length) values[canonical] = cells[column].trim()
    })

    const saleDate = parseDate(values.sale_date, dayFirst)
    const salePriceMinor = parseMoney(values.sale_price)
    const problems: string[] = []
    if (!saleDate) problems.push('could not read the sale date')
    if (salePriceMinor === null) problems.push('could not read the price')
    else if (salePriceMinor <= 0) problems.push('the price is zero or negative')
    if (problems.length) {
      const message = problems.join(' and ')
      errors.push({
        line_number: lineNumber,
        message: message.charAt(0).toUpperCase() + message.slice(1) + '.',
        values,
      })
      return
    }

    const gradeText = values.grade ? Number(values.grade.replace(/[^0-9.]/g, '')) : NaN
    rows.push({
      lineNumber,
      saleDate: saleDate!,
      salePriceMinor: salePriceMinor!,
      shippingMinor: parseMoney(values.shipping),
      currency: values.currency ? values.currency.toUpperCase() : null,
      listingTitle: values.listing_title || null,
      platform: values.platform || null,
      seller: values.seller || null,
      sourceUrl: values.source_url || null,
      externalId: values.external_id || null,
      gradeLabel: values.grade_label || null,
      grade: Number.isFinite(gradeText) ? gradeText : null,
      companyCode: values.company ? values.company.toUpperCase() : null,
      lotSize: Number(values.lot_size) > 0 ? Number(values.lot_size) : 1,
      conditionNote: values.condition_note || null,
    })
  })

  return { rows, errors }
}

/* --- Outliers, pricing and the summary ------------------------------------ */

export function markOutliers(sales: MarketSale[], params: MarketParameters): number {
  let flagged = 0
  const labels = new Set(sales.map((sale) => sale.grade_label))

  for (const label of labels) {
    // Per grade: a PSA 10 at ten times the raw price is the normal state of
    // affairs, not an outlier.
    const forLabel = sales.filter((sale) => sale.grade_label === label)
    const candidates = forLabel.filter(
      (sale) => !sale.is_excluded || sale.exclusion_reason === 'price_outlier',
    )

    if (candidates.length < params.minSalesForOutliers) {
      for (const sale of candidates) {
        if (sale.exclusion_reason === 'price_outlier') {
          sale.is_excluded = false
          sale.exclusion_reason = null
          sale.excluded_by = null
          sale.is_outlier = false
        }
      }
      continue
    }

    const bounds = iqrBounds(candidates.map((sale) => toMinor(sale.sale_price)), params.outlierIqrMultiplier)
    if (!bounds) continue
    const [low, high] = bounds
    for (const sale of candidates) {
      if (sale.excluded_by === 'user') continue
      const price = toMinor(sale.sale_price)
      const outside = price < low || price > high
      if (outside && !sale.is_outlier) {
        sale.is_outlier = true
        sale.is_excluded = true
        sale.exclusion_reason = 'price_outlier'
        sale.excluded_by = 'system'
        flagged += 1
      } else if (!outside && sale.is_outlier) {
        sale.is_outlier = false
        sale.is_excluded = false
        sale.exclusion_reason = null
        sale.excluded_by = null
      }
    }
  }
  return flagged
}

export function premiumVsRawPct(raw: MarketPrice | undefined, graded: MarketPrice): number | null {
  if (!raw) return null
  const base = raw.realistic_sale ?? raw.median
  const value = graded.realistic_sale ?? graded.median
  if (!base || !value) return null
  return Math.round(((value - base) / base) * 100 * 10) / 10
}

/**
 * Recompute every grade's valuation for one identity. Prices are keyed on
 * `catalog_key` + `grade_label`, and existing rows are updated in place so an
 * override the user set survives a reprice.
 */
export function recompute(
  catalogKey: string,
  sales: MarketSale[],
  existing: MarketPrice[],
  params: MarketParameters,
  currency: string,
): MarketPrice[] {
  const today = new Date().toISOString().slice(0, 10)
  const usable = sales.filter((sale) => sale.catalog_key === catalogKey && !sale.is_excluded)
  const labels = [...new Set(usable.map((sale) => sale.grade_label))].sort((a, b) =>
    a === 'raw' ? -1 : b === 'raw' ? 1 : a.localeCompare(b),
  )

  const kept: MarketPrice[] = []
  for (const label of labels) {
    const forLabel = usable.filter((sale) => sale.grade_label === label)
    const valuation = valueSales(forLabel, params, today)
    const first = forLabel[0]

    let row = existing.find(
      (price) => price.catalog_key === catalogKey && price.grade_label === label,
    )
    if (!row) {
      row = {
        id: `price-${catalogKey}-${label}`.replace(/[^a-zA-Z0-9-]/g, '-'),
        catalog_key: catalogKey,
        company_id: null,
        grade: null,
        grade_label: label,
        currency,
        median: null,
        weighted_median: null,
        mean: null,
        low_quartile: null,
        high_quartile: null,
        last_sale: null,
        realistic_sale: null,
        quick_sale: null,
        sample_size: 0,
        window_days: null,
        last_sale_at: null,
        confidence: 'none',
        computed_at: null,
        user_value: null,
        user_value_note: null,
        premium_vs_raw_pct: null,
      }
      existing.push(row)
    }

    row.company_id = first.company_id
    row.grade = first.grade
    row.currency = currency
    row.median = toMajor(valuation.medianMinor)
    row.weighted_median = toMajor(valuation.weightedMedianMinor)
    row.mean = toMajor(valuation.meanMinor)
    row.low_quartile = toMajor(valuation.lowQuartileMinor)
    row.high_quartile = toMajor(valuation.highQuartileMinor)
    row.last_sale = toMajor(valuation.lastSaleMinor)
    row.realistic_sale = toMajor(valuation.realisticMinor)
    row.quick_sale = toMajor(valuation.quickMinor)
    row.sample_size = valuation.sampleSize
    row.window_days = valuation.windowDays
    row.last_sale_at = valuation.lastSaleAt
    row.confidence = valuation.confidence
    row.computed_at = new Date().toISOString()
    kept.push(row)
  }

  const raw = kept.find((price) => price.grade_label === 'raw')
  for (const row of kept) {
    row.premium_vs_raw_pct = row.grade_label === 'raw' ? null : premiumVsRawPct(raw, row)
  }
  return kept
}

export function summarise(
  catalogKey: string | null,
  sales: MarketSale[],
  prices: MarketPrice[],
  params: MarketParameters,
  currency: string,
): MarketSummary {
  if (!catalogKey) {
    return {
      catalog_key: '',
      currency,
      prices: [],
      liquidity: measureLiquidity([], new Date().toISOString().slice(0, 10)),
      trend: measureTrend([], new Date().toISOString().slice(0, 10), params),
      sale_count: 0,
      excluded_count: 0,
      grade_labels: [],
      computed_at: null,
    }
  }

  const today = new Date().toISOString().slice(0, 10)
  const mine = sales.filter((sale) => sale.catalog_key === catalogKey)
  const usable = mine.filter((sale) => !sale.is_excluded)
  const forKey = prices.filter((price) => price.catalog_key === catalogKey)
  const computed = forKey.map((price) => price.computed_at).filter(Boolean) as string[]

  return {
    catalog_key: catalogKey,
    currency,
    prices: forKey,
    // Liquidity spans every grade: a card whose slabs rarely appear but whose
    // raw copies sell weekly still trades.
    liquidity: measureLiquidity(usable, today),
    trend: measureTrend(trendSales(usable).sales, today, params),
    sale_count: usable.length,
    excluded_count: mine.length - usable.length,
    grade_labels: [...new Set(usable.map((sale) => sale.grade_label))].sort((a, b) =>
      a === 'raw' ? -1 : b === 'raw' ? 1 : a.localeCompare(b),
    ),
    computed_at: computed.length ? computed.sort().pop()! : null,
  }
}

export function blankImportResult(): ImportResult {
  return {
    imported: 0,
    updated: 0,
    skipped: 0,
    excluded: 0,
    outliers_flagged: 0,
    exclusions: {},
    errors: [],
    prices: [],
  }
}

export { todayNumber }
