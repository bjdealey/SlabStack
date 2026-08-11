/**
 * The decision engine, ported from the backend (spec sections 24-31, 33).
 *
 * The spec's core principle governs every line of it:
 *
 *   Don't optimise for theoretical card value. Optimise for expected,
 *   risk-adjusted, realistically realisable profit after grading, selling,
 *   submission and liquidity costs.
 *
 * Four things follow from that, and they are why this is not `max(value)`:
 *
 * **Expected, not best.** A card that grades a 10 one time in ten and an 8 the
 * other nine is an 8. Every figure is probability-weighted across the whole
 * distribution; the best case is reported separately and labelled.
 *
 * **Realisable, not theoretical.** Profit is measured against *selling it raw*,
 * because that is the alternative you actually have.
 *
 * **Risk-adjusted.** The downside is a percentile of the outcome distribution,
 * not the worst grade on the ladder. Risk tolerance shifts the thresholds
 * rather than the arithmetic.
 *
 * **After liquidity costs.** A slab you cannot sell is not profit. The richer
 * route that loses is surfaced as the alternative, never quietly dropped.
 *
 * Nothing here invents a number: grades with no sales are *unknown* rather than
 * worthless, and the share of the distribution that could be priced travels
 * with every expectation computed from it.
 */

import type { Confidence, Decision } from '@/lib/types'
import { gradeLabelFor } from './economics'
import { toMinor } from './market'

/** Profit levels the spec asks for explicitly (section 25), in major units. */
const PROFIT_LADDER = [25, 50, 100] as const

const CONFIDENCE_ORDER: Confidence[] = ['none', 'low', 'medium', 'high']
const rankOf = (confidence: string) => CONFIDENCE_ORDER.indexOf(confidence as Confidence)

const pounds = (minor: number | null | undefined) =>
  minor === null || minor === undefined
    ? '—'
    : `${minor < 0 ? '−' : ''}£${(Math.abs(minor) / 100).toFixed(2)}`

