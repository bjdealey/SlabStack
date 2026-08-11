import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Coins,
  Filter as FilterIcon,
  PackageCheck,
  Sparkles,
  Tag,
  TrendingDown,
} from 'lucide-react'
import { api, keys } from '@/lib/api'
import type {
  FilterResult,
  Opportunity,
  RankedOpportunities,
  SellingCandidate,
  SellingQueue,
  SubmissionReturn,
  SubmissionReturns,
} from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { DecisionBadge, StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn, formatMoney, formatNumber, humanise } from '@/lib/utils'

/** The batch the sweep costs against, matching the dashboard. Bulk is the norm. */
const BATCH = 20

/** The engine's own line for "thin", so every view marks the same rows. */
const THIN_COVERAGE = 0.8

/**
 * Four questions, one set of answers.
 *
 * Nothing on this page computes anything. Every figure was produced by the
 * decision, market, economics or submission engine and can be traced to the
 * card page showing the same number — which is the whole point, because a
 * second opinion here would be the one that goes stale.
 */
export function Analytics() {
  return (
    <>
      <PageHeader
        title="Analytics"
        description="What to send, what to list, what came back, and the cuts to find them."
      />
      <div className="p-6">
        <Tabs defaultValue="opportunities" className="space-y-6">
          <TabsList>
            <TabsTrigger value="opportunities">What to grade</TabsTrigger>
            <TabsTrigger value="selling">What to sell</TabsTrigger>
            <TabsTrigger value="returns">What came back</TabsTrigger>
            <TabsTrigger value="filters">Cuts</TabsTrigger>
          </TabsList>

          <TabsContent value="opportunities">
            <OpportunitiesTab />
          </TabsContent>
          <TabsContent value="selling">
            <SellingTab />
          </TabsContent>
          <TabsContent value="returns">
            <ReturnsTab />
          </TabsContent>
          <TabsContent value="filters">
            <FiltersTab />
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}

/* --- What to grade -------------------------------------------------------- */

function OpportunitiesTab() {
  const [batch, setBatch] = useState(BATCH)
  const ranked = useQuery({
    queryKey: keys.rankedOpportunities(batch),
    queryFn: () => api.rankedOpportunities(batch),
  })

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Worth grading, best first</PanelTitle>
          <PanelDescription>
            {ranked.data
              ? summariseRanked(ranked.data, batch)
              : 'Ranked by opportunity score — profitability, grade odds, liquidity, trend and risk.'}
          </PanelDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* The list genuinely changes with the parcel you have in mind: a card
              that does not pay alone often pays in a submission of twenty. */}
          <div className="flex items-center gap-1">
            {[1, 20, 50].map((size) => (
              <Button
                key={size}
                size="sm"
                variant={batch === size ? 'primary' : 'secondary'}
                onClick={() => setBatch(size)}
              >
                {size === 1 ? 'Alone' : `Batch of ${size}`}
              </Button>
            ))}
          </div>
          {ranked.data ? <StatusBadge status={ranked.data.status} /> : null}
        </div>
      </PanelHeader>

      <PanelBody>
        {ranked.isError ? <ErrorState error={ranked.error} /> : null}
        {ranked.isLoading ? <RowSkeletons /> : null}

        {ranked.data && !ranked.data.items.length ? (
          <EmptyState
            icon={<Sparkles className="size-8" />}
            title="Nothing clears your bar"
            description={
              ranked.data.reason ??
              'No analysed card is worth grading at this batch size. Try a larger batch.'
            }
          />
        ) : null}

        {ranked.data?.items.length ? (
          <div className="space-y-1.5">
            {ranked.data.items.map((row) => (
              <OpportunityRow key={row.card_id} row={row} currency={ranked.data.currency} />
            ))}
            <ThinFootnote items={ranked.data.items} />
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
          {row.company_code
            ? ` · ${row.company_code}${row.tier_name ? ` ${row.tier_name}` : ''}`
            : ''}
        </span>
      </span>

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

      <span className="tabular w-16 shrink-0 text-right text-xs text-ink-muted">
        {formatMoney(row.grading_cost, currency)}
      </span>

      <span className="tabular w-10 shrink-0 text-right text-sm font-semibold text-brand">
        {row.opportunity_score === null ? '—' : formatNumber(row.opportunity_score)}
      </span>

      <DecisionBadge decision={row.decision} />
    </Link>
  )
}

/* --- What to sell --------------------------------------------------------- */

function SellingTab() {
  const queue = useQuery({ queryKey: keys.sellingQueue, queryFn: api.sellingQueue })

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>The selling queue</PanelTitle>
          <PanelDescription>
            {queue.data
              ? summariseQueue(queue.data)
              : 'Cards better off sold as they are, with a price to ask for each.'}
          </PanelDescription>
        </div>
        {queue.data ? <StatusBadge status={queue.data.status} /> : null}
      </PanelHeader>

      <PanelBody className="space-y-3">
        {queue.isError ? <ErrorState error={queue.error} /> : null}
        {queue.isLoading ? <RowSkeletons /> : null}

        {queue.data && !queue.data.items.length ? (
          <EmptyState
            icon={<Tag className="size-8" />}
            title="Nothing to list"
            description={
              queue.data.reason ??
              'No analysed card is better off sold raw. A card needs graded sales before the engine can reach that conclusion.'
            }
          />
        ) : null}

        {queue.data?.items.length ? (
          <>
            <div className="space-y-1.5">
              {queue.data.items.map((row) => (
                <SellingRow key={row.card_id} row={row} currency={queue.data.currency} />
              ))}
            </div>
            {/* The asking price is a strategy, not a valuation, and saying so
                once beats repeating it on every row. */}
            <p className="text-[0.7rem] leading-relaxed text-ink-faint">
              The asking price is a negotiating position: above what the card realistically
              fetches, by more when it trades rarely, and never above the upper quartile of what
              people have actually paid.
            </p>
            {queue.data.notes.map((note) => (
              <p key={note} className="text-[0.7rem] leading-relaxed text-ink-faint">
                {note}
              </p>
            ))}
          </>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

function SellingRow({ row, currency }: { row: SellingCandidate; currency: string }) {
  return (
    <Link
      to={`/cards/${row.card_id}`}
      className="block rounded-lg border border-line px-3 py-2.5 transition-colors hover:bg-canvas"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">{row.name}</span>
          <span className="block truncate text-xs text-ink-faint">
            {row.set_label ?? 'Unknown set'}
            {row.liquidity_band ? ` · ${humanise(row.liquidity_band)}` : ''}
            {row.days_since_last_sale !== null
              ? ` · last sold ${formatNumber(row.days_since_last_sale)}d ago`
              : ''}
          </span>
        </span>

        <span className="tabular shrink-0 text-right">
          <span className="block text-[0.7rem] uppercase tracking-wide text-ink-faint">Ask</span>
          <span className="block text-sm font-semibold text-ink">
            {formatMoney(row.suggested_listing, currency)}
          </span>
        </span>

        {/* What you keep, not the headline. The fees are the difference and
            they are the reason the two numbers are both here. */}
        <span className="tabular shrink-0 text-right">
          <span className="block text-[0.7rem] uppercase tracking-wide text-ink-faint">
            You keep
          </span>
          <span className="block text-sm font-semibold text-ink">
            {formatMoney(row.net_proceeds, currency)}
          </span>
        </span>

        <span className="tabular w-20 shrink-0 text-right">
          <span className="block text-[0.7rem] uppercase tracking-wide text-ink-faint">
            vs paid
          </span>
          <span
            className={cn(
              'block text-sm',
              row.gain_vs_purchase === null
                ? 'text-ink-faint'
                : row.gain_vs_purchase >= 0
                  ? 'text-positive'
                  : 'text-negative',
            )}
          >
            {row.gain_vs_purchase === null
              ? '—'
              : formatMoney(row.gain_vs_purchase, currency, { signed: true })}
          </span>
        </span>

        <DecisionBadge decision={row.decision} />
      </div>

      {row.listing_basis ? (
        <p className="pt-1.5 text-[0.7rem] leading-relaxed text-ink-faint">{row.listing_basis}</p>
      ) : null}
      {row.blockers.map((blocker) => (
        <p
          key={blocker}
          className="flex items-start gap-1.5 pt-1.5 text-[0.7rem] leading-relaxed text-caution"
        >
          <AlertTriangle className="mt-px size-3 shrink-0" />
          {blocker}
        </p>
      ))}
    </Link>
  )
}

/* --- What came back ------------------------------------------------------- */

function ReturnsTab() {
  const returns = useQuery({ queryKey: keys.submissionReturns, queryFn: api.submissionReturns })

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>How the parcels actually did</PanelTitle>
            <PanelDescription>
              {returns.data
                ? summariseReturns(returns.data)
                : 'Predicted grades against the grades that came back.'}
            </PanelDescription>
          </div>
          {returns.data ? <StatusBadge status={returns.data.status} /> : null}
        </PanelHeader>

        <PanelBody className="space-y-4">
          {returns.isError ? <ErrorState error={returns.error} /> : null}
          {returns.isLoading ? <RowSkeletons /> : null}

          {returns.data && !returns.data.submissions.length ? (
            <EmptyState
              icon={<PackageCheck className="size-8" />}
              title="No submissions yet"
              description="Send a parcel and record the grades when it comes back. This is the comparison the learning system is built on."
            />
          ) : null}

          {returns.data?.scored ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <Figure
                label="Spent on grading"
                value={formatMoney(returns.data.total_cost, returns.data.currency)}
              />
              <Figure
                label="Profit against cost"
                value={formatMoney(returns.data.total_profit, returns.data.currency, {
                  signed: true,
                })}
                tone={(returns.data.total_profit ?? 0) >= 0 ? 'positive' : 'negative'}
              />
              <Figure
                label="Return"
                value={
                  returns.data.roi_pct === null ? '—' : `${formatNumber(returns.data.roi_pct)}%`
                }
              />
            </div>
          ) : null}

          {returns.data?.submissions.map((entry) => (
            <ReturnCard
              key={entry.submission_id}
              entry={entry}
              currency={returns.data.currency}
            />
          ))}
        </PanelBody>
      </Panel>
    </div>
  )
}

function ReturnCard({ entry, currency }: { entry: SubmissionReturn; currency: string }) {
  return (
    <div className="rounded-lg border border-line">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-3 py-2.5">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-ink">{entry.reference}</span>
          <span className="block truncate text-xs text-ink-faint">
            {entry.company_code ?? 'No grader'} · {formatNumber(entry.card_count)} card(s) ·{' '}
            {formatNumber(entry.graded_count)} graded
          </span>
        </span>

        {entry.mean_surprise !== null ? (
          // The single most interesting number here: whether the grader was
          // kinder or harsher than the model expected, on average.
          <Badge tone={entry.mean_surprise >= 0 ? 'positive' : 'caution'}>
            {entry.mean_surprise >= 0 ? '+' : '−'}
            {Math.abs(entry.mean_surprise).toFixed(2)} vs predicted
          </Badge>
        ) : null}

        <span className="tabular shrink-0 text-right">
          <span className="block text-sm font-semibold text-ink">
            {formatMoney(entry.total_profit, currency, { signed: true })}
          </span>
          <span className="block text-[0.7rem] text-ink-faint">
            {entry.roi_pct === null ? 'not scored' : `${formatNumber(entry.roi_pct)}% return`}
          </span>
        </span>

        <Badge tone={entry.status === 'returned' ? 'positive' : 'outline'}>
          {humanise(entry.status)}
        </Badge>
      </div>

      {entry.status_note ? (
        <p className="px-3 py-2.5 text-xs leading-relaxed text-ink-muted">{entry.status_note}</p>
      ) : null}

      {entry.cards.length ? (
        <div className="divide-y divide-line">
          {entry.cards.map((card) => (
            <div
              key={`${card.card_id}-${card.actual_grade}`}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2"
            >
              <Link
                to={`/cards/${card.card_id}`}
                className="min-w-0 flex-1 truncate text-sm text-ink hover:underline"
              >
                {card.name}
              </Link>

              <span className="tabular shrink-0 text-xs text-ink-muted">
                predicted {card.predicted_grade === null ? '—' : card.predicted_grade}
                {' → '}
                <span className="font-semibold text-ink">
                  got {card.actual_grade === null ? '—' : card.actual_grade}
                </span>
              </span>

              <span className="tabular w-20 shrink-0 text-right text-xs text-ink-muted">
                {formatMoney(card.net_if_sold, currency)}
              </span>

              <span
                className={cn(
                  'tabular w-20 shrink-0 text-right text-sm font-medium',
                  card.profit === null
                    ? 'text-ink-faint'
                    : card.profit >= 0
                      ? 'text-positive'
                      : 'text-negative',
                )}
              >
                {formatMoney(card.profit, currency, { signed: true })}
              </span>

              {card.blockers.length ? (
                <p className="w-full text-[0.7rem] leading-relaxed text-caution">
                  {card.blockers.join(' ')}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/* --- Cuts ----------------------------------------------------------------- */

function FiltersTab() {
  const [active, setActive] = useState('grade_now')
  const filters = useQuery({ queryKey: keys.collectionFilters, queryFn: api.collectionFilters })
  const result = useQuery({
    queryKey: keys.filterResult(active, BATCH),
    queryFn: () => api.applyFilter(active, BATCH),
    enabled: Boolean(filters.data),
  })

  const definition = filters.data?.find((item) => item.key === active)

  return (
    <div className="space-y-4">
      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle className="flex items-center gap-2">
              <FilterIcon className="size-4" />
              One question at a time
            </PanelTitle>
            <PanelDescription>
              Each cut is a predicate over figures the engines already produced — never a second
              definition of the same idea.
            </PanelDescription>
          </div>
        </PanelHeader>
        <PanelBody>
          {filters.isError ? <ErrorState error={filters.error} /> : null}
          <div className="flex flex-wrap gap-2">
            {filters.data?.map((item) => (
              <Button
                key={item.key}
                size="sm"
                variant={active === item.key ? 'primary' : 'secondary'}
                onClick={() => setActive(item.key)}
                title={item.description}
              >
                {item.label}
              </Button>
            ))}
          </div>
          {definition ? (
            <p className="pt-3 text-xs leading-relaxed text-ink-muted">{definition.description}</p>
          ) : null}
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>{definition?.label ?? 'Results'}</PanelTitle>
            <PanelDescription>
              {result.data ? summariseFilter(result.data) : 'Applying the cut…'}
            </PanelDescription>
          </div>
          {result.data ? <StatusBadge status={result.data.status} /> : null}
        </PanelHeader>
        <PanelBody>
          {result.isError ? <ErrorState error={result.error} /> : null}
          {result.isLoading ? <RowSkeletons /> : null}

          {result.data && !result.data.items.length ? (
            <EmptyState
              icon={<TrendingDown className="size-8" />}
              title="No card falls in this cut"
              description={
                result.data.reason ??
                'Nothing in the analysed collection matches. That is an answer, not an error.'
              }
            />
          ) : null}

          {result.data?.items.length ? (
            <div className="space-y-1.5">
              {result.data.items.map((row) => (
                <FilterRow key={row.card_id} row={row} currency={result.data.currency} />
              ))}
              <ThinFootnote items={result.data.items} />
            </div>
          ) : null}
        </PanelBody>
      </Panel>
    </div>
  )
}

function FilterRow({ row, currency }: { row: Opportunity; currency: string }) {
  const thin = row.expected_profit !== null && row.coverage < THIN_COVERAGE
  return (
    <Link
      to={`/cards/${row.card_id}`}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line px-3 py-2.5 transition-colors hover:bg-canvas"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink">{row.name}</span>
        <span className="block truncate text-xs text-ink-faint">{row.headline}</span>
      </span>

      {/* The two readings the market-shaped cuts are defined against, shown so
          a row can be checked against the filter that selected it. */}
      <span className="tabular w-14 shrink-0 text-right text-xs text-ink-muted">
        {row.liquidity_score === null ? '—' : `${row.liquidity_score.toFixed(1)}/10`}
      </span>
      <span className="w-20 shrink-0 text-right text-xs text-ink-muted">
        {row.trend_direction ? humanise(row.trend_direction) : '—'}
      </span>

      <span className="tabular shrink-0 text-right text-sm font-semibold text-ink">
        {formatMoney(row.expected_profit, currency, { signed: true })}
        {thin ? '*' : ''}
      </span>

      <DecisionBadge decision={row.decision} />
    </Link>
  )
}

/* --- Shared bits ---------------------------------------------------------- */

function Figure({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'positive' | 'negative'
}) {
  return (
    <div className="rounded-lg border border-line px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-[0.7rem] uppercase tracking-wide text-ink-faint">
        <Coins className="size-3" />
        {label}
      </p>
      <p
        className={cn(
          'tabular pt-0.5 text-lg font-semibold',
          tone === 'positive' ? 'text-positive' : tone === 'negative' ? 'text-negative' : 'text-ink',
        )}
      >
        {value}
      </p>
    </div>
  )
}

function RowSkeletons() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  )
}

/** One asterisk, one explanation — wherever conditional profits are listed. */
function ThinFootnote({ items }: { items: Opportunity[] }) {
  const thin = items.some((row) => row.expected_profit !== null && row.coverage < THIN_COVERAGE)
  if (!thin) return null
  return (
    <p className="pt-1 text-[0.7rem] leading-relaxed text-ink-faint">
      * Profit is conditional: not every grade this card might get has ever sold, so the figure is
      what you would make if it lands on one that has.
    </p>
  )
}

/* --- Summaries: always the total *and* its denominator --------------------- */

function summariseRanked(data: RankedOpportunities, batch: number): string {
  if (!data.analysed) return data.reason ?? 'No card can be decided yet.'
  const scope = `${formatNumber(data.actionable)} of ${formatNumber(data.analysed)} analysed card(s) are worth grading ${
    batch === 1 ? 'on their own' : `in a submission of ${batch}`
  }.`
  if (!data.actionable) return scope
  return `${scope} ${formatMoney(data.total_grading_cost, data.currency)} of grading to return ${formatMoney(
    data.expected_profit,
    data.currency,
    { signed: true },
  )}.`
}

function summariseQueue(data: SellingQueue): string {
  if (!data.items.length) return data.reason ?? 'Nothing is better off sold raw right now.'
  return `${formatNumber(data.items.length)} card(s) to list, netting ${formatMoney(
    data.total_net_proceeds,
    data.currency,
  )} after fees and postage.`
}

function summariseReturns(data: SubmissionReturns): string {
  if (!data.submissions.length) return 'No submissions yet, so there is nothing to score.'
  if (!data.scored) return data.reason ?? 'Nothing has come back yet.'
  const scope = `Scored ${formatNumber(data.scored)} returned submission(s)`
  return data.awaiting
    ? `${scope}; ${formatNumber(data.awaiting)} still out and counted in no total.`
    : `${scope}.`
}

function summariseFilter(data: FilterResult): string {
  const scope = `${formatNumber(data.matched)} of ${formatNumber(data.analysed)} analysed card(s).`
  // Never a match count without what it could not see.
  return data.unclassified
    ? `${scope} ${formatNumber(data.unclassified)} could not be decided, so were not tested.`
    : scope
}
