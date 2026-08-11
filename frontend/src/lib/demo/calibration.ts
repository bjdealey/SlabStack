/**
 * The demo's port of `app/services/calibration.py`.
 *
 * Same three refusals as the server's: no scoring a prediction made after the
 * fact, no learning from a handful of results, no pooling graders. `make parity`
 * runs the Brier score and the correction maths on both sides over identical
 * cases, so a divergence fails the build.
 */

import type {
  AccuracyReport,
  CalibrationEntry,
  CompanyAccuracy,
  GradeBand,
  GradingCompany,
  ScoredResult,
} from '@/lib/types'

export const DEFAULT_MINIMUM_SAMPLE = 10
export const DEFAULT_MAX_OFFSET = 1.0

/**
 * The spread the rules engine typically produces for a complete assessment.
 * A multiplier of 1.0 means "the model's own confidence was about right".
 */
const NOMINAL_SIGMA = 0.55

/**
 * How wrong the whole distribution was. Lower is better; 0 is perfect.
 *
 * Marking the distribution rather than the mode is the point — being 95% sure of
 * a 10 and being 40% sure of a 10 are very different claims, and only this
 * notices when the confident one is wrong.
 */
export function brierScore(
  probabilities: Record<string, number> | null | undefined,
  actual: number | null,
): number | null {
  if (!probabilities || !Object.keys(probabilities).length || actual === null) return null

  let total = 0
  let matched = false
  for (const [key, value] of Object.entries(probabilities)) {
    const grade = Number(key)
    if (Number.isNaN(grade)) continue
    const outcome = Math.abs(grade - actual) < 1e-9 ? 1 : 0
    if (outcome) matched = true
    total += (value - outcome) ** 2
  }
  // The actual grade was given zero probability: a real, and maximal, miss.
  if (!matched) total += 1
  return round4(total)
}

export interface ResultRow {
  cardId: string
  name: string
  companyId: string
  companyCode: string
  actualGrade: number
  predictedGrade: number | null
  predictedProbabilities: Record<string, number> | null
  gradedAt: string | null
}

export function buildAccuracyReport(
  rows: ResultRow[],
  companies: GradingCompany[],
  minimumSample: number,
  awaiting: number,
): AccuracyReport {
  const report: AccuracyReport = {
    status: 'ok',
    reason: null,
    scored: rows.length,
    awaiting,
    minimum_sample: minimumSample,
    companies: [],
    results: [],
  }

  if (!rows.length) {
    report.status = 'insufficient_data'
    report.reason =
      'No graded results recorded yet. Send a submission, record the grades when it comes ' +
      'back, and the model starts being marked against them.'
    return report
  }

  const byCompany = new Map<string, ResultRow[]>()
  for (const row of rows) {
    const list = byCompany.get(row.companyId) ?? []
    list.push(row)
    byCompany.set(row.companyId, list)
  }

  for (const [companyId, group] of byCompany) {
    const company = companies.find((item) => item.id === companyId)
    if (!company) continue
    report.companies.push(scoreCompany(company, group, minimumSample))
  }
  report.companies.sort((a, b) => b.scored - a.scored)

  report.results = rows.map(
    (row): ScoredResult => ({
      card_id: row.cardId,
      name: row.name,
      company_code: row.companyCode,
      predicted_grade: row.predictedGrade,
      actual_grade: row.actualGrade,
      surprise:
        row.predictedGrade === null ? null : round2(row.actualGrade - row.predictedGrade),
      brier: brierScore(row.predictedProbabilities, row.actualGrade),
      graded_at: row.gradedAt,
    }),
  )

  if (awaiting) {
    report.status = 'partial'
    report.reason =
      `${awaiting} graded card(s) had no prediction recorded when they were sent, so they ` +
      'cannot be marked. Cards added to a submission from now on will be.'
  }
  return report
}