const pct = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`

/* --- Thresholds and risk tolerance ---------------------------------------- */

export interface Thresholds {
  minimumRoiPct: number
  minimumAbsoluteProfitMinor: number
  minimumProbabilityOfProfit: number
  minimumLiquidityScore: number
  gradingValueFloorMinor: number
  holdRecheckDays: number
  riskTolerance: string
  weights: Record<string, number>
}

const DEFAULT_WEIGHTS: Record<string, number> = {
  profitability: 35,
  grade_probability: 25,
  liquidity: 20,
  trend: 10,
  risk: 10,
}

/**
 * What the user requires before grading is worth it. Risk tolerance shifts
 * these rather than changing any arithmetic: a conservative user needs the
 * grade to land profitably more often, but the expected value is the same
 * number either way.
 */
export function thresholdsFrom(settings: Record<string, unknown>): Thresholds {
  const num = (key: string, fallback: number) => {
    const value = Number(settings[key])
    return Number.isFinite(value) ? value : fallback
  }
  const risk = String(settings.risk_tolerance ?? 'balanced')
  const weights = settings.decision_score_weights as Record<string, number> | undefined

  const thresholds: Thresholds = {
    minimumRoiPct: num('minimum_roi_pct', 25),
    minimumAbsoluteProfitMinor: toMinor(num('minimum_absolute_profit', 25)),
    minimumProbabilityOfProfit: num('minimum_probability_of_profit', 60) / 100,
    minimumLiquidityScore: num('minimum_liquidity_score', 3),
    gradingValueFloorMinor: toMinor(num('grading_value_floor', 15)),
    holdRecheckDays: Math.round(num('hold_recheck_days', 30)),
    riskTolerance: risk,
    weights: weights && Object.keys(weights).length ? { ...weights } : { ...DEFAULT_WEIGHTS },
  }

  // Shift the bar, not the maths.
  if (risk === 'conservative') {
    thresholds.minimumProbabilityOfProfit = Math.min(
      0.95,
      thresholds.minimumProbabilityOfProfit + 0.15,
    )
    thresholds.minimumLiquidityScore += 1.5
  } else if (risk === 'aggressive') {
    thresholds.minimumProbabilityOfProfit = Math.max(
      0.05,
      thresholds.minimumProbabilityOfProfit - 0.15,
    )
    thresholds.minimumLiquidityScore = Math.max(0, thresholds.minimumLiquidityScore - 1)
  }
  return thresholds
}

/**
 * Which percentile counts as "the worst reasonable outcome".
 *
 * A *lower* percentile reaches further into the bad tail, so the conservative
 * reading is the lower number: someone being careful should be shown how bad it
 * can plausibly get, not a milder bad case.
 */
export function downsidePercentile(thresholds: Thresholds): number {
  if (thresholds.riskTolerance === 'conservative') return 0.05
  if (thresholds.riskTolerance === 'aggressive') return 0.2
  return 0.1
}

/* --- The outcome distribution --------------------------------------------- */

export interface Outcome {
  grade: number
  label: string
  probability: number
  grossMinor: number | null
  netMinor: number | null
  /** Profit against selling the card raw today, after the grading cost. */
  profitMinor: number | null
}

export interface OutcomeDistribution {
  outcomes: Outcome[]
  /** Share of the grade distribution with a price. The rest is unknown, not worthless. */
  coverage: number
}

const pricedOf = (distribution: OutcomeDistribution) =>
  distribution.outcomes.filter((item) => item.netMinor !== null)

/** Probability-weighted mean over the priced outcomes only. */
function expectation(
  distribution: OutcomeDistribution,
  pick: (item: Outcome) => number,
): number | null {
  const priced = pricedOf(distribution)
  if (!priced.length || distribution.coverage <= 0) return null
  const total = priced.reduce((sum, item) => sum + pick(item) * item.probability, 0)
  return total / distribution.coverage
}

/**
 * Probability that an outcome satisfies the predicate, **unconditionally**.
 *
 * Deliberately not renormalised over the priced outcomes, unlike the
 * expectations. "This is profitable 100% of the time" is a very different claim
 * from "100% of the 40% of outcomes we can price are profitable", and the first
 * is what a reader hears. So unpriced grades count against it: unknown is not
 * good news.
 */
function probabilityWhere(
  distribution: OutcomeDistribution,
  predicate: (item: Outcome) => boolean,
): number | null {
  const priced = pricedOf(distribution)
  if (!priced.length) return null
  return priced.reduce((sum, item) => (predicate(item) ? sum + item.probability : sum), 0)
}

/**
 * Probability over *every* outcome, priced or not — for questions about the
 * grade itself, which we can answer whether or not that grade has ever sold.
 */
function probabilityOfGrade(
  distribution: OutcomeDistribution,
  predicate: (item: Outcome) => boolean,
): number | null {
  if (!distribution.outcomes.length) return null
  return distribution.outcomes.reduce(
    (sum, item) => (predicate(item) ? sum + item.probability : sum),
    0,
  )
}

/**
 * Profit at a percentile of the distribution. Used for downside and upside
 * instead of the worst and best grades: a 1% chance of a 3 is a tail, not a
 * forecast.
 */
function percentileProfit(distribution: OutcomeDistribution, fraction: number): number | null {
  const priced = [...pricedOf(distribution)].sort(
    (a, b) => (a.profitMinor ?? 0) - (b.profitMinor ?? 0),
  )
  if (!priced.length || distribution.coverage <= 0) return null
  const target = fraction * distribution.coverage
  let running = 0
  for (const item of priced) {
    running += item.probability
    if (running >= target) return item.profitMinor
  }
  return priced[priced.length - 1].profitMinor
}

/**
 * Turn P(grade) and net-per-slab into what each outcome is actually worth.
 *
 * Profit is measured against selling the card raw today: a slab worth £400 when
 * the raw card fetches £380 and grading costs £25 is a loss, however good the
 * grade looks.
 */
export function buildDistribution(
  probabilities: Record<number, number>,
  netByLabel: Record<string, number>,
  options: {
    companyCode: string
    costMinor: number
    rawNetMinor: number
    grossByLabel?: Record<string, number>
  },
): OutcomeDistribution {
  const distribution: OutcomeDistribution = { outcomes: [], coverage: 0 }
  const grades = Object.keys(probabilities)
    .map(Number)
    .sort((a, b) => b - a)

  for (const grade of grades) {
    const probability = probabilities[grade]
    const label = gradeLabelFor(options.companyCode, grade)
    const net = netByLabel[label]
    const outcome: Outcome = {
      grade,
      label,
      probability,
      grossMinor: options.grossByLabel?.[label] ?? null,
      netMinor: null,
      profitMinor: null,
    }
    if (net !== undefined) {
      outcome.netMinor = net
      outcome.profitMinor = net - options.costMinor - options.rawNetMinor
      distribution.coverage += probability
    }
    distribution.outcomes.push(outcome)
  }
  return distribution
}

/* --- One grading route ----------------------------------------------------- */

export interface RouteOutcome {
  companyId: string
  companyCode: string
  tierId: string | null
  tierName: string | null
  costMinor: number
  /** The submission this costing assumes — the same tier costs different money in a batch of 20. */
  batchSize: number
  expectedNetMinor: number | null
  expectedProfitMinor: number | null
  roiPct: number | null
  probabilityOfProfit: number | null
  probabilityOfTarget: Record<string, number>
  minimumProfitableGrade: number | null
  probabilityAtOrAboveMinimum: number | null
  downsideMinor: number | null
  upsideMinor: number | null
  coverage: number
  slabLiquidity: number | null
  slabSales: number
  opportunityScore: number | null
  scoreParts: Record<string, number>
  confidence: Confidence
  distribution: OutcomeDistribution
  notes: string[]
}

export interface DecisionInputs {
  rawNetMinor: number | null
  rawValueMinor: number | null
  liquidityScore: number | null
  trendDirection: string
  trendConfidence: Confidence
  marketConfidence: Confidence
  gradeConfidence: Confidence
  /** Sales counted per grade label, for per-company slab liquidity. */
  salesByLabel: Record<string, number>
  marketRecognition: Record<string, number>
}

/** Grades this card might get that have no sales behind them, likeliest first. */
export function unpricedLabels(route: RouteOutcome): string[] {
  return [...route.distribution.outcomes]
    .sort((a, b) => b.probability - a.probability)
    .filter((item) => item.netMinor === null)
    .map((item) => item.label)
}

/** Expected value and risk for one grading route. */
export function evaluateRoute(input: {
  companyId: string
  companyCode: string
  tierId: string | null
  tierName: string | null
  costMinor: number
  probabilities: Record<number, number>
  netByLabel: Record<string, number>
  grossByLabel?: Record<string, number>
  inputs: DecisionInputs
  thresholds: Thresholds
  batchSize: number
}): RouteOutcome {
  const { inputs, thresholds } = input
  const rawNet = inputs.rawNetMinor ?? 0
  const distribution = buildDistribution(input.probabilities, input.netByLabel, {
    companyCode: input.companyCode,
    costMinor: input.costMinor,
    rawNetMinor: rawNet,
    grossByLabel: input.grossByLabel,
  })

  const route: RouteOutcome = {
    companyId: input.companyId,
    companyCode: input.companyCode,
    tierId: input.tierId,
    tierName: input.tierName,
    costMinor: input.costMinor,
    batchSize: input.batchSize,
    expectedNetMinor: null,
    expectedProfitMinor: null,
    roiPct: null,
    probabilityOfProfit: null,
    probabilityOfTarget: {},
    minimumProfitableGrade: null,
    probabilityAtOrAboveMinimum: null,
    downsideMinor: null,
    upsideMinor: null,
    coverage: Math.round(distribution.coverage * 10_000) / 10_000,
    slabLiquidity: null,
    slabSales: 0,
    opportunityScore: null,
    scoreParts: {},
    confidence: 'none',
    distribution,
    notes: [],
  }

  if (!pricedOf(distribution).length) {
    route.notes.push(
      `No ${input.companyCode} sales stored for any grade this card might get, so there is ` +
        'nothing to expect.',
    )
    return route
  }

  const expectedNet = expectation(distribution, (item) => item.netMinor ?? 0)
  const expectedProfit = expectation(distribution, (item) => item.profitMinor ?? 0)
  route.expectedNetMinor = expectedNet === null ? null : Math.round(expectedNet)
  route.expectedProfitMinor = expectedProfit === null ? null : Math.round(expectedProfit)

  // ROI is measured on the grading fee — the money you choose to spend. The
  // card's own value is not "returned" by grading, it is carried through.
  if (route.expectedProfitMinor !== null && input.costMinor > 0) {
    route.roiPct = Math.round((route.expectedProfitMinor / input.costMinor) * 1000) / 10
  }

  route.probabilityOfProfit = probabilityWhere(distribution, (item) => (item.profitMinor ?? 0) > 0)
  for (const target of PROFIT_LADDER) {
    const targetMinor = toMinor(target)
    const probability = probabilityWhere(
      distribution,
      (item) => (item.profitMinor ?? 0) >= targetMinor,
    )
    if (probability !== null) {
      route.probabilityOfTarget[String(target)] = Math.round(probability * 10_000) / 10_000
    }
  }

  const profitable = pricedOf(distribution).filter((item) => (item.profitMinor ?? 0) > 0)
  if (profitable.length) {
    const floor = profitable.reduce((low, item) => (item.grade < low.grade ? item : low))
    route.minimumProfitableGrade = floor.grade
    // A question about the grade, not the price: we know how often it comes
    // back a 9 whether or not a 9 has ever sold.
    route.probabilityAtOrAboveMinimum = probabilityOfGrade(
      distribution,
      (item) => item.grade >= floor.grade,
    )
  }

  route.downsideMinor = percentileProfit(distribution, downsidePercentile(thresholds))
  route.upsideMinor = percentileProfit(distribution, 0.9)

  // How readily *this grader's* slabs actually trade. The card's overall
  // liquidity says people want the card; this says they want it in this slab.
  route.slabSales = Object.entries(inputs.salesByLabel)
    .filter(([label]) => label.split(' ')[0].toUpperCase() === input.companyCode.toUpperCase())
    .reduce((sum, [, count]) => sum + count, 0)
  route.slabLiquidity = slabLiquidity(
    route.slabSales,
    inputs.marketRecognition[input.companyCode] ?? 5,
    inputs.liquidityScore,
  )

  const scored = score(route, inputs, thresholds)
  route.scoreParts = scored.parts
  route.opportunityScore = scored.total
  route.confidence = routeConfidence(route, inputs)

  if (route.coverage < 0.999) {
    route.notes.push(
      `Priced against ${pct(route.coverage)} of the likely grades — the rest have no ` +
        `${input.companyCode} sales stored, so they are left out rather than counted as zero.`,
    )
  }
  return route
}

/**
 * 0-10: how readily this grader's slab of this card would sell.
 *
 * Blends what the market has done (sales of this grader's slabs) with how
 * widely the grader is accepted, which is the only signal available before any
 * of its slabs have traded. The card's own liquidity caps it: a grader's
 * reputation cannot make an untraded card liquid.
 */
function slabLiquidity(
  sales: number,
  recognition: number,
  cardLiquidity: number | null,
): number | null {
  if (cardLiquidity === null && sales === 0) return null
  const observed = sales ? Math.min(10, sales * 1.2) : null
  // With no observed sales the recognition score is all there is, and it is a
  // weaker claim, so it is discounted rather than taken at face value.
  let blended = observed ?? recognition * 0.6
  if (observed !== null && sales < 5) blended = (observed + recognition * 0.6) / 2
  if (cardLiquidity !== null) blended = Math.min(blended, cardLiquidity)
  return Math.round(Math.max(0, Math.min(10, blended)) * 10) / 10
}

/* --- The composite score (spec section 27) --------------------------------- */

const TREND_POINTS: Record<string, number> = {
  strong_up: 10,
  up: 7.5,
  stable: 5,
  down: 2.5,
  strong_down: 0,
  insufficient_data: 5,
}

const CONFIDENCE_POINTS: Record<string, number> = { high: 10, medium: 7, low: 4, none: 1 }

/** The Grading Opportunity Score: five 0-10 components, user-weighted, out of 100. */
function score(
  route: RouteOutcome,
  inputs: DecisionInputs,
  thresholds: Thresholds,
): { parts: Record<string, number>; total: number } {
  const parts: Record<string, number> = {}

  // Profitability, measured against the user's own bar rather than an absolute
  // scale — "twice what I asked for" is the meaningful statement.
  const profit = route.expectedProfitMinor ?? 0
  const bar = Math.max(thresholds.minimumAbsoluteProfitMinor, 1)
  const ratio = profit / bar
  parts.profitability = ratio > 0 ? Math.max(0, Math.min(10, 5 * ratio)) : 0

  parts.grade_probability = Math.round((route.probabilityOfProfit ?? 0) * 10 * 100) / 100
  parts.liquidity = route.slabLiquidity ?? 0
  parts.trend = TREND_POINTS[inputs.trendDirection] ?? 5

  // Risk: how much of the answer rests on evidence, and how far the downside
  // falls. A wide, thinly-priced distribution scores badly even when its
  // expectation is good.
  const evidence =
    (CONFIDENCE_POINTS[inputs.marketConfidence] ?? 1) * 0.5 +
    (CONFIDENCE_POINTS[inputs.gradeConfidence] ?? 1) * 0.5
  let downsidePenalty = 0
  if (route.downsideMinor !== null && route.downsideMinor < 0) {
    const loss = Math.abs(route.downsideMinor)
    downsidePenalty = Math.min(5, (loss / Math.max(route.costMinor, 1)) * 2.5)
  }
  parts.risk = Math.max(0, Math.round((evidence * route.coverage - downsidePenalty) * 100) / 100)

  const weights = thresholds.weights
  const totalWeight = Object.values(weights).reduce((sum, value) => sum + value, 0) || 100
  const weighted = Object.entries(weights).reduce(
    (sum, [key, weight]) => sum + (parts[key] ?? 0) * weight,
    0,
  )
  const rounded = Object.fromEntries(
    Object.entries(parts).map(([key, value]) => [key, Math.round(value * 100) / 100]),
  )
  return { parts: rounded, total: Math.round((weighted / totalWeight) * 10 * 10) / 10 }
}

/** The weakest link: a perfect grade model priced off two sales is a two-sale answer. */
function routeConfidence(route: RouteOutcome, inputs: DecisionInputs): Confidence {
  const weakest =
    rankOf(inputs.marketConfidence) <= rankOf(inputs.gradeConfidence)
      ? inputs.marketConfidence
      : inputs.gradeConfidence
  if (route.coverage < 0.5) return weakest === 'none' ? 'none' : 'low'
  if (route.coverage < 0.8 && weakest === 'high') return 'medium'
  return weakest
}

/* --- The decision (spec sections 24-26, 31, 33) ---------------------------- */

export interface DecisionResult {
  decision: Decision
  confidence: Confidence
  headline: string
  chosen: RouteOutcome | null
  alternative: RouteOutcome | null
  alternativeNote: string | null
  reasons: string[]
  blockers: string[]
  reviewInDays: number | null
}

const blankResult = (): DecisionResult => ({
  decision: 'insufficient_data',
  confidence: 'none',
  headline: '',
  chosen: null,
  alternative: null,
  alternativeNote: null,
  reasons: [],
  blockers: [],
  reviewInDays: null,
})

/**
 * Pick a route and a verdict, and be able to justify both.
 *
 * `routesIfBatched` separates "not worth grading" from "not worth grading *on
 * its own*" — the difference between `do_not_grade` and `grade_if_batch_filled`,
 * and one of the more useful things the engine can say.
 */
export function decide(
  routes: RouteOutcome[],
  options: {
    inputs: DecisionInputs
    thresholds: Thresholds
    batchSize: number
    routesIfBatched?: RouteOutcome[] | null
  },
): DecisionResult {
  const { inputs, thresholds, batchSize } = options
  const result = blankResult()

  if (inputs.rawValueMinor === null) {
    result.headline = 'No value known for this card yet.'
    result.blockers.push('Add comparable sales or your own raw estimate.')
    return result
  }

  if (inputs.rawValueMinor < thresholds.gradingValueFloorMinor) {
    result.decision = 'do_not_grade'
    result.confidence = 'high'
    result.headline = 'Too cheap to be worth grading.'
    result.reasons.push(
      'The raw card is below your grading value floor, so the fee would swallow it whatever ' +
        'grade it came back as.',
    )
    return result
  }

  const priced = routes.filter((route) => route.expectedProfitMinor !== null)
  if (!priced.length) {
    result.headline = 'Not enough data to recommend a decision yet.'
    result.blockers.push(
      'Add graded sales for at least one grader, so the outcome can be priced rather than ' +
        'guessed at.',
    )
    return result
  }

  // Rank by the composite score, which already carries liquidity and risk.
  const ranked = [...priced].sort(
    (a, b) =>
      (b.opportunityScore ?? 0) - (a.opportunityScore ?? 0) ||
      (b.expectedProfitMinor ?? 0) - (a.expectedProfitMinor ?? 0),
  )
  const best = ranked[0]
  result.chosen = best
  result.confidence = best.confidence

  // Spec section 26: the richest route on paper is not always the one to take.
  const richest = priced.reduce((top, route) =>
    (route.expectedProfitMinor ?? 0) > (top.expectedProfitMinor ?? 0) ? route : top,
  )
  if (richest !== best && (richest.expectedProfitMinor ?? 0) > (best.expectedProfitMinor ?? 0)) {
    result.alternative = richest
    result.alternativeNote = whyNot(richest, best)
  }

  for (const route of ranked) {
    const missing = unpricedLabels(route)
    if (route.coverage < 0.999 && missing.length) {
      result.blockers.push(
        `Add ${missing.slice(0, 3).join(', ')} sales — ${pct(1 - route.coverage)} of this ` +
          `card's likely outcomes have no ${route.companyCode} price behind them.`,
      )
      break
    }
  }

  const clears = clearsThresholds(best, thresholds)
  if (clears === null) {
    result.decision = 'grade'
    result.headline = `Grade with ${best.companyCode}${best.tierName ? ` ${best.tierName}` : ''}.`
    result.reasons.push(
      'Expected profit beats selling raw by enough to clear your bar, and the grade lands ' +
        `profitably ${pct(best.probabilityOfProfit ?? 0)} of the time.`,
    )
    return result
  }

  // It failed. Would a fuller submission fix it? That is a different answer
  // from "not worth grading".
  if (options.routesIfBatched?.length && batchSize === 1) {
    const batched = options.routesIfBatched.filter((route) => route.expectedProfitMinor !== null)
    if (batched.length) {
      const bestBatched = batched.reduce((top, route) =>
        (route.opportunityScore ?? 0) > (top.opportunityScore ?? 0) ||
        ((route.opportunityScore ?? 0) === (top.opportunityScore ?? 0) &&
          (route.expectedProfitMinor ?? 0) > (top.expectedProfitMinor ?? 0))
          ? route
          : top,
      )
      if (clearsThresholds(bestBatched, thresholds) === null) {
        result.decision = 'grade_if_batch_filled'
        result.chosen = bestBatched
        result.confidence = bestBatched.confidence
        result.headline = 'Worth grading, but not on its own.'
        result.reasons.push(
          `Sending it alone costs ${pounds(best.costMinor)} and does not clear your bar. ` +
            `In a submission of ${bestBatched.batchSize} it costs ` +
            `${pounds(bestBatched.costMinor)} and does.`,
        )
        return result
      }
    }
  }

  // Grading is out. Sell raw, or hold?
  if (inputs.trendDirection === 'strong_up' || inputs.trendDirection === 'up') {
    result.decision = 'hold'
    result.headline = 'Hold — grading does not pay, but the market is rising.'
    result.reasons.push(clears)
    result.reasons.push(
      `Raw prices are ${inputs.trendDirection.replace(/_/g, ' ')}, so the picture may look ` +
        'different in a month.',
    )
    result.reviewInDays = thresholds.holdRecheckDays
    return result
  }

  const liquidity = inputs.liquidityScore
  if (liquidity !== null && liquidity < thresholds.minimumLiquidityScore) {
    result.decision = 'keep_raw'
    result.headline = 'Keep it raw — grading does not pay and it barely trades.'
    result.reasons.push(clears)
    result.reasons.push(
      `Liquidity ${liquidity.toFixed(1)}/10 is below your minimum, so a quick raw sale is not ` +
        'realistic either.',
    )
    return result
  }

  result.decision = 'sell_raw'
  result.headline = 'Sell it raw.'
  result.reasons.push(clears)
  if (inputs.rawNetMinor !== null) {
    result.reasons.push(
      `Selling raw nets ${pounds(inputs.rawNetMinor)} today with no fee and no wait.`,
    )
  }
  return result
}

