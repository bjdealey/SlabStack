/**
 * The demo's port of `app/services/analytics.py`.
 *
 * Same rule as the server's: everything here is a *view*. It ranks, filters and
 * compares answers the ported decision, market and submission engines already
 * gave, and never re-derives one of them. `make parity` runs both this file and
 * the Python it mirrors over identical cases and compares every field, so a
 * divergence fails the build rather than quietly shipping to Pages.
 */

import type {
  BlockStatus,
  CollectionDecisions,
  CollectionFilter,
  Decision,
  FilterResult,
  GradedCardResult,
  Opportunity,
  RankedOpportunities,
  SellingCandidate,
  SellingQueue,
  Submission,
  SubmissionReturn,
  SubmissionReturns,
} from '@/lib/types'

/** Verdicts that mean money would be spent on grading. */
const ACTIONABLE = new Set<Decision>(['grade', 'grade_if_batch_filled'])

/** Verdicts that mean the card is not going to a grader. */
const SELLABLE = new Set<Decision>(['sell_raw', 'keep_raw', 'do_not_grade'])

/**
 * Directions that mean the price is going the wrong way. `insufficient_data` is
 * deliberately absent: not knowing is not the same as falling.
 */
const FALLING = new Set(['down', 'strong_down'])

export function rankOpportunities(decisions: CollectionDecisions): RankedOpportunities {
  const actionable = decisions.opportunities.filter((item) => ACTIONABLE.has(item.decision))
  return {
    currency: decisions.currency,
    analysed: decisions.analysed,
    total_cards: decisions.total_cards,
    actionable: actionable.length,
    expected_profit: decisions.expected_profit,
    total_grading_cost: decisions.total_grading_cost,
    items: actionable,
    status: decisions.status,
    reason: actionable.length
      ? `${actionable.length} of ${decisions.analysed} analysed card(s) are worth grading. ${
          decisions.reason ?? ''
        }`.trim()
      : decisions.reason,
  }
}

/**
 * What to *ask*, which is not what it will *fetch*.
 *
 * A listing price is a negotiating position: you list above the realistic sale
 * and expect to come down. How far above is a liquidity question — a card that
 * trades weekly can be listed near its median and still move, while one that
 * trades twice a year needs room to be haggled down and time to find its buyer.
 *
 * The upper quartile of actual sales is the ceiling, because asking more than
 * anyone has recently paid is how a listing sits unsold for a year.
 */
export function suggestedListingMinor(
  realisticMinor: number | null,
  highQuartileMinor: number | null,
  liquidityScore: number | null,
): [number | null, string | null] {
  if (realisticMinor === null) return [null, null]

  let markup: number
  let basis: string
  if (liquidityScore === null) {
    markup = 0.1
    basis = '10% above the realistic sale price — no liquidity reading to judge by.'
  } else if (liquidityScore >= 7) {
    markup = 0.05
    basis = '5% above the realistic sale price. It trades often, so it does not need room.'
  } else if (liquidityScore >= 4) {
    markup = 0.1
    basis = '10% above the realistic sale price, leaving a little room to negotiate.'
  } else {
    markup = 0.18
    basis =
      '18% above the realistic sale price. It trades rarely, so it needs room to be haggled ' +
      'down and time to find a buyer.'
  }

  let asking = Math.round(realisticMinor * (1 + markup))

  // Never below the realistic sale price: the cap exists to stop you asking
  // more than the market pays, not to talk you into asking less than it does.
  if (highQuartileMinor && realisticMinor < highQuartileMinor && highQuartileMinor < asking) {
    asking = highQuartileMinor
    basis =
      'Capped at the upper quartile of recent sales — asking more than anyone has recently ' +
      'paid is how a listing sits unsold.'
  }
  return [asking, basis]
}

export interface QueueInput {
  /** The card's raw price row, if it has one. */
  realisticMinor: number | null
  highQuartileMinor: number | null
  confidence: string
  liquidityScore: number | null
  liquidityBand: string | null
  daysSinceLastSale: number | null
  trendDirection: string | null
  purchasePrice: number | null
}

