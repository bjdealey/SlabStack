import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ArrowRight, Boxes, Camera, ClipboardCheck, Coins, LineChart } from 'lucide-react'
import { api, keys } from '@/lib/api'
import type { CollectionDecisions, Decision } from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { StatTile } from '@/components/StatTile'
import { Opportunities } from '@/components/Opportunities'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/ui/states'
import { formatMoney, formatNumber } from '@/lib/utils'

/** The batch the collection sweep costs against. Grading in bulk is the norm. */
const COLLECTION_BATCH = 20

const DECISION_ROWS: readonly (readonly [
  Decision,
  string,
  'positive' | 'brand' | 'caution' | 'neutral' | 'negative' | 'outline',
])[] = [
  ['grade', 'Grade', 'positive'],
  ['grade_if_batch_filled', 'Grade in a batch', 'caution'],
  ['sell_raw', 'Sell raw', 'brand'],
  ['hold', 'Hold', 'caution'],
  ['keep_raw', 'Keep raw', 'neutral'],
  ['do_not_grade', 'Do not grade', 'negative'],
  ['insufficient_data', 'Not yet analysable', 'outline'],
]

export function Dashboard() {
  const summary = useQuery({ queryKey: keys.summary, queryFn: api.summary })
  // Its own query on purpose: this runs the decision engine over every ready
  // card, so the dashboard paints first and the verdicts arrive after.
  const decisions = useQuery({
    queryKey: keys.collectionDecisions(COLLECTION_BATCH),
    queryFn: () => api.collectionDecisions(COLLECTION_BATCH),
    enabled: (summary.data?.totals.cards ?? 0) > 0,
  })

  if (summary.isLoading) return <LoadingPanel label="Loading your collection…" />
  if (summary.isError) {
    return (
      <div className="p-6">
        <ErrorState error={summary.error} />
      </div>
    )
  }

  const data = summary.data!
  const { totals, values, readiness } = data
  const currency = values.currency
  const empty = totals.cards === 0
  const verdicts = decisions.data

  return (
    <>
      <PageHeader
        title="Collection"
        description="What you own, and how ready it is to be analysed."
        actions={
          <Button asChild variant="primary">
            <Link to="/collection?new=1">Add a card</Link>
          </Button>
        }
      />

      <div className="space-y-6 p-6">
        {empty ? (
          <Panel>
            <EmptyState
              icon={<Boxes className="size-8" />}
              title="No cards yet"
              description="Add your first card to start building the collection. Grading recommendations need a condition assessment and comparable sales — the dashboard will tell you what is still missing as you go."
              action={
                <Button asChild variant="primary">
                  <Link to="/collection?new=1">Add your first card</Link>
                </Button>
              }
            />
          </Panel>
        ) : null}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            label="Cards"
            value={formatNumber(totals.cards)}
            hint={`${formatNumber(totals.copies)} copies across ${formatNumber(totals.distinct_sets)} sets`}
            icon={<Boxes className="size-4" />}
          />
          <StatTile
            label="Known raw value"
            value={formatMoney(values.known_raw_value, currency, { compact: true })}
            hint={`Best value for ${values.cards_with_value} of ${totals.cards} cards — yours, the market's, or what you paid`}
            icon={<Coins className="size-4" />}
          />
          <StatTile
            label="Comparable sales"
            value={formatNumber(data.market_sales_stored)}
            hint={
              data.market_sales_stored
                ? 'Sales counted toward your valuations'
                : 'Add sales to value the collection'
            }
            icon={<LineChart className="size-4" />}
          />
          <ProfitTile
            loading={decisions.isLoading}
            decisions={verdicts}
            currency={currency}
            totalCards={totals.cards}
          />
        </section>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel>
            <PanelHeader>
              <div>
                <PanelTitle>Analysis readiness</PanelTitle>
                <PanelDescription>
                  A grading recommendation needs all four. Nothing here is guessed on your behalf.
                </PanelDescription>
              </div>
            </PanelHeader>
            <PanelBody className="space-y-4">
              {readiness.map((item) => {
                const pct = item.total ? Math.round((item.count / item.total) * 100) : 0
                return (
                  <div key={item.key}>
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <span className="text-ink">{item.label}</span>
                      <span className="tabular text-xs text-ink-faint">
                        {formatNumber(item.count)} / {formatNumber(item.total)}
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-raised">
                      <div
                        className="h-full rounded-full bg-brand transition-[width]"
                        style={{ width: `${Math.min(pct, 100)}%` }}
                      />
                    </div>
                    {item.count < item.total ? (
                      <p className="mt-1 text-xs text-ink-faint">{item.action}</p>
                    ) : null}
                  </div>
                )
              })}
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader>
              <div>
                <PanelTitle>Decisions</PanelTitle>
                <PanelDescription>
                  {verdicts
                    ? `The engine's verdict on ${formatNumber(verdicts.analysed)} of your ${formatNumber(verdicts.total_cards)} cards, in a submission of ${verdicts.batch_size}.`
                    : data.decisions.reason}
                </PanelDescription>
              </div>
            </PanelHeader>
            <PanelBody className="space-y-2.5 text-sm">
              {DECISION_ROWS.map(([key, label, tone]) => {
                // Cards with neither an assessment nor sales are not "no" — they
                // are unanswered, and the engine never saw them.
                const count = verdicts
                  ? key === 'insufficient_data'
                    ? (verdicts.counts.insufficient_data ?? 0) + verdicts.skipped_not_ready
                    : (verdicts.counts[key] ?? 0)
                  : (data.decisions[key] ?? 0)
                return (
                  <div key={key} className="flex items-center justify-between gap-3">
                    <Badge tone={tone}>{label}</Badge>
                    <span className="tabular text-ink">
                      {decisions.isLoading ? '·' : formatNumber(count)}
                    </span>
                  </div>
                )
              })}
              {verdicts?.truncated ? (
                <p className="border-t border-line pt-3 text-xs text-ink-faint">{verdicts.reason}</p>
              ) : null}
              {data.review_due > 0 ? (
                <p className="border-t border-line pt-3 text-xs text-caution">
                  {data.review_due} card(s) on hold are due for a recheck.
                </p>
              ) : null}
            </PanelBody>
          </Panel>
        </div>

        {!empty ? (
          <Opportunities
            decisions={verdicts}
            loading={decisions.isLoading}
            error={decisions.error}
          />
        ) : null}

        {data.by_set.length ? (
          <Panel>
            <PanelHeader>
              <div>
                <PanelTitle>Where the collection sits</PanelTitle>
                <PanelDescription>Cards per set, largest first.</PanelDescription>
              </div>
            </PanelHeader>
            <PanelBody>
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.by_set} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                    <XAxis
                      dataKey="set"
                      tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
                      tickLine={false}
                      axisLine={{ stroke: 'var(--color-line)' }}
                      interval={0}
                      angle={-20}
                      textAnchor="end"
                      height={60}
                    />
                    <YAxis
                      tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
                      tickLine={false}
                      axisLine={false}
                      allowDecimals={false}
                    />
                    <Tooltip
                      cursor={{ fill: 'var(--color-surface-raised)' }}
                      contentStyle={{
                        background: 'var(--color-surface-raised)',
                        border: '1px solid var(--color-line)',
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                    <Bar dataKey="cards" radius={[4, 4, 0, 0]}>
                      {data.by_set.map((entry) => (
                        <Cell key={entry.set} fill="var(--color-brand)" />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </PanelBody>
          </Panel>
        ) : null}

        <div className="grid gap-6 md:grid-cols-3">
          <NextStep
            icon={<Camera className="size-4" />}
            title="Photograph your cards"
            body={`${totals.with_images} of ${totals.cards} cards have images. Front and back photos are what make a condition assessment checkable later.`}
            to="/collection?has_images=false"
            cta="Find unphotographed cards"
            disabled={empty}
          />
          <NextStep
            icon={<ClipboardCheck className="size-4" />}
            title="Assess condition"
            body={`${totals.with_condition} of ${totals.cards} cards are assessed. Centering and defects drive every grade probability.`}
            to="/collection?has_condition=false"
            cta="Find unassessed cards"
            disabled={empty}
          />
          <NextStep
            icon={<Coins className="size-4" />}
            title="Configure grading prices"
            body={`${data.priced_tiers_configured} priced tier(s) configured. Grader pricing changes often — the engine only uses what you have entered.`}
            to="/settings"
            cta="Open grading settings"
          />
        </div>
      </div>
    </>
  )
}

/**
 * The one number the whole app exists to produce — so it never appears without
 * the count of cards it was summed over. "£2,140" across three cards and across
 * three hundred are different claims.
 */
function ProfitTile({
  loading,
  decisions,
  currency,
  totalCards,
}: {
  loading: boolean
  decisions: CollectionDecisions | undefined
  currency: string
  totalCards: number
}) {
  if (loading || !decisions) {
    return (
      <StatTile
        label="Expected profit"
        value={loading ? 'Analysing…' : 'Not calculated'}
        pending
        hint={
          loading
            ? 'Running the decision engine across your collection'
            : 'Assess a card and add its comparable sales'
        }
        icon={<ArrowRight className="size-4" />}
      />
    )
  }

  const graded =
    (decisions.counts.grade ?? 0) + (decisions.counts.grade_if_batch_filled ?? 0)

  return (
    <StatTile
      label="Expected profit"
      value={
        decisions.expected_profit === null
          ? 'Nothing to grade'
          : formatMoney(decisions.expected_profit, currency, { compact: true, signed: true })
      }
      tone={decisions.expected_profit ? 'positive' : 'neutral'}
      pending={decisions.expected_profit === null}
      hint={
        decisions.expected_profit === null
          ? `Nothing in the ${formatNumber(decisions.analysed)} analysed card(s) clears your bar`
          : `Over selling raw, from ${formatNumber(graded)} card(s) worth grading — analysed ${formatNumber(decisions.analysed)} of ${formatNumber(totalCards)}`
      }
      icon={<ArrowRight className="size-4" />}
    />
  )
}

function NextStep({
  icon,
  title,
  body,
  to,
  cta,
  disabled,
}: {
  icon: React.ReactNode
  title: string
  body: string
  to: string
  cta: string
  disabled?: boolean
}) {
  return (
    <Panel className="flex flex-col justify-between">
      <PanelBody className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-medium text-ink">
          <span className="text-brand">{icon}</span>
          {title}
        </div>
        <p className="text-xs leading-relaxed text-ink-faint">{body}</p>
      </PanelBody>
      <div className="px-5 pb-4">
        <Button asChild size="sm" variant="secondary" disabled={disabled}>
          <Link to={to}>
            {cta} <ArrowRight />
          </Link>
        </Button>
      </div>
    </Panel>
  )
}
