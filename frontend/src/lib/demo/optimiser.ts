/**
 * Pack the cards worth grading into submissions, ported from the backend.
 *
 * Three passes, and the third is the one that matters:
 *
 * 1. **Route** every analysable card at a batch big enough for the bulk tiers
 *    to be on the table, and take the route the engine recommends.
 * 2. **Pack** by (company, tier), checking each tier's own minimum.
 * 3. **Re-verify** every card at the size its batch actually came out at.
 *
 * Without step 3 the plan looks profitable and is not: a card that clears the
 * bar at twenty-five may not at six, because six cards carry the postage
 * twenty-five would have shared.
 */

import type { CardEvaluation, GradingCompany, OptimiserPlan, PlacedCard, ProposedBatch } from '@/lib/types'
import { toMinor } from './market'

/** Decisions that mean the card belongs in a submission at all. */
const WORTH_GRADING = new Set(['grade', 'grade_if_batch_filled'])

const money = (minor: number | null | undefined) =>
  minor === null || minor === undefined
    ? '—'
    : `${minor < 0 ? '−' : ''}£${(Math.abs(minor) / 100).toFixed(2)}`

export interface Candidate {
  cardId: string
}

/**
 * A batch big enough that every tier is on the table. Routing at 1 would hide
 * the bulk tiers and send everything to the expensive ones.
 */
export function hopefulBatchSize(companies: GradingCompany[]): number {
  const minimums = companies
    .flatMap((company) => company.tiers)
    .filter((tier) => tier.active)
    .map((tier) => tier.minimum_cards)
  return Math.max(1, ...minimums)
}