export function buildSellingQueue(
  decisions: CollectionDecisions,
  lookup: (cardId: string) => QueueInput,
): SellingQueue {
  const result: SellingQueue = {
    currency: decisions.currency,
    analysed: decisions.analysed,
    total_cards: decisions.total_cards,
    total_net_proceeds: null,
    items: [],
    status: 'ok',
    reason: null,
    notes: [],
  }

  const sellable = decisions.opportunities.filter((item) => SELLABLE.has(item.decision))
  if (!sellable.length) {
    result.status = 'insufficient_data'
    result.reason = decisions.analysed
      ? 'Nothing in the analysed cards is better off sold raw right now.'
      : decisions.reason
    return result
  }

  let netTotal = 0
  let counted = 0
  for (const item of sellable) {
    const market = lookup(item.card_id)
    const candidate: SellingCandidate = {
      card_id: item.card_id,
      name: item.name,
      set_label: item.set_label,
      decision: item.decision,
      realistic_sale: null,
      net_proceeds: item.net_raw_alternative,
      suggested_listing: null,
      listing_basis: null,
      liquidity_score: market.liquidityScore,
      liquidity_band: market.liquidityBand,
      days_since_last_sale: market.daysSinceLastSale,
      trend_direction: market.trendDirection,
      confidence: market.confidence as SellingCandidate['confidence'],
      purchase_price: market.purchasePrice,
      gain_vs_purchase: null,
      blockers: [],
    }

    if (market.realisticMinor !== null) {
      candidate.realistic_sale = market.realisticMinor / 100
      const [asking, basis] = suggestedListingMinor(
        market.realisticMinor,
        market.highQuartileMinor,
        market.liquidityScore,
      )
      candidate.suggested_listing = asking === null ? null : asking / 100
      candidate.listing_basis = basis
    } else {
      candidate.blockers.push(
        'No raw sales stored, so there is nothing to price a listing against.',
      )
    }

    if (candidate.net_proceeds !== null && market.purchasePrice !== null) {
      candidate.gain_vs_purchase = round2(candidate.net_proceeds - market.purchasePrice)
    }
    if (candidate.net_proceeds !== null) {
      netTotal += Math.round(candidate.net_proceeds * 100)
      counted += 1
    }
    result.items.push(candidate)
  }

  // Most valuable first: this is a to-do list, and the biggest cheque is the
  // one worth writing the listing for tonight.
  result.items.sort((a, b) => (b.net_proceeds ?? 0) - (a.net_proceeds ?? 0))
  if (counted) result.total_net_proceeds = round2(netTotal / 100)

  const unpriced = result.items.filter((item) => item.suggested_listing === null)
  if (unpriced.length) {
    result.status = 'partial'
    result.notes.push(
      `${unpriced.length} card(s) have no raw sales to price against, so no listing price is ` +
        'suggested for them.',
    )
    result.reason = result.notes[0]
  }
  return result
}

/**
 * The cuts offered in the UI. Each is a predicate over figures the decision and
 * market engines already produced — never a fresh definition of the same idea.
 */
export const FILTERS: CollectionFilter[] = [
  { key: 'grade_now', label: 'Grade now', description: 'Clears your bar on its own today.' },
  {
    key: 'grade_if_batch_filled',
    label: 'Grade in a batch',
    description: 'Worth grading, but only once a submission is full.',
  },
  { key: 'sell_raw', label: 'Sell raw', description: 'Better off sold as it is.' },
  {
    key: 'hold',
    label: 'Hold',
    description: 'Grading does not pay yet, but the market is rising.',
  },
  {
    key: 'high_upside',
    label: 'High upside',
    description: 'The good outcomes are worth a lot more than the bad ones.',
  },
  {
    key: 'high_risk',
    label: 'High risk',
    description: 'A real chance of losing money against selling it raw.',
  },
  {
    key: 'low_liquidity',
    label: 'Hard to sell',
    description: 'Trades below the minimum liquidity you set, so any plan for it will be slow.',
  },
  {
    key: 'declining',
    label: 'Declining',
    description: 'Prices are falling — waiting is costing you.',
  },
  {
    key: 'needs_data',
    label: 'Needs data',
    description: 'Cannot be decided until it has more behind it.',
  },
]