function scoreCompany(
  company: GradingCompany,
  rows: ResultRow[],
  minimum: number,
): CompanyAccuracy {
  const errors = rows
    .filter((row) => row.predictedGrade !== null)
    .map((row) => row.actualGrade - (row.predictedGrade as number))
  const briers = rows
    .map((row) => brierScore(row.predictedProbabilities, row.actualGrade))
    .filter((value): value is number => value !== null)

  const accuracy: CompanyAccuracy = {
    company_id: company.id,
    company_code: company.code,
    company_name: company.name,
    scored: rows.length,
    exact_pct: null,
    within_half_pct: null,
    within_one_pct: null,
    mean_error: null,
    mean_absolute_error: null,
    error_stdev: null,
    mean_brier: null,
    bands: [],
    headline: null,
    status: 'ok',
    reason: null,
  }

  if (errors.length) {
    accuracy.exact_pct = pct(errors.filter((e) => Math.abs(e) < 1e-9).length, errors.length)
    accuracy.within_half_pct = pct(
      errors.filter((e) => Math.abs(e) <= 0.5 + 1e-9).length,
      errors.length,
    )
    accuracy.within_one_pct = pct(
      errors.filter((e) => Math.abs(e) <= 1 + 1e-9).length,
      errors.length,
    )
    accuracy.mean_error = round3(mean(errors))
    accuracy.mean_absolute_error = round3(mean(errors.map(Math.abs)))
    accuracy.error_stdev = stdev(errors)
  }
  if (briers.length) accuracy.mean_brier = round4(mean(briers))

  accuracy.bands = bandsFor(rows)
  accuracy.headline = headlineFor(accuracy, company.code)

  if (rows.length < minimum) {
    accuracy.status = 'partial'
    accuracy.reason =
      `Measured across ${rows.length} result(s). Below ${minimum} this describes those cards ` +
      'rather than your eye, so it is reported but never applied to a prediction.'
  }
  return accuracy
}

/** The calibration curve: predicted rate against observed rate, per grade. */
function bandsFor(rows: ResultRow[]): GradeBand[] {
  const grades = new Set<number>()
  for (const row of rows) {
    for (const key of Object.keys(row.predictedProbabilities ?? {})) grades.add(Number(key))
    grades.add(row.actualGrade)
  }

  return [...grades]
    .sort((a, b) => b - a)
    .map((grade) => {
      let predicted = 0
      let actual = 0
      for (const row of rows) {
        predicted += row.predictedProbabilities?.[gradeKey(grade)] ?? 0
        if (Math.abs(row.actualGrade - grade) < 1e-9) actual += 1
      }
      const predictedRate = round4(predicted / rows.length)
      const actualRate = round4(actual / rows.length)
      return {
        grade,
        predicted_count: round2(predicted),
        actual_count: actual,
        predicted_rate: predictedRate,
        actual_rate: actualRate,
        gap_pct: round1((actualRate - predictedRate) * 100),
      }
    })
}

/**
 * The single sentence worth reading, or nothing.
 *
 * Prefers the biggest per-grade miss over the overall bias, because "your
 * predicted 10 rate runs 14 points above your actual rate" is actionable in a
 * way that "mean error -0.13" is not.
 */
function headlineFor(accuracy: CompanyAccuracy, companyCode: string): string | null {
  if (!accuracy.bands.length) return null

  const worst = accuracy.bands.reduce((a, b) =>
    Math.abs(b.gap_pct ?? 0) > Math.abs(a.gap_pct ?? 0) ? b : a,
  )
  const gap = worst.gap_pct ?? 0
  if (Math.abs(gap) >= 5) {
    const direction = gap < 0 ? 'above' : 'below'
    return (
      `Your predicted ${companyCode} ${formatGrade(worst.grade)} rate runs ` +
      `${Math.abs(gap).toFixed(0)} points ${direction} your actual rate.`
    )
  }

  if (accuracy.mean_error !== null && Math.abs(accuracy.mean_error) >= 0.1) {
    const reads = accuracy.mean_error > 0 ? 'harsh' : 'generously'
    const better = accuracy.mean_error > 0 ? 'better' : 'worse'
    return (
      `Cards come back ${Math.abs(accuracy.mean_error).toFixed(2)} grades ${better} than ` +
      `predicted on average — your assessment reads ${reads}.`
    )
  }
  return `Predictions track ${companyCode}'s grading closely. Nothing to correct.`
}

/**
 * What this grader's history says the model should do differently.
 *
 * Lands on the two parameters the model already has — where the distribution is
 * centred and how wide it is — rather than inventing a new mechanism.
 */
/**
 * The correction itself, as pure arithmetic over the signed errors.
 *
 * Split out from `calibrationFor` so it can be compared against the server over
 * identical inputs with no store in the way — this is the part where the two
 * implementations could silently drift.
 */
