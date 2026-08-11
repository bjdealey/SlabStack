/**
 * Run the browser port's decision engine over the parity cases and print JSON.
 *
 * Paired with `server.py`, which runs the same cases through the backend.
 * `compare.py` diffs the two. See README.md.
 */

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { DecisionInputs } from '@/lib/demo/decision'
import { decide, evaluateRoute, thresholdsFrom } from '@/lib/demo/decision'

interface ParityCase {
  name: string
  companyCode: string
  probabilities: Record<string, number>
  netByLabel: Record<string, number>
  grossByLabel: Record<string, number>
  costMinor: number
  batchSize: number
  inputs: DecisionInputs
  settings: Record<string, unknown>
}

const here = dirname(fileURLToPath(import.meta.url))
const { cases } = JSON.parse(readFileSync(join(here, 'cases.json'), 'utf8')) as {
  cases: ParityCase[]
}

const output = cases.map((testCase) => {
  const thresholds = thresholdsFrom(testCase.settings)
  const route = evaluateRoute({
    companyId: 'c1',
    companyCode: testCase.companyCode,
    tierId: 't1',
    tierName: 'Economy',
    costMinor: testCase.costMinor,
    probabilities: testCase.probabilities as unknown as Record<number, number>,
    netByLabel: testCase.netByLabel,
    grossByLabel: testCase.grossByLabel,
    inputs: testCase.inputs,
    thresholds,
    batchSize: testCase.batchSize,
  })
  const result = decide([route], {
    inputs: testCase.inputs,
    thresholds,
    batchSize: testCase.batchSize,
  })
  return {
    name: testCase.name,
    decision: result.decision,
    confidence: result.confidence,
    headline: result.headline,
    reasons: result.reasons,
    blockers: result.blockers,
    review_in_days: result.reviewInDays,
    route: {
      expected_net_minor: route.expectedNetMinor,
      expected_profit_minor: route.expectedProfitMinor,
      roi_pct: route.roiPct,
      probability_of_profit: route.probabilityOfProfit,
      probability_of_target: route.probabilityOfTarget,
      minimum_profitable_grade: route.minimumProfitableGrade,
      probability_at_or_above_minimum: route.probabilityAtOrAboveMinimum,
      downside_minor: route.downsideMinor,
      upside_minor: route.upsideMinor,
      coverage: route.coverage,
      slab_liquidity: route.slabLiquidity,
      slab_sales: route.slabSales,
      opportunity_score: route.opportunityScore,
      score_parts: route.scoreParts,
      confidence: route.confidence,
      notes: route.notes,
      rows: route.distribution.outcomes.map((item) => ({
        grade: item.grade,
        label: item.label,
        probability: item.probability,
        gross_minor: item.grossMinor,
        net_minor: item.netMinor,
        profit_minor: item.profitMinor,
      })),
    },
  }
})

console.log(JSON.stringify(output, null, 2))