export function applyFilter(
  key: string,
  decisions: CollectionDecisions,
  minimumLiquidity: number,
): FilterResult {
  const definition = FILTERS.find((item) => item.key === key)
  if (!definition) throw new Error(`'${key}' is not a collection filter.`)

  const matched = decisions.opportunities.filter((item) =>
    matches(key, item, minimumLiquidity),
  )
  const result: FilterResult = {
    key,
    label: definition.label,
    description: definition.description,
    currency: decisions.currency,
    matched: matched.length,
    analysed: decisions.analysed,
    total_cards: decisions.total_cards,
    // Cards the sweep never reached plus those it could not decide: both are
    // unanswered, and lumping them into "does not match" would be a lie.
    unclassified:
      key === 'needs_data'
        ? 0
        : decisions.skipped_not_ready + (decisions.counts.insufficient_data ?? 0),
    card_ids: matched.map((item) => item.card_id),
    items: matched,
    status: 'ok' as BlockStatus,
    reason: null,
  }

  if (!decisions.analysed) {
    result.status = 'insufficient_data'
    result.reason = decisions.reason
  } else if (result.unclassified) {
    result.status = 'partial'
    result.reason =
      `${result.unclassified} card(s) could not be decided, so they were not tested against ` +
      'this filter.'
  }
  return result
}

/**
 * Whether one card falls into a named cut.
 *
 * Every branch reads a figure the decision engine produced. Where a figure is
 * unknown the card does **not** match: an unknown risk is not a low risk, and a
 * card with no trend behind it is not a falling one.
 */
function matches(key: string, item: Opportunity, minimumLiquidity: number): boolean {
  if (key === 'grade_now') return item.decision === 'grade'
  if (key === 'grade_if_batch_filled') return item.decision === 'grade_if_batch_filled'
  if (key === 'sell_raw') return item.decision === 'sell_raw'
  if (key === 'hold') return item.decision === 'hold'
  if (key === 'needs_data') return item.decision === 'insufficient_data'

  if (key === 'high_upside') {
    // Judged against what selling it raw would net, so "high" means high
    // relative to the alternative rather than high in absolute pounds.
    if (item.expected_profit === null || !item.net_raw_alternative) return false
    return item.expected_profit >= item.net_raw_alternative * 0.5
  }

  if (key === 'high_risk') {
    if (item.probability_of_profit === null) return false
    return item.probability_of_profit < 0.5 && ACTIONABLE.has(item.decision)
  }

  if (key === 'low_liquidity') {
    if (item.liquidity_score === null) return false
    return item.liquidity_score < minimumLiquidity
  }

  if (key === 'declining') {
    return item.trend_direction !== null && FALLING.has(item.trend_direction)
  }

  return false
}

/* --- Submission returns ---------------------------------------------------- */

export interface ReturnInput {
  submission: Submission
  /** What one slab is worth at its actual grade, or null when nobody has sold one. */
  gradedValueMinor: (cardId: string, grade: number) => number | null
  /** What that gross would net after fees and postage. */
  netOf: (grossMinor: number) => number | null
}

/**
 * What the parcels you have sent actually returned.
 *
 * Only submissions that have come back can be scored. The rest are counted and
 * reported rather than averaged in at zero, which would make every open
 * submission look like a loss.
 */
