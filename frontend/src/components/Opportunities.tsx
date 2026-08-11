import { Link } from 'react-router-dom'
import { Sparkles, UserCheck } from 'lucide-react'
import type { CollectionDecisions, Opportunity } from '@/lib/types'
import { DecisionBadge, StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn, formatMoney, formatNumber } from '@/lib/utils'

/** Enough to act on this week without becoming a second collection page. */
const SHOWN = 12

/** The engine's own line for "thin", so both views mark the same rows. */
const THIN_COVERAGE = 0.8

/**
 * The collection's ranked to-do list: which cards to send, best first.
 *
 * The engine has already decided each one individually; this only orders them.
 * Cards it declined are still listed — a "sell raw" with the reason attached is
 * as useful as a "grade", and hiding them would make the list look like the
 * whole collection is a winner.
 */
export function Opportunities({
  decisions,
  loading,
  error,
}: {
  decisions: CollectionDecisions | undefined
  loading: boolean
  error: unknown
}) {
  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>What to grade next</PanelTitle>
          <PanelDescription>
            {decisions
              ? summarise(decisions)
              : 'Ranked by opportunity score — profitability, grade odds, liquidity, trend and risk.'}
          </PanelDescription>
        </div>
        {decisions ? <StatusBadge status={decisions.status} /> : null}
      </PanelHeader>

      <PanelBody>
        {error ? <ErrorState error={error} /> : null}

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : null}

        {!loading && decisions && !decisions.opportunities.length ? (
          <EmptyState
            icon={<Sparkles className="size-8" />}
            title="Nothing to rank yet"
            description={
              decisions.reason ??
              'A card needs a condition assessment and comparable sales before the engine can decide it.'
            }
          />
        ) : null}

        {decisions?.opportunities.length ? (
          <div className="space-y-1.5">
            {decisions.opportunities.slice(0, SHOWN).map((row) => (
              <OpportunityRow key={row.card_id} row={row} currency={decisions.currency} />
            ))}
            {decisions.opportunities
              .slice(0, SHOWN)
              .some((row) => row.expected_profit !== null && row.coverage < THIN_COVERAGE) ? (
              <p className="pt-1 text-[0.7rem] leading-relaxed text-ink-faint">
                * Profit is conditional: not every grade this card might get has ever sold, so the
                figure is what you would make if it lands on one that has.
              </p>
            ) : null}
            {decisions.opportunities.length > SHOWN ? (
              <p className="pt-1 text-xs text-ink-faint">
                And {formatNumber(decisions.opportunities.length - SHOWN)} more analysed card(s).
              </p>
            ) : null}
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

function OpportunityRow({ row, currency }: { row: Opportunity; currency: string }) {
  const thin = row.expected_profit !== null && row.coverage < THIN_COVERAGE
  return (
    <Link
      to={`/cards/${row.card_id}`}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line px-3 py-2.5 transition-colors hover:bg-canvas"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink">{row.name}</span>
        <span className="block truncate text-xs text-ink-faint">
          {row.set_label ?? 'Unknown set'}
          {row.company_code ? ` · ${row.company_code}${row.tier_name ? ` ${row.tier_name}` : ''}` : ''}
        </span>
      </span>

      {/* A big profit on a thinly-priced card is conditional, and a ranked list
          is exactly where that qualifier gets lost. So it rides with the number. */}
      <span className="tabular shrink-0 text-right">
        <span className={cn('block text-sm font-semibold', thin ? 'text-ink-muted' : 'text-ink')}>
          {formatMoney(row.expected_profit, currency, { signed: true })}
          {thin ? '*' : ''}
        </span>
        <span className="block text-[0.7rem] text-ink-faint">
          {thin
            ? `only ${Math.round(row.coverage * 100)}% priced`
            : row.roi_pct === null
              ? 'no ROI'
              : `${formatNumber(row.roi_pct)}% ROI`}
        </span>
      </span>

      <span className="tabular w-12 shrink-0 text-right text-xs text-ink-muted">
        {row.probability_of_profit === null
          ? '—'
          : `${Math.round(row.probability_of_profit * 100)}%`}
      </span>

      <span className="tabular w-10 shrink-0 text-right text-sm font-semibold text-brand">
        {row.opportunity_score === null ? '—' : formatNumber(row.opportunity_score)}
      </span>

      {row.is_user_override ? (
        <Badge tone="brand" title="You set this decision yourself">
          <UserCheck className="size-3" />
        </Badge>
      ) : null}
      <DecisionBadge decision={row.decision} />
    </Link>
  )
}

/** Always the totals *and* their denominator — never one without the other. */
function summarise(decisions: CollectionDecisions): string {
  if (!decisions.analysed) {
    return decisions.reason ?? 'No card can be decided yet.'
  }
  const worth =
    (decisions.counts.grade ?? 0) + (decisions.counts.grade_if_batch_filled ?? 0)
  const scope = `Analysed ${formatNumber(decisions.analysed)} of ${formatNumber(decisions.total_cards)} cards in a submission of ${decisions.batch_size}.`
  if (!worth) return `${scope} None of them clears your bar for grading.`
  return `${scope} ${formatNumber(worth)} worth grading, costing ${formatMoney(
    decisions.total_grading_cost,
    decisions.currency,
  )} to return ${formatMoney(decisions.expected_profit, decisions.currency, { signed: true })}.`
}