export function correctionFromErrors(
  errors: number[],
  options: {
    companyCode: string
    companyId?: string
    minimumSample?: number
    maxOffset?: number
    enabled?: boolean
  },
): CalibrationEntry {
  const minimumSample = options.minimumSample ?? DEFAULT_MINIMUM_SAMPLE
  const maxOffset = options.maxOffset ?? DEFAULT_MAX_OFFSET
  const enabled = options.enabled ?? true

  const entry: CalibrationEntry = {
    company_id: options.companyId ?? '',
    company_code: options.companyCode,
    sample_size: errors.length,
    minimum_sample: minimumSample,
    grade_offset: 0,
    spread_multiplier: 1,
    applied: false,
    confidence: 'none',
    reason: null,
  }

  if (!errors.length) {
    entry.reason =
      `No ${options.companyCode} results recorded yet, so there is nothing to learn from.`
    return entry
  }

  const meanError = mean(errors)
  const spread = stdev(errors) ?? 0

  // Clamped: a measured two-grade bias is far likelier to be a run of odd cards
  // than a real one, and applying it would wreck every prediction.
  entry.grade_offset = round3(Math.max(-maxOffset, Math.min(maxOffset, meanError)))
  // Never narrowed below the model's own spread: claiming more precision than
  // the rules engine does on the strength of a few dozen cards is backwards.
  entry.spread_multiplier = spread ? round3(Math.max(1, spread / NOMINAL_SIGMA)) : 1

  if (!enabled) {
    entry.reason =
      'Calibration is switched off in Settings, so the measurement is reported but not applied.'
    return entry
  }

  if (errors.length < minimumSample) {
    entry.reason =
      `${errors.length} of the ${minimumSample} results needed before a correction is ` +
      'applied. A bias fitted to this few cards is noise, and correcting for noise makes ' +
      'the model worse without saying so.'
    return entry
  }

  entry.applied = true
  entry.confidence =
    errors.length >= minimumSample * 3
      ? 'high'
      : errors.length >= minimumSample * 2
        ? 'medium'
        : 'low'

  const identity =
    Math.abs(entry.grade_offset) < 1e-9 && Math.abs(entry.spread_multiplier - 1) < 1e-9
  if (identity) {
    entry.reason =
      `${errors.length} ${options.companyCode} result(s) and nothing to correct — predictions ` +
      'already track this grader.'
  } else {
    const moves = entry.grade_offset > 0 ? 'up' : 'down'
    entry.reason =
      `Learned from ${errors.length} ${options.companyCode} result(s): the centre moves ` +
      `${moves} by ${Math.abs(entry.grade_offset).toFixed(2)} grades` +
      (entry.spread_multiplier > 1
        ? ` and the range widens by ${((entry.spread_multiplier - 1) * 100).toFixed(0)}%.`
        : '.')
  }
  return entry
}

/** What this grader's history says the model should do differently. */
export function calibrationFor(
  company: GradingCompany,
  rows: ResultRow[],
  options: { minimumSample: number; maxOffset: number; enabled: boolean },
): CalibrationEntry {
  const errors = rows
    .filter((row) => row.companyId === company.id && row.predictedGrade !== null)
    .map((row) => row.actualGrade - (row.predictedGrade as number))
  return correctionFromErrors(errors, {
    companyCode: company.code,
    companyId: company.id,
    minimumSample: options.minimumSample,
    maxOffset: options.maxOffset,
    enabled: options.enabled,
  })
}

/** Matches Python's `%g`: 10 stays "10", 9.5 stays "9.5". */
const gradeKey = (grade: number) => String(Number(grade.toPrecision(6)))
const formatGrade = gradeKey

const mean = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length

/** Sample standard deviation. Null below two points, which have none. */
function stdev(values: number[]): number | null {
  if (values.length < 2) return null
  const m = mean(values)
  const variance = values.reduce((sum, v) => sum + (v - m) ** 2, 0) / (values.length - 1)
  return round3(Math.sqrt(variance))
}

const pct = (count: number, total: number) => (total ? round1((count / total) * 100) : null)
const round1 = (v: number) => Math.round(v * 10) / 10
const round2 = (v: number) => Math.round(v * 100) / 100
const round3 = (v: number) => Math.round(v * 1000) / 1000
const round4 = (v: number) => Math.round(v * 10000) / 10000