export function optimise(input: {
  candidates: string[]
  totalCards: number
  companies: GradingCompany[]
  currency: string
  limit: number
  /** Evaluate one card at one batch size. */
  evaluate: (cardId: string, batchSize: number) => CardEvaluation
}): OptimiserPlan {
  const { companies, currency, evaluate } = input
  const plan: OptimiserPlan = {
    status: 'ok',
    reason: null,
    currency,
    analysable: input.candidates.length,
    worth_grading: 0,
    placed: 0,
    total_cards: input.totalCards,
    truncated: false,
    routed_at_batch_size: hopefulBatchSize(companies),
    expected_profit: null,
    total_grading_cost: null,
    batches: [],
    unplaced: [],
    stopped_paying: [],
    notes: [],
  }

  let candidates = input.candidates
  if (candidates.length > input.limit) {
    plan.truncated = true
    candidates = candidates.slice(0, input.limit)
  }

  // --- Pass 1: route ------------------------------------------------------
  const routed = new Map<string, { evaluation: CardEvaluation; cardId: string }[]>()
  for (const cardId of candidates) {
    const evaluation = evaluate(cardId, plan.routed_at_batch_size)
    const recommendation = evaluation.recommendation
    if (!WORTH_GRADING.has(recommendation.decision)) continue
    plan.worth_grading += 1

    if (!recommendation.company_code) {
      plan.unplaced.push({
        card_id: cardId,
        name: evaluation.raw.display_name,
        set_label: evaluation.raw.set_label,
        company_code: null,
        tier_name: null,
        expected_profit: recommendation.expected_profit,
        reason: 'The engine recommends grading it but named no grader to send it to.',
      })
      continue
    }
    const key = `${recommendation.company_code}::${recommendation.tier_name ?? ''}`
    const members = routed.get(key) ?? []
    members.push({ evaluation, cardId })
    routed.set(key, members)
  }

  if (!routed.size) {
    plan.status = 'insufficient_data'
    plan.reason = plan.analysable
      ? 'Nothing in your collection clears the bar for grading right now, so there is no ' +
        'submission to build.'
      : 'No card has both a condition assessment and comparable sales yet, so nothing can be ' +
        'decided — assess a card and add its sales.'
    finish(plan, input.limit)
    return plan
  }

  // --- Passes 2 and 3: pack, then re-verify at the real size ---------------
  const ordered = [...routed.entries()].sort((a, b) => b[1].length - a[1].length)
  for (const [key, members] of ordered) {
    const [companyCode, tierName] = key.split('::')
    const company = companies.find((item) => item.code === companyCode)
    if (!company) continue
    const tier = company.tiers.find((item) => item.tier_name === tierName && item.active) ?? null

    const realSize = members.length
    const batch: ProposedBatch = {
      company_id: company.id,
      company_code: companyCode,
      tier_id: tier?.id ?? null,
      tier_name: tierName || null,
      effective_tier_name: tierName || null,
      card_count: realSize,
      minimum_cards: tier?.minimum_cards ?? 1,
      maximum_cards: tier?.maximum_cards ?? null,
      short_by: 0,
      expected_profit: null,
      grading_cost: null,
      expected_profit_if_filled: null,
      viable: true,
      reason: null,
      cards: [],
    }

    const short = tier !== null && realSize < tier.minimum_cards
    if (short && tier) {
      batch.short_by = tier.minimum_cards - realSize
      batch.viable = false
    }

    let profitMinor = 0
    let costMinor = 0
    for (const { evaluation, cardId } of members) {
      const placed = reverify(evaluate, cardId, evaluation, batch, realSize)
      batch.cards.push(placed)
      if (placed.still_pays) {
        profitMinor += toMinor(placed.expected_profit ?? 0)
        costMinor += toMinor(placed.grading_cost ?? 0)
      } else {
        plan.stopped_paying.push(placed)
      }
    }

    const landed = new Set(
      batch.cards.filter((card) => card.still_pays).map((card) => card.tier_name),
    )
    batch.effective_tier_name = landed.size === 1 ? [...landed][0] : (tierName || null)

    if (short && tier) {
      const filled = members.reduce(
        (sum, { evaluation }) => sum + toMinor(evaluation.recommendation.expected_profit ?? 0),
        0,
      )
      batch.expected_profit_if_filled = Number((filled / 100).toFixed(2))
      const sameTier = batch.effective_tier_name === tierName
      batch.reason =
        `${companyCode} ${tierName} needs ${tier.minimum_cards} cards and this batch has ` +
        `${realSize}. ` +
        (sameTier ? '' : `As it stands these would be graded at ${batch.effective_tier_name}. `) +
        `Adding ${batch.short_by} more card(s) at this tier is worth ` +
        `${money(filled - profitMinor)} across the cards already in it.`
    }

    batch.expected_profit = Number((profitMinor / 100).toFixed(2))
    batch.grading_cost = Number((costMinor / 100).toFixed(2))
    plan.batches.push(batch)
  }

  plan.batches.sort((a, b) => (b.expected_profit ?? 0) - (a.expected_profit ?? 0))
  const paying = plan.batches.flatMap((batch) => batch.cards.filter((card) => card.still_pays))
  plan.placed = paying.length
  if (paying.length) {
    plan.expected_profit = Number(
      (paying.reduce((sum, card) => sum + toMinor(card.expected_profit ?? 0), 0) / 100).toFixed(2),
    )
    plan.total_grading_cost = Number(
      (paying.reduce((sum, card) => sum + toMinor(card.grading_cost ?? 0), 0) / 100).toFixed(2),
    )
  }

  finish(plan, input.limit)
  return plan
}

/**
 * Cost this card again at the size its batch actually came out at.
 *
 * `grade_if_batch_filled` was a fair answer while the batch was hypothetical.
 * Here the size is a known fact, so it means "not in the batch you have".
 */
