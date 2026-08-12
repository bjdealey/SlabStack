import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Coins,
  Filter as FilterIcon,
  Banknote,
  PackageCheck,
  ScanEye,
  Sparkles,
  Tag,
  TrendingDown,
} from 'lucide-react'
import { api, keys } from '@/lib/api'
import type {
  AssessmentCandidate,
  DisposalOutcome,
  FilterResult,
  Opportunity,
  RankedOpportunities,
  SellingCandidate,
  SellingQueue,
  SubmissionReturn,
  SubmissionReturns,
} from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { Accuracy } from '@/components/Accuracy'
import { DecisionBadge, StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { StatTile } from '@/components/StatTile'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn, formatDate, formatMoney, formatNumber, humanise } from '@/lib/utils'

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
        description="What to send, what to list, what came back, the cuts to find them, and how well the model has been calling it."
      />
      <div className="p-6">
        <Tabs defaultValue="opportunities" className="space-y-6">
          <TabsList>
            <TabsTrigger value="opportunities">What to grade</TabsTrigger>
            <TabsTrigger value="assess">What to assess</TabsTrigger>
            <TabsTrigger value="selling">What to sell</TabsTrigger>
            <TabsTrigger value="returns">What came back</TabsTrigger>
            <TabsTrigger value="realised">What you made</TabsTrigger>
            <TabsTrigger value="filters">Cuts</TabsTrigger>
            <TabsTrigger value="accuracy">How it's doing</TabsTrigger>
          </TabsList>

          <TabsContent value="opportunities">
            <OpportunitiesTab />
          </TabsContent>
          <TabsContent value="assess">
            <AssessTab />
          </TabsContent>
          <TabsContent value="selling">
            <SellingTab />
          </TabsContent>
          <TabsContent value="returns">
            <ReturnsTab />
          </TabsContent>
          <TabsContent value="realised">
            <RealisedTab />
          </TabsContent>
          <TabsContent value="filters">
            <FiltersTab />
          </TabsContent>
          <TabsContent value="accuracy">
            <Accuracy />
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}

/* --- What you actually made ----------------------------------------------- */

/**
 * The only figures in this application that are not projections.
 *
 * `prediction_results` has scored *grade* predictions since Phase 8, so the app
 * could report that it called a PSA 9 correctly while having no idea whether the
 * submission made money. This is the other half, and the panel's main job is to
 * keep the two kinds of number apart: proceeds are what arrived, profit is what
 * arrived less what it cost — and where a cost was never recorded there is no
 * profit to show, only a proceeds figure and a note saying what is missing.
 */