/** `null` when the route clears every bar; otherwise the first one it fails. */
function clearsThresholds(route: RouteOutcome, thresholds: Thresholds): string | null {
  const profit = route.expectedProfitMinor ?? 0
  if (profit < thresholds.minimumAbsoluteProfitMinor) {
    return (
      `Expected profit of ${pounds(profit)} over selling raw is below your minimum of ` +
      `${pounds(thresholds.minimumAbsoluteProfitMinor)}.`
    )
  }
  if (route.roiPct !== null && route.roiPct < thresholds.minimumRoiPct) {
    return (
      `A ${route.roiPct.toFixed(0)}% return on the grading fee is below your minimum of ` +
      `${thresholds.minimumRoiPct.toFixed(0)}%.`
    )
  }
  const probability = route.probabilityOfProfit ?? 0
  if (probability < thresholds.minimumProbabilityOfProfit) {
    // Distinguish "this card does not grade well enough" from "we cannot see
    // enough of the outcomes to say". They need different actions, and blaming
    // the card for a gap in the data is the wrong answer.
    if (route.coverage < 0.999 && probability >= route.coverage - 1e-9) {
      const missing = unpricedLabels(route).slice(0, 4).join(', ') || 'some grades'
      return (
        `Every grade with sales behind it is profitable, but only ${pct(route.coverage)} of ` +
        `the likely outcomes have any — so this cannot be confirmed. Add ${missing} sales.`
      )
    }
    return (
      `It only lands profitably ${pct(probability)} of the time, below your minimum of ` +
      `${pct(thresholds.minimumProbabilityOfProfit)}.`
    )
  }
  if (route.slabLiquidity !== null && route.slabLiquidity < thresholds.minimumLiquidityScore) {
    return (
      `${route.companyCode} slabs of this card score ${route.slabLiquidity.toFixed(1)}/10 for ` +
      `liquidity, below your minimum of ${thresholds.minimumLiquidityScore.toFixed(1)}.`
    )
  }
  return null
}