function reverify(
  evaluate: (cardId: string, batchSize: number) => CardEvaluation,
  cardId: string,
  routedEval: CardEvaluation,
  batch: ProposedBatch,
  realSize: number,
): PlacedCard {
  const routed = routedEval.recommendation
  const placed: PlacedCard = {
    card_id: cardId,
    name: routedEval.raw.display_name,
    set_label: routedEval.raw.set_label,
    company_code: batch.company_code,
    tier_id: batch.tier_id,
    tier_name: batch.tier_name,
    declared_value: routedEval.grading_options.declared_value,
    decision_when_routed: routed.decision,
    decision_in_batch: routed.decision,
    expected_profit: routed.expected_profit,
    grading_cost: routed.grading_cost,
    opportunity_score: routed.opportunity_score,
    still_pays: true,
    reason: null,
    cheaper_tier_name: null,
    cheaper_tier_saving: null,
  }

  if (realSize === routed.assumed_batch_size) {
    suggestCheaperTier(placed, routedEval)
    return placed
  }

  const actual = evaluate(cardId, realSize)
  const recommendation = actual.recommendation
  placed.decision_in_batch = recommendation.decision
  placed.opportunity_score = recommendation.opportunity_score

  // The tier the card actually lands on at this count, which is not always the
  // one it was routed to: Bulk needs twenty-five, so in a batch of nine these
  // are Economy cards. Reporting them as Bulk at Economy's price would quote a
  // route that does not exist at this size.
  placed.tier_name = recommendation.tier_name ?? batch.tier_name
  placed.tier_id = placed.tier_name === batch.tier_name ? batch.tier_id : null

  const atThisSize = actual.expected_outcomes.outcomes.find(
    (outcome) =>
      outcome.company_code === batch.company_code && outcome.tier_name === placed.tier_name,
  )
  placed.expected_profit = atThisSize?.expected_profit ?? recommendation.expected_profit
  placed.grading_cost = atThisSize?.grading_cost ?? recommendation.grading_cost

  if (recommendation.decision !== 'grade') {
    placed.still_pays = false
    const was = routed.expected_profit
    const now = placed.expected_profit
    const moved =
      was !== null && now !== null && toMinor(was) !== toMinor(now)
        ? ` Expected profit falls from ${money(toMinor(was))} to ${money(toMinor(now))}.`
        : ''
    placed.reason =
      `Worth grading in a batch of ${routed.assumed_batch_size}, but this batch has ` +
      `${realSize} — so it carries a bigger share of the postage.${moved}`
  } else {
    suggestCheaperTier(placed, actual)
  }
  return placed
}

/** "Move it to Bulk and save £2.20" — but only where the move is legal. */
function suggestCheaperTier(placed: PlacedCard, evaluation: CardEvaluation): void {
  const options = evaluation.grading_options.options
  const current = options.find(
    (option) =>
      option.company_code === placed.company_code && option.tier_name === placed.tier_name,
  )
  if (!current || current.total_cost === null) return

  const cheaper = options.filter(
    (option) =>
      option.company_code === placed.company_code &&
      option.available &&
      option.total_cost !== null &&
      option.total_cost < current.total_cost!,
  )
  if (!cheaper.length) return

  const best = cheaper.reduce((low, option) =>
    (option.total_cost ?? 0) < (low.total_cost ?? 0) ? option : low,
  )
  placed.cheaper_tier_name = best.tier_name
  placed.cheaper_tier_saving = Number((current.total_cost - (best.total_cost ?? 0)).toFixed(2))
}

function finish(plan: OptimiserPlan, limit: number): void {
  if (plan.truncated) {
    plan.notes.push(
      `Only the ${limit} most recently updated ready cards were considered. Raise the limit ` +
        'to include the rest.',
    )
  }
  if (plan.stopped_paying.length) {
    plan.notes.push(
      `${plan.stopped_paying.length} card(s) were worth grading in a full batch but not in the ` +
        'batch they ended up in. They are listed against their batch, and are not counted in ' +
        'any total.',
    )
  }
  const short = plan.batches.filter((batch) => !batch.viable)
  if (short.length) {
    plan.notes.push(
      `${short.length} proposed batch(es) are short of their tier's minimum. They are costed ` +
        'as they stand, so you can see what filling them would be worth.',
    )
  }

  if (plan.status === 'insufficient_data') return
  if (plan.truncated || short.length || plan.stopped_paying.length || plan.unplaced.length) {
    plan.status = 'partial'
    plan.reason = plan.notes[0] ?? null
  } else {
    plan.status = 'ok'
  }
}
