/**
 * The grading-cost and net-sale engines, ported for the browser-only demo.
 *
 * A companion to `backend/app/services/economics.py`, kept in step with it by
 * hand. It exists because GitHub Pages has no server to run the Python;
 * `VITE_DEMO` is a compile-time constant, so a normal build never bundles it.
 *
 * Money is in **minor units** here, exactly as it is server-side, so the same
 * rounding produces the same pennies.
 *
 * The behaviours worth keeping in step are the ones the tests name: a declared
 * value is probability-weighted rather than best-case and never below the raw
 * card, an unpriced tier is reported rather than costed at zero, shared costs
 * split penny-exact, a slab pays the graded postage, and a best case never
 * pairs one grader's fee with another grader's slab price.
 */

import type {
  Confidence,
  GradingCompany,
  GradingTier,
  MarketPrice,
  SellingProfile,
} from '@/lib/types'
import { toMajor, toMinor } from './market'

/* --- Shared helpers ------------------------------------------------------- */

/** Percentage of a minor-unit amount, rounded half-up like the server's `apply_pct`. */
export function applyPct(minor: number, pct: number): number {
  return Math.round((minor * pct) / 100)
}

/**
 * Split a total across equal weights losing not a single penny. The remainder
 * goes to the earliest parts, matching `money.allocate`.
 */
export function allocateEqually(totalMinor: number, parts: number): number[] {
  if (parts <= 0) return []
  const base = Math.floor(totalMinor / parts)
  const remainder = totalMinor - base * parts
  return Array.from({ length: parts }, (_, index) => base + (index < remainder ? 1 : 0))
}

export function gradeLabelFor(companyCode: string, grade: number): string {
  return `${companyCode.toUpperCase()} ${grade}`
}

/* --- Declared value ------------------------------------------------------- */

export interface DeclaredValue {
  valueMinor: number | null
  source: 'system' | 'user'
  confidence: Confidence
  basis: string | null
  coverage: number | null
}

const bestOf = (price: MarketPrice | undefined): number | null => {
  if (!price) return null
  const value = price.user_value ?? price.realistic_sale ?? price.median
  return value === null || value === undefined ? null : toMinor(value)
}

/**
 * Deliberately not the top-grade value: declared value decides insurance and
 * tier eligibility, so over-declaring buys a more expensive tier than the card
 * needs and under-declaring leaves it under-insured.
 */
export function suggestDeclaredValue(
  card: { user_raw_value: number | null; purchase_price: number | null },
  prices: MarketPrice[],
  probabilities: Record<number, number> | null,
  companyCode: string | null,
): DeclaredValue {
  const byLabel = new Map(prices.map((price) => [price.grade_label, price]))
  const rawMinor = bestOf(byLabel.get('raw'))

  if (probabilities && companyCode) {
    let weighted = 0
    let covered = 0
    for (const [grade, probability] of Object.entries(probabilities)) {
      const value = bestOf(byLabel.get(gradeLabelFor(companyCode, Number(grade))))
      if (!value) continue
      weighted += value * probability
      covered += probability
    }
    if (covered > 0) {
      // Renormalise over the covered mass: grades with no price are unknown,
      // not worthless.
      let valueMinor = Math.round(weighted / covered)
      if (rawMinor && valueMinor < rawMinor) valueMinor = rawMinor
      return {
        valueMinor,
        source: 'system',
        confidence: covered >= 0.8 ? 'high' : covered >= 0.5 ? 'medium' : 'low',
        coverage: Math.round(covered * 1000) / 1000,
        basis:
          `Probability-weighted across the ${companyCode} grades with sales data, covering ` +
          `${Math.round(covered * 100)}% of the likely outcomes.`,
      }
    }
  }

  if (rawMinor) {
    return {
      valueMinor: rawMinor,
      source: 'system',
      confidence: 'low',
      coverage: null,
      basis:
        'The raw market value — no graded sales are stored for this card, so there is nothing ' +
        "to estimate the slab's value from. A slab is worth at least the card inside it, so " +
        'this is a floor rather than an estimate.',
    }
  }

  const fallback = card.user_raw_value ?? card.purchase_price
  if (fallback) {
    return {
      valueMinor: toMinor(fallback),
      source: 'system',
      confidence: 'none',
      coverage: null,
      basis:
        card.user_raw_value !== null
          ? 'Your own raw estimate.'
          : 'What you paid for it — a floor, not a valuation.',
    }
  }

  return {
    valueMinor: null,
    source: 'system',
    confidence: 'none',
    coverage: null,
    basis: 'No value known for this card. Add comparable sales or your own estimate.',
  }
}

