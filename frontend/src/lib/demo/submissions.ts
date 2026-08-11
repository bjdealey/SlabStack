/**
 * Real submissions, ported from the backend (spec sections 12, 32).
 *
 * Phase 4 costed a *hypothetical* card in a hypothetical batch. This costs the
 * batch you actually built, and that changes three things:
 *
 * **Insurance is charged on the parcel**, from the real declared values — not
 * one card's value multiplied by the batch.
 *
 * **Allocation can finally be value-weighted**, because there are now other
 * cards to weight against.
 *
 * **Minimums are per tier, not per parcel.** Bulk pricing needs N cards *at
 * bulk rates*; shipping is shared across the whole parcel regardless of tier,
 * which is what makes a mixed submission worth building.
 *
 * Nothing is silently dropped: a batch that breaks a tier's rules comes back
 * costed with every violation named.
 */

import type {
  Card,
  GradingCompany,
  GradingTier,
  Submission,
  SubmissionCardLine,
  SubmissionTierGroup,
} from '@/lib/types'
import { allocateEqually, applyPct, heldMembership } from './economics'
import { toMajor, toMinor } from './market'

/** One card as stored in the demo's in-memory submission. */
export interface StoredLine {
  id: string
  card_id: string
  tier_id: string | null
  declared_value_minor: number | null
  declared_value_source: string
  declared_value_confidence: string | null
  /** Frozen when the card joined the parcel — the prediction worth scoring. */
  predicted_grade: number | null
  actual_grade: number | null
  cert_number: string | null
  status: string
  sort_order: number
  notes: string | null
}

export interface StoredSubmission {
  id: string
  reference: string
  name: string | null
  company_id: string
  tier_id: string | null
  status: string
  currency: string
  cost_allocation_method: string
  shipping_out_minor: number
  shipping_return_minor: number
  handling_minor: number
  other_fees_minor: number
  membership_allocation_minor: number
  submitted_at: string | null
  received_at: string | null
  returned_at: string | null
  tracking_outbound: string | null
  tracking_return: string | null
  notes: string | null
  created_at: string
  cards: StoredLine[]
}

const money = (minor: number | null | undefined) =>
  minor === null || minor === undefined
    ? '—'
    : `${minor < 0 ? '−' : ''}£${(Math.abs(minor) / 100).toFixed(2)}`

const displayName = (card: Card) =>
  card.card_number ? `${card.name} ${card.card_number}` : card.name

const setLabel = (card: Card) =>
  card.set_name && card.set_code
    ? `${card.set_name} (${card.set_code})`
    : (card.set_name ?? card.set_code ?? null)

/**
 * Penny-exact split by arbitrary weights. Remainder pennies go to the largest
 * weights first, so the parts always sum back to the total.
 */
export function allocateWeighted(totalMinor: number, weights: number[]): number[] {
  if (!weights.length) return []
  const totalWeight = weights.reduce((sum, weight) => sum + weight, 0)
  if (totalWeight <= 0) return allocateEqually(totalMinor, weights.length)

  const raw = weights.map((weight) => (totalMinor * weight) / totalWeight)
  const floors = raw.map((value) => Math.floor(value))
  let remainder = totalMinor - floors.reduce((sum, value) => sum + value, 0)
  const order = raw
    .map((value, index) => ({ index, fraction: value - floors[index] }))
    .sort((a, b) => b.fraction - a.fraction)
  for (const { index } of order) {
    if (remainder <= 0) break
    floors[index] += 1
    remainder -= 1
  }
  return floors
}