export function buildSubmissionReturns(
  currency: string,
  inputs: ReturnInput[],
): SubmissionReturns {
  const result: SubmissionReturns = {
    currency,
    submissions: [],
    scored: 0,
    awaiting: 0,
    total_cost: null,
    total_profit: null,
    roi_pct: null,
    status: 'ok',
    reason: null,
  }

  let costMinor = 0
  let profitMinor = 0

  for (const { submission, gradedValueMinor, netOf } of inputs) {
    const entry: SubmissionReturn = {
      submission_id: submission.id,
      reference: submission.reference,
      company_code: submission.company_code,
      status: submission.status,
      returned_at: submission.returned_at,
      card_count: submission.card_count,
      graded_count: 0,
      total_cost: submission.total_cost,
      total_value: null,
      total_profit: null,
      roi_pct: null,
      mean_surprise: null,
      cards: [],
      status_note: null,
    }

    const graded = submission.cards.filter((row) => row.actual_grade !== null)
    entry.graded_count = graded.length

    if (!graded.length) {
      result.awaiting += 1
      entry.status_note =
        'No grades recorded yet, so there is nothing to score. Record the grades when the ' +
        'parcel comes back.'
      result.submissions.push(entry)
      continue
    }

    let valueMinor = 0
    let entryCostMinor = 0
    const surprises: number[] = []

    for (const row of graded) {
      const lineCostMinor = Math.round((row.total_cost ?? 0) * 100)
      const card: GradedCardResult = {
        card_id: row.card_id,
        name: row.name,
        predicted_grade: row.predicted_grade,
        actual_grade: row.actual_grade,
        surprise: null,
        cost: row.total_cost,
        graded_value: null,
        net_if_sold: null,
        profit: null,
        blockers: [],
      }
      if (row.predicted_grade !== null && row.actual_grade !== null) {
        card.surprise = round2(row.actual_grade - row.predicted_grade)
        surprises.push(card.surprise)
      }

      const worth = gradedValueMinor(row.card_id, row.actual_grade!)
      if (worth === null) {
        card.blockers.push(
          `No ${submission.company_code} sales stored at grade ${formatGrade(row.actual_grade!)}, ` +
            'so this slab cannot be valued.',
        )
      } else {
        card.graded_value = worth / 100
        const net = netOf(worth)
        if (net !== null) {
          card.net_if_sold = net / 100
          valueMinor += net
          card.profit = round2((net - lineCostMinor) / 100)
        }
      }
      entryCostMinor += lineCostMinor
      entry.cards.push(card)
    }

    entry.total_value = valueMinor ? round2(valueMinor / 100) : null
    if (entryCostMinor && valueMinor) {
      entry.total_profit = round2((valueMinor - entryCostMinor) / 100)
      entry.roi_pct = round1(((valueMinor - entryCostMinor) / entryCostMinor) * 100)
      costMinor += entryCostMinor
      profitMinor += valueMinor - entryCostMinor
    }
    if (surprises.length) {
      entry.mean_surprise = round2(surprises.reduce((a, b) => a + b, 0) / surprises.length)
    }

    // A slab nobody has sold still cost money to grade. Its fee is in the total
    // and its value is not, so the return is a floor rather than an estimate.
    const unvalued = entry.cards.filter((row) => row.graded_value === null).length
    if (unvalued && entry.roi_pct !== null) {
      entry.status_note =
        `${unvalued} of ${entry.graded_count} graded card(s) have no sales at that grade, so ` +
        'they cost money here but add no value. The return is a floor, not an estimate.'
    }

    result.scored += 1
    result.submissions.push(entry)
  }

  if (costMinor) {
    result.total_cost = round2(costMinor / 100)
    result.total_profit = round2(profitMinor / 100)
    result.roi_pct = round1((profitMinor / costMinor) * 100)
  }

  if (!inputs.length) {
    result.status = 'insufficient_data'
    result.reason = 'No submissions yet, so there is nothing to score.'
  } else if (!result.scored) {
    result.status = 'insufficient_data'
    result.reason =
      `${result.awaiting} submission(s) are still out. Record the grades when they come back ` +
      'and this becomes a real return.'
  } else if (result.awaiting) {
    result.status = 'partial'
    result.reason =
      `Scored ${result.scored} returned submission(s); ${result.awaiting} still out and not ` +
      'counted in any total.'
  }
  return result
}

/** Matches Python's `%g`: 10 stays "10", 9.5 stays "9.5". */
const formatGrade = (grade: number) => String(Number(grade.toPrecision(6)))

const round2 = (value: number) => Math.round(value * 100) / 100
const round1 = (value: number) => Math.round(value * 10) / 10