/* --- Assumptions ---------------------------------------------------------- */

export interface SubmissionAssumptions {
  batchSize: number
  shippingOutMinor: number
  shippingReturnMinor: number
  insurancePct: number
  handlingMinor: number
  allocationMethod: string
  allocationNote: string | null
}

export function assumptionsFrom(
  settings: Record<string, unknown>,
  batchSize = 1,
): SubmissionAssumptions {
  const money = (key: string) => toMinor(Number(settings[key] ?? 0) || 0)
  let method = String(settings.cost_allocation_method ?? 'equal')
  let note: string | null = null
  if (method === 'value_weighted') {
    // Weighting by value needs the other cards' values, which do not exist
    // until there is a real batch.
    note =
      'Value-weighted allocation needs a real batch to weight against, so these figures split ' +
      'shared costs equally. Build a submission to see your split.'
    method = 'equal'
  }
  return {
    batchSize: Math.max(1, Math.floor(batchSize)),
    shippingOutMinor: money('default_submission_shipping_out'),
    shippingReturnMinor: money('default_submission_shipping_return'),
    insurancePct: Number(settings.default_submission_insurance_pct ?? 0) || 0,
    handlingMinor: 0,
    allocationMethod: method,
    allocationNote: note,
  }
}

/* --- Eligibility and cost -------------------------------------------------- */

const money = (minor: number | null | undefined) =>
  minor === null || minor === undefined ? '—' : `£${(minor / 100).toFixed(2)}`

export function heldMembership(company: GradingCompany, today: string) {
  return (
    company.memberships.find(
      (membership) =>
        membership.active &&
        membership.user_holds &&
        (!membership.expires_on || membership.expires_on >= today),
    ) ?? null
  )
}

function tierInEffect(tier: GradingTier, today: string): boolean {
  const started = !tier.effective_from || tier.effective_from <= today
  const ended = Boolean(tier.effective_to && tier.effective_to < today)
  return started && !ended
}

/**
 * Every tier, each with the reasons it cannot be used. Returned rather than
 * filtered out: "Bulk needs 25 cards and you have three" beats Bulk vanishing.
 */
export function eligibleTiers(
  company: GradingCompany,
  declaredValueMinor: number | null,
  batchSize: number,
  today: string,
): { tier: GradingTier; blockers: string[] }[] {
  const membership = heldMembership(company, today)
  return company.tiers
    .filter((tier) => tier.active && tierInEffect(tier, today))
    .sort((a, b) => a.sort_order - b.sort_order || a.price - b.price)
    .map((tier) => {
      const blockers: string[] = []
      if (tier.price <= 0) {
        blockers.push(
          `No price configured for ${company.code} ${tier.tier_name}. ` +
            'Add current pricing in Settings → Grading.',
        )
      }
      if (declaredValueMinor !== null) {
        if (tier.min_declared_value && declaredValueMinor < toMinor(tier.min_declared_value)) {
          blockers.push(
            `Declared value is below this tier's minimum of ` +
              `${money(toMinor(tier.min_declared_value))}.`,
          )
        }
        if (tier.max_declared_value && declaredValueMinor > toMinor(tier.max_declared_value)) {
          blockers.push(
            `Declared value exceeds this tier's ceiling of ` +
              `${money(toMinor(tier.max_declared_value))} — a more expensive tier is required.`,
          )
        }
      }
      if (batchSize < tier.minimum_cards) {
        blockers.push(
          `Needs ${tier.minimum_cards} cards in one submission; ${batchSize} assumed.`,
        )
      }
      if (tier.maximum_cards !== null && batchSize > tier.maximum_cards) {
        blockers.push(`Takes at most ${tier.maximum_cards} cards per submission.`)
      }
      if (tier.membership_required && !membership) {
        const available = company.memberships.filter((item) => item.active)
        const cost = available.length ? ` (${money(toMinor(available[0].annual_fee))}/year)` : ''
        blockers.push(`Requires ${company.code} membership${cost}, which you do not hold.`)
      }
      return { tier, blockers }
    })
}

export interface CostBreakdown {
  baseFeeMinor: number
  membershipDiscountMinor: number
  gradingFeeMinor: number
  perCardFeesMinor: number
  declaredValueFeeMinor: number
  allocatedOverheadMinor: number
  totalMinor: number
  sharedTotalMinor: number
  batchSize: number
  membershipCode: string | null
}