/** Cost a real batch from the cards actually in it. */
export function costSubmission(input: {
  submission: StoredSubmission
  cards: Map<string, Card>
  companies: GradingCompany[]
  declaredFor: (card: Card, companyCode: string | null) => number | null
  insurancePct: number
  today: string
}): Submission {
  const { submission, cards, companies, insurancePct, today } = input
  const company = companies.find((item) => item.id === submission.company_id) ?? null
  const membership = company ? heldMembership(company, today) : null

  const base: Submission = {
    id: submission.id,
    reference: submission.reference,
    name: submission.name,
    status: submission.status,
    currency: submission.currency,
    company_id: submission.company_id,
    company_code: company?.code ?? null,
    company_name: company?.name ?? null,
    tier_id: submission.tier_id,
    card_count: 0,
    declared_value_total: 0,
    shipping_out: toMajor(submission.shipping_out_minor),
    shipping_return: toMajor(submission.shipping_return_minor),
    insurance: 0,
    handling: toMajor(submission.handling_minor),
    other_fees: toMajor(submission.other_fees_minor),
    tier_additional_fees: 0,
    shared_pot: 0,
    grading_fees: 0,
    per_card_fees: 0,
    declared_value_fees: 0,
    membership_discount: 0,
    total_cost: 0,
    cost_per_card: null,
    allocation_method: submission.cost_allocation_method,
    allocation_note: null,
    membership_code: membership?.code ?? null,
    submitted_at: submission.submitted_at,
    received_at: submission.received_at,
    returned_at: submission.returned_at,
    tracking_outbound: submission.tracking_outbound,
    tracking_return: submission.tracking_return,
    notes: submission.notes,
    tiers: [],
    cards: [],
    blockers: [],
    warnings: [],
  }

  if (!company) {
    base.blockers.push('This submission has no grading company.')
    return base
  }

  const rows = [...submission.cards].sort((a, b) => a.sort_order - b.sort_order)
  if (!rows.length) {
    base.blockers.push('No cards in this submission yet. Add the cards you intend to send.')
    return base
  }

  const defaultTier = company.tiers.find((tier) => tier.id === submission.tier_id) ?? null
  const tiersUsed = new Map<string, GradingTier>()
  const lines: SubmissionCardLine[] = []
  const weights: number[] = []

  for (const row of rows) {
    const card = cards.get(row.card_id)
    if (!card) continue

    const tier = company.tiers.find((item) => item.id === row.tier_id) ?? defaultTier
    if (tier) tiersUsed.set(tier.id, tier)

    let valueMinor = row.declared_value_minor
    let source = row.declared_value_source
    let confidence = row.declared_value_confidence
    if (card.user_declared_value !== null) {
      valueMinor = toMinor(card.user_declared_value)
      source = 'user'
      confidence = 'high'
    } else if (valueMinor === null) {
      valueMinor = input.declaredFor(card, company.code)
      source = 'system'
      confidence = null
    }

    const line: SubmissionCardLine = {
      submission_card_id: row.id,
      card_id: card.id,
      name: displayName(card),
      set_label: setLabel(card),
      tier_id: tier?.id ?? null,
      tier_name: tier?.tier_name ?? null,
      declared_value: toMajor(valueMinor),
      declared_value_source: source,
      declared_value_confidence: confidence as SubmissionCardLine['declared_value_confidence'],
      base_fee: 0,
      membership_discount: null,
      grading_fee: 0,
      per_card_fees: null,
      declared_value_fee: null,
      allocated_overhead: 0,
      total_cost: 0,
      allocation_weight: 1,
      predicted_grade: row.predicted_grade,
      actual_grade: row.actual_grade,
      status: row.status,
      sort_order: row.sort_order,
      blockers: [],
    }

    if (!tier) {
      line.blockers.push(
        'No tier chosen for this card, and the submission has no default tier.',
      )
    } else {
      let discountPct = membership ? tier.membership_discount_pct : 0
      if (membership && membership.discount_pct > discountPct) discountPct = membership.discount_pct
      const baseFee = toMinor(tier.price)
      const discount = discountPct ? applyPct(baseFee, discountPct) : 0
      line.base_fee = toMajor(baseFee)
      line.membership_discount = toMajor(discount) || null
      line.grading_fee = toMajor(baseFee - discount)
      line.per_card_fees = toMajor(toMinor(tier.per_card_fees)) || null
      if (tier.declared_value_fee_pct && valueMinor) {
        line.declared_value_fee = toMajor(applyPct(valueMinor, tier.declared_value_fee_pct))
      }
      if (tier.price <= 0) {
        line.blockers.push(
          `No price configured for ${company.code} ${tier.tier_name}. ` +
            'Add current pricing in Settings → Grading.',
        )
      }
      if (valueMinor !== null) {
        const floor = tier.min_declared_value === null ? null : toMinor(tier.min_declared_value)
        const ceiling = tier.max_declared_value === null ? null : toMinor(tier.max_declared_value)
        if (floor && valueMinor < floor) {
          line.blockers.push(
            `Declared value ${money(valueMinor)} is below ${company.code} ` +
              `${tier.tier_name}'s minimum of ${money(floor)}.`,
          )
        }
        if (ceiling && valueMinor > ceiling) {
          line.blockers.push(
            `Declared value ${money(valueMinor)} exceeds ${company.code} ` +
              `${tier.tier_name}'s ceiling of ${money(ceiling)} — this card needs a more ` +
              'expensive tier.',
          )
        }
      }
    }

    if (valueMinor === null) {
      line.blockers.push(
        'No declared value for this card — the parcel cannot be insured accurately. ' +
          'Add comparable sales or set your own figure.',
      )
    }

    lines.push(line)
    weights.push(valueMinor ?? 0)
  }

  base.card_count = lines.length
  const declaredTotal = weights.reduce((sum, value) => sum + value, 0)
  base.declared_value_total = toMajor(declaredTotal)

  const insuranceMinor =
    insurancePct && declaredTotal ? applyPct(declaredTotal, insurancePct) : 0
  const tierFeesMinor = [...tiersUsed.values()].reduce(
    (sum, tier) => sum + toMinor(tier.additional_fees),
    0,
  )
  const potMinor =
    submission.shipping_out_minor +
    submission.shipping_return_minor +
    insuranceMinor +
    submission.handling_minor +
    submission.other_fees_minor +
    tierFeesMinor +
    submission.membership_allocation_minor

  base.insurance = toMajor(insuranceMinor)
  base.tier_additional_fees = toMajor(tierFeesMinor)
  base.shared_pot = toMajor(potMinor)

  // Allocation. Value-weighted still falls back when there is nothing to weight
  // by, and says so rather than producing an equal split under the other label.
  let method = submission.cost_allocation_method
  let allocationWeights = lines.map(() => 1)
  if (method === 'value_weighted' && declaredTotal > 0) {
    allocationWeights = weights
    base.allocation_note =
      'Shared costs are split by declared value, so the expensive cards carry more of the ' +
      'postage and insurance they are responsible for.'
  } else if (method === 'value_weighted') {
    method = 'equal'
    base.allocation_note =
      'No card in this submission has a declared value, so there is nothing to weight by — ' +
      'shared costs are split equally instead.'
  } else {
    base.allocation_note = 'Shared costs are split equally across every card in the parcel.'
  }
  base.allocation_method = method

  const shares = potMinor ? allocateWeighted(potMinor, allocationWeights) : lines.map(() => 0)
  lines.forEach((line, index) => {
    line.allocation_weight = allocationWeights[index]
    line.allocated_overhead = toMajor(shares[index])
    line.total_cost = Number(
      (
        (line.grading_fee ?? 0) +
        (line.per_card_fees ?? 0) +
        (line.declared_value_fee ?? 0) +
        (line.allocated_overhead ?? 0)
      ).toFixed(2),
    )
  })

  base.cards = lines
  const sum = (pick: (line: SubmissionCardLine) => number | null) =>
    Number(lines.reduce((total, line) => total + (pick(line) ?? 0), 0).toFixed(2))
  base.grading_fees = sum((line) => line.grading_fee)
  base.per_card_fees = sum((line) => line.per_card_fees)
  base.declared_value_fees = sum((line) => line.declared_value_fee)
  base.membership_discount = sum((line) => line.membership_discount)
  base.total_cost = sum((line) => line.total_cost)
  base.cost_per_card = lines.length
    ? Number((base.total_cost / lines.length).toFixed(2))
    : null

  base.tiers = tierGroups(lines, tiersUsed, company, membership !== null)
  base.blockers = submissionBlockers(base, lines)
  base.warnings = submissionWarnings(base, company, membership !== null, potMinor)
  return base
}