function RealisedTab() {
  const report = useQuery({ queryKey: keys.realised, queryFn: api.realised })
  const data = report.data

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>What you actually made</PanelTitle>
          <PanelDescription>
            {data
              ? `${formatNumber(data.sold)} sale(s) recorded, ${formatNumber(data.scored)} with every cost known.`
              : 'Realised proceeds and profit, against what the market said on the day.'}
          </PanelDescription>
        </div>
        {data ? <StatusBadge status={data.status} /> : null}
      </PanelHeader>

      <PanelBody className="space-y-3">
        {report.isError ? <ErrorState error={report.error} /> : null}
        {report.isLoading ? <RowSkeletons /> : null}

        {data && !data.items.length ? (
          <EmptyState
            icon={<Banknote className="size-8" />}
            title="Nothing sold yet"
            description={
              data.reason ??
              'Mark a card sold from its page and this starts scoring the decisions behind them.'
            }
          />
        ) : null}

        {data?.items.length ? (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <StatTile
                label="Net proceeds"
                value={formatMoney(data.total_net_proceeds, data.currency)}
                hint="Every recorded sale."
              />
              <StatTile
                label="Realised profit"
                value={
                  data.total_realised_profit === null
                    ? '—'
                    : formatMoney(data.total_realised_profit, data.currency)
                }
                hint={`Across the ${formatNumber(data.scored)} with every cost known.`}
              />
              <StatTile
                label="Grading gained"
                value={
                  data.total_grading_gain === null
                    ? '—'
                    : formatMoney(data.total_grading_gain, data.currency)
                }
                hint="Slabs against the raw price the day they sold."
              />
            </div>

            <div className="space-y-1.5">
              {data.items.map((row) => (
                <RealisedRow key={row.disposal_id} row={row} currency={data.currency} />
              ))}
            </div>

            {data.notes.map((note) => (
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

/** What the sale is measured against, when there is anything to measure it by. */
function comparison(row: DisposalOutcome, currency: string): string {
  const parts: string[] = []
  if (row.market_value_on_the_day !== null) {
    // The comparison is the *sale price* against the market, both gross. Saying
    // "netted" here would describe the payout, which is a different number and
    // would read as though the platform's fees were a selling mistake.
    const gap =
      row.vs_market_pct === null
        ? ''
        : row.vs_market_pct === 0
          ? ' — you sold right at it'
          : ` — you sold ${Math.abs(row.vs_market_pct)}% ${row.vs_market_pct > 0 ? 'above' : 'below'} it`
    parts.push(`Worth ${formatMoney(row.market_value_on_the_day, currency)} that day${gap}.`)
  }
  if (row.grading_gain !== null) {
    parts.push(
      `Grading gained ${formatMoney(row.grading_gain, currency)} over selling it raw that day.`,
    )
  }
  return parts.length
    ? parts.join(' ')
    : 'No price history for the day it sold, so there is nothing to compare it against.'
}

function RealisedRow({ row, currency }: { row: DisposalOutcome; currency: string }) {
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{row.name}</span>
        <span className="text-[0.7rem] text-ink-faint">
          {formatDate(row.sold_on)} · {row.grade_label}
        </span>
        <span className="tabular text-sm text-ink">
          {formatMoney(row.net_proceeds, currency)}
        </span>
        {/* Profit and proceeds are different claims, so an incomplete one shows
            no number at all rather than a number with a cost missing from it. */}
        {row.profit_is_complete ? (
          <Badge tone={(row.realised_profit ?? 0) >= 0 ? 'positive' : 'negative'}>
            {(row.realised_profit ?? 0) >= 0 ? '+' : ''}
            {formatMoney(row.realised_profit, currency)}
          </Badge>
        ) : (
          <Badge tone="caution">profit unknown</Badge>
        )}
      </div>
      <p className="pt-0.5 text-[0.7rem] leading-relaxed text-ink-faint">
        {row.reason ?? comparison(row, currency)}
      </p>
    </>
  )
  return row.card_id ? (
    <Link
      to={`/cards/${row.card_id}`}
      className="block rounded-lg border border-line px-3 py-2 transition-colors hover:border-brand/50"
    >
      {body}
    </Link>
  ) : (
    <div className="rounded-lg border border-line px-3 py-2">{body}</div>
  )
}

/* --- What to assess ------------------------------------------------------- */

const VERDICT_TONES: Record<string, 'positive' | 'neutral' | 'caution'> = {
  assess: 'positive',
  skip: 'neutral',
  unknown: 'caution',
}

/**
 * Which unassessed cards are worth five minutes.
 *
 * Importing four hundred cards takes a second; assessing four hundred does not,
 * and the decision engine cannot help choose — it needs an assessment before it
 * says anything at all. So this ranks on the one thing already known about every
 * card: what the market pays for it raw against what it pays for the same card
 * in a slab.
 *
 * The number is a **ceiling**, and the panel works hard not to let it read as a
 * forecast. It is the most grading could add if the card came back at the
 * best-priced grade — an upper bound a real assessment can only lower. Its
 * value is in the other direction: a card whose best case still loses money
 * cannot be worth grading in any condition, so it is settled without ever being
 * looked at.
 */
function AssessTab() {
  const [batch, setBatch] = useState(BATCH)
  const queue = useQuery({
    queryKey: keys.assessmentQueue(batch),
    queryFn: () => api.assessmentQueue(batch),
  })

  const worth = queue.data?.items.filter((item) => item.verdict === 'assess') ?? []
  const settled = queue.data?.items.filter((item) => item.verdict !== 'assess') ?? []

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Worth a proper look, best first</PanelTitle>
          <PanelDescription>
            {queue.data
              ? `${formatNumber(queue.data.worth_assessing)} of ${formatNumber(queue.data.analysed)} unassessed card(s) could clear your bar. ${formatNumber(queue.data.ruled_out)} cannot, whatever condition they are in.`
              : 'Ranked by the most grading could possibly add, so the assessments go where they pay.'}
          </PanelDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {/* Shipping belongs to the parcel, so a ceiling costed at one card is
              the honest worst case and a fuller batch raises every one of them. */}
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
          {queue.data ? <StatusBadge status={queue.data.status} /> : null}
        </div>
      </PanelHeader>

      <PanelBody className="space-y-3">
        {queue.isError ? <ErrorState error={queue.error} /> : null}
        {queue.isLoading ? <RowSkeletons /> : null}

        {queue.data && !queue.data.items.length ? (
          <EmptyState
            icon={<ScanEye className="size-8" />}
            title="Nothing waiting to be assessed"
            description={
              queue.data.reason ??
              'Every priced card has been assessed. Import more, or sync a source so unpriced cards can be ranked.'
            }
          />
        ) : null}

        {worth.length ? (
          <div className="space-y-1.5">
            {worth.map((row) => (
              <AssessRow key={row.card_id} row={row} currency={queue.data!.currency} />
            ))}
          </div>
        ) : null}

        {settled.length ? (
          <details className="rounded-lg border border-line">
            <summary className="cursor-pointer px-3 py-2 text-xs text-ink-muted">
              {formatNumber(settled.length)} card(s) you can leave alone — and why
            </summary>
            <div className="space-y-1.5 border-t border-line p-3">
              {settled.map((row) => (
                <AssessRow key={row.card_id} row={row} currency={queue.data!.currency} />
              ))}
            </div>
          </details>
        ) : null}

        {queue.data?.items.length ? (
          <p className="text-[0.7rem] leading-relaxed text-ink-faint">
            These are ceilings, not forecasts: the most grading could add if the card came back at
            the best-priced grade. A real assessment can only bring the number down — which is why
            a card that fails here is settled, and one that passes still has to be looked at.
          </p>
        ) : null}

        {queue.data?.notes.map((note) => (
          <p key={note} className="text-[0.7rem] leading-relaxed text-ink-faint">
            {note}
          </p>
        ))}
      </PanelBody>
    </Panel>
  )
}

function AssessRow({ row, currency }: { row: AssessmentCandidate; currency: string }) {
  return (
    <Link
      to={`/cards/${row.card_id}`}
      className="block rounded-lg border border-line px-3 py-2 transition-colors hover:border-brand/50"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{row.name}</span>
        {row.best_grade_label ? (
          <span className="text-[0.7rem] text-ink-faint">
            best priced: {row.best_grade_label}
            {row.ceiling_is_complete ? '' : ' (not the top grade)'}
          </span>
        ) : null}
        {row.ceiling !== null ? (
          <span
            className={cn(
              'tabular text-sm',
              row.verdict === 'assess' ? 'text-positive' : 'text-ink-faint',
            )}
          >
            {row.ceiling > 0 ? '+' : ''}
            {formatMoney(row.ceiling, currency)}
          </span>
        ) : null}
        {/* "no data" would be wrong for a card that has plenty — just none
            above the grade that was priced. Both unknowns are waiting on the
            same thing, and naming it points at the fix. */}
        <Badge tone={VERDICT_TONES[row.verdict] ?? 'neutral'}>
          {row.verdict === 'assess' ? 'assess' : row.verdict === 'skip' ? 'settled' : 'needs prices'}
        </Badge>
      </div>
      {row.reason ? (
        <p className="pt-0.5 text-[0.7rem] leading-relaxed text-ink-faint">{row.reason}</p>
      ) : null}
    </Link>
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

              {/* Money that arrived and a price that might are not the same
                  claim, and a column of figures that mixes them silently is the
                  quietest way to overstate a return. */}
              <span className="tabular w-24 shrink-0 text-right text-xs text-ink-muted">
                {formatMoney(card.net_if_sold, currency)}
                {card.value_basis === 'realised' ? (
                  <span className="block text-[0.6rem] text-positive">sold</span>
                ) : card.value_basis === 'market' ? (
                  <span className="block text-[0.6rem] text-ink-faint">at today's price</span>
                ) : null}
              </span>

              <span
                className={cn(
                  // Wide enough for a signed four-figure sum: at w-20 a good
                  // parcel wrapped its own profit onto two lines.
                  'tabular w-28 shrink-0 whitespace-nowrap text-right text-sm font-medium',
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