export function sharedPot(
  tier: GradingTier,
  assumptions: SubmissionAssumptions,
  declaredValueMinor: number | null,
): number {
  let total = toMinor(tier.additional_fees)
  total += assumptions.shippingOutMinor
  total += assumptions.shippingReturnMinor
  total += assumptions.handlingMinor
  if (assumptions.insurancePct && declaredValueMinor) {
    // Insurance covers the whole parcel, so a batch insures batchSize cards.
    total += applyPct(declaredValueMinor * assumptions.batchSize, assumptions.insurancePct)
  }
  return total
}

export function costForTier(
  tier: GradingTier,
  company: GradingCompany,
  declaredValueMinor: number | null,
  assumptions: SubmissionAssumptions,
  today: string,
): CostBreakdown {
  const membership = heldMembership(company, today)
  let discountPct = membership ? tier.membership_discount_pct : 0
  if (membership && membership.discount_pct > discountPct) discountPct = membership.discount_pct

  const base = toMinor(tier.price)
  const discount = discountPct ? applyPct(base, discountPct) : 0
  const fee = base - discount

  const declaredFee =
    tier.declared_value_fee_pct && declaredValueMinor
      ? applyPct(declaredValueMinor, tier.declared_value_fee_pct)
      : 0

  const pot = sharedPot(tier, assumptions, declaredValueMinor)
  const share = pot ? allocateEqually(pot, assumptions.batchSize)[0] : 0
  const perCard = toMinor(tier.per_card_fees)

  return {
    baseFeeMinor: base,
    membershipDiscountMinor: discount,
    gradingFeeMinor: fee,
    perCardFeesMinor: perCard,
    declaredValueFeeMinor: declaredFee,
    allocatedOverheadMinor: share,
    totalMinor: fee + perCard + declaredFee + share,
    sharedTotalMinor: pot,
    batchSize: assumptions.batchSize,
    membershipCode: membership?.code ?? null,
  }
}

/* --- Net sale value ------------------------------------------------------- */

export interface NetSaleValue {
  grossMinor: number
  shippingIncomeMinor: number
  platformFeeMinor: number
  paymentFeeMinor: number
  otherFeeMinor: number
  listingFeeMinor: number
  postageCostMinor: number
  packagingCostMinor: number
  netMinor: number
  totalCostsMinor: number
  isGraded: boolean
}

/**
 * `graded` picks the postage and packaging figures. Using the raw postage for
 * a graded sale is one of the quieter ways to make grading look more
 * profitable than it is.
 */
export function netSaleValue(
  grossMinor: number | null,
  profile: SellingProfile | null | undefined,
  graded: boolean,
): NetSaleValue | null {
  if (grossMinor === null || !profile) return null

  const shippingIncome = toMinor(profile.shipping_charged_to_buyer)
  const feeBase = grossMinor + (profile.fees_apply_to_shipping ? shippingIncome : 0)

  const platformFee = applyPct(feeBase, profile.platform_fee_pct)
  const paymentFee = applyPct(feeBase, profile.payment_fee_pct) + toMinor(profile.payment_fixed_fee)
  const otherFee = applyPct(feeBase, profile.other_fee_pct)
  const listingFee = toMinor(profile.listing_fee)

  let postage = toMinor(profile.shipping_cost)
  let packaging = toMinor(profile.packaging_cost)
  if (graded) {
    if (profile.graded_shipping_cost !== null) postage = toMinor(profile.graded_shipping_cost)
    if (profile.graded_packaging_cost !== null) packaging = toMinor(profile.graded_packaging_cost)
  }

  const net =
    grossMinor +
    shippingIncome -
    platformFee -
    paymentFee -
    otherFee -
    listingFee -
    postage -
    packaging

  return {
    grossMinor,
    shippingIncomeMinor: shippingIncome,
    platformFeeMinor: platformFee,
    paymentFeeMinor: paymentFee,
    otherFeeMinor: otherFee,
    listingFeeMinor: listingFee,
    postageCostMinor: postage,
    packagingCostMinor: packaging,
    netMinor: net,
    totalCostsMinor: grossMinor + shippingIncome - net,
    isGraded: graded,
  }
}

export function netByGrade(
  prices: MarketPrice[],
  profile: SellingProfile | null | undefined,
): Map<string, NetSaleValue> {
  const results = new Map<string, NetSaleValue>()
  for (const price of prices) {
    const value = netSaleValue(bestOf(price), profile, price.grade_label !== 'raw')
    if (value) results.set(price.grade_label, value)
  }
  return results
}

export { toMajor }