/** Count the cards on each tier and check that tier's own rules. */
function tierGroups(
  lines: SubmissionCardLine[],
  tiersUsed: Map<string, GradingTier>,
  company: GradingCompany,
  holdsMembership: boolean,
): SubmissionTierGroup[] {
  const counts = new Map<string | null, number>()
  for (const line of lines) counts.set(line.tier_id, (counts.get(line.tier_id) ?? 0) + 1)

  const groups: SubmissionTierGroup[] = []
  for (const [tierId, count] of counts) {
    const tier = tierId ? tiersUsed.get(tierId) : undefined
    const group: SubmissionTierGroup = {
      tier_id: tierId,
      tier_name: tier?.tier_name ?? null,
      company_code: company.code,
      card_count: count,
      minimum_cards: tier?.minimum_cards ?? 1,
      maximum_cards: tier?.maximum_cards ?? null,
      short_by: 0,
      over_by: 0,
      blockers: [],
    }
    if (!tier) {
      group.blockers.push('These cards have no tier.')
    } else {
      if (count < tier.minimum_cards) {
        group.short_by = tier.minimum_cards - count
        group.blockers.push(
          `${company.code} ${tier.tier_name} needs ${tier.minimum_cards} cards at that tier; ` +
            `this submission has ${count}. Add ${group.short_by} more, or move them to a tier ` +
            'with no minimum.',
        )
      }
      if (tier.maximum_cards !== null && count > tier.maximum_cards) {
        group.over_by = count - tier.maximum_cards
        group.blockers.push(
          `${company.code} ${tier.tier_name} takes at most ${tier.maximum_cards} cards per ` +
            `submission; this has ${count}. Split ${group.over_by} into another submission.`,
        )
      }
      if (tier.membership_required && !holdsMembership) {
        group.blockers.push(
          `${company.code} ${tier.tier_name} requires a membership you do not hold.`,
        )
      }
    }
    groups.push(group)
  }
  groups.sort((a, b) => (a.tier_name ?? '').localeCompare(b.tier_name ?? ''))
  return groups
}