/** Say why the more profitable route lost. Never hide it (spec section 26). */
function whyNot(richest: RouteOutcome, chosen: RouteOutcome): string {
  const gap = (richest.expectedProfitMinor ?? 0) - (chosen.expectedProfitMinor ?? 0)
  const reasons: string[] = []

  if ((richest.slabLiquidity ?? 0) < (chosen.slabLiquidity ?? 0)) {
    reasons.push(
      `${richest.companyCode} slabs of this card score ${(richest.slabLiquidity ?? 0).toFixed(1)}` +
        `/10 for liquidity against ${chosen.companyCode}'s ` +
        `${(chosen.slabLiquidity ?? 0).toFixed(1)} — profit you cannot realise is not profit`,
    )
  }
  if ((richest.probabilityOfProfit ?? 0) < (chosen.probabilityOfProfit ?? 0)) {
    reasons.push(
      `it only lands profitably ${pct(richest.probabilityOfProfit ?? 0)} of the time against ` +
        `${chosen.companyCode}'s ${pct(chosen.probabilityOfProfit ?? 0)}`,
    )
  }
  if ((richest.coverage ?? 0) < (chosen.coverage ?? 0)) {
    reasons.push(`only ${pct(richest.coverage ?? 0)} of its likely grades have sales behind them`)
  }
  if (!reasons.length) {
    reasons.push('it scores lower once liquidity, trend and risk are weighed in')
  }

  return (
    `${richest.companyCode}${richest.tierName ? ` ${richest.tierName}` : ''} shows ` +
    `${pounds(gap)} more expected profit, but ${reasons.join('; ')}.`
  )
}