function submissionBlockers(result: Submission, lines: SubmissionCardLine[]): string[] {
  const blockers: string[] = []
  for (const group of result.tiers) blockers.push(...group.blockers)

  const unpriced = lines.filter((line) => line.declared_value === null)
  if (unpriced.length) {
    const names = unpriced.slice(0, 3).map((line) => line.name).join(', ')
    const more = unpriced.length > 3 ? ` and ${unpriced.length - 3} more` : ''
    blockers.push(
      `${unpriced.length} card(s) have no declared value (${names}${more}). The parcel cannot ` +
        'be insured accurately until they do.',
    )
  }

  const overCeiling = lines.filter((line) =>
    line.blockers.some((item) => item.includes('ceiling')),
  )
  if (overCeiling.length) {
    blockers.push(
      `${overCeiling.length} card(s) are worth more than their tier covers. Move them to a ` +
        'higher tier before sending.',
    )
  }
  return blockers
}

function submissionWarnings(
  result: Submission,
  company: GradingCompany,
  holdsMembership: boolean,
  potMinor: number,
): string[] {
  const warnings: string[] = []

  if (potMinor && result.card_count === 1) {
    warnings.push(
      `One card carries the whole ${money(potMinor)} of shipping and insurance. Adding cards ` +
        'to this parcel lowers the cost of every card in it.',
    )
  }

  if (!holdsMembership) {
    const discountable = company.tiers.filter(
      (tier) => tier.active && tier.membership_discount_pct > 0,
    )
    const available = company.memberships.filter((item) => item.active)
    if (discountable.length && available.length) {
      const saving = result.cards.reduce(
        (sum, line) =>
          sum + applyPct(toMinor(line.base_fee ?? 0), discountable[0].membership_discount_pct),
        0,
      )
      const fee = toMinor(available[0].annual_fee)
      if (saving > 0) {
        const verdict =
          saving > fee
            ? `saves ${money(saving - fee)} on this submission alone`
            : `would need ${money(fee - saving)} more of grading to break even`
        warnings.push(
          `A ${company.code} membership costs ${money(fee)} a year and would take ` +
            `${money(saving)} off these fees — it ${verdict}.`,
        )
      }
    }
  }

  if (result.allocation_method === 'equal' && result.card_count > 1) {
    const values = result.cards.map((line) => toMinor(line.declared_value ?? 0))
    const highest = Math.max(...values)
    const lowest = Math.min(...values)
    if (highest && lowest * 4 < highest) {
      warnings.push(
        'The cards in this parcel differ widely in value, and shared costs are split equally — ' +
          'the cheapest card carries as much postage as the most valuable. Value-weighted ' +
          'allocation is in Settings → Grading.',
      )
    }
  }
  return warnings
}

/** A human-readable reference: SUB-2026-08-001, sequential within the month. */
export function nextReference(existing: string[], today: string): string {
  const prefix = `SUB-${today.slice(0, 7)}-`
  const used = existing
    .filter((reference) => reference.startsWith(prefix))
    .map((reference) => Number(reference.split('-').pop()))
    .filter((value) => Number.isFinite(value))
  const next = used.length ? Math.max(...used) + 1 : 1
  return `${prefix}${String(next).padStart(3, '0')}`
}
