import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, ChevronDown, Info, Scale, Target, UserCheck } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { CardEvaluation, Decision, ExpectedOutcome, RecommendationBlock } from '@/lib/types'
import { ConfidenceBadge, DecisionBadge, StatusBadge } from '@/components/DecisionBadge'
import { ExplanationList } from '@/components/ExplanationList'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/field'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { cn, formatMoney, formatNumber } from '@/lib/utils'

/**
 * Below this share of priced outcomes the engine itself reports the expected
 * outcomes as `partial`, so the UI uses the same line rather than inventing a
 * second opinion about what counts as thin.
 */
const THIN_COVERAGE = 0.8

/** The five things the score weighs, in the order the engine reports them. */
const SCORE_LABELS: Record<string, string> = {
  profitability: 'Profitability',
  grade_probability: 'Grade odds',
  liquidity: 'Liquidity',
  trend: 'Trend',
  risk: 'Risk',
}

const OVERRIDE_CHOICES: { value: Decision; label: string }[] = [
  { value: 'grade', label: 'Grade' },
  { value: 'sell_raw', label: 'Sell raw' },
  { value: 'keep_raw', label: 'Keep raw' },
  { value: 'hold', label: 'Hold' },
  { value: 'do_not_grade', label: 'Do not grade' },
]

/**
 * The verdict, and enough of its working to argue with.
 *
 * Every figure here is a profit *over selling the card raw today* — grading has
 * to beat doing nothing, not beat zero. The panel leads with the decision but
 * gives equal room to what would change it: the bar it cleared or missed, the
 * grades that have no price behind them, and the route that lost.
 */
export function Recommendation({
  cardId,
  evaluation,
  onOverridden,
}: {
  cardId: string
  evaluation: CardEvaluation
  onOverridden: () => void
}) {
  const [editingOverride, setEditingOverride] = useState(false)
  const block = evaluation.recommendation
  const currency = evaluation.currency
  const leader = evaluation.expected_outcomes.outcomes[0] ?? null
  // An expectation computed over some of the outcomes is a conditional one, and
  // below the engine's own "thin" line that changes how the figure should be
  // read. Above it the exact coverage still shows as a chip — but shouting
  // about a 3% gap would spend the warning where it is not needed and leave
  // nothing behind it when coverage really is 13%.
  const partiallyPriced = block.expected_profit !== null && block.coverage < THIN_COVERAGE

  return (
    <>
      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>Recommendation</PanelTitle>
            <PanelDescription>{block.headline}</PanelDescription>
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {block.is_user_override ? <Badge tone="brand">Your call</Badge> : null}
            <DecisionBadge decision={block.decision} />
          </div>
        </PanelHeader>

        <PanelBody className="space-y-5">
          {block.is_user_override ? (
            <p className="flex items-start gap-2 rounded-lg border border-brand/30 bg-brand/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
              <UserCheck className="mt-0.5 size-3.5 shrink-0 text-brand" />
              You set this decision yourself, so the engine is explaining rather than deciding.
            </p>
          ) : null}

          <ScoreHeadline block={block} currency={currency} />

          {partiallyPriced ? (
            <p className="flex items-start gap-2 rounded-lg border border-caution/30 bg-caution/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
              <Scale className="mt-0.5 size-3.5 shrink-0 text-caution" />
              Only {asPercent(block.coverage)} of the grades this card might get have ever sold
              graded. The profit and ROI below are what you would make{' '}
              <em className="not-italic font-medium text-ink">if</em> it lands on one of those —
              not what to expect overall. P(profit) counts the rest against it.
            </p>
          ) : null}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric
              label={partiallyPriced ? 'Profit if priced' : 'Expected profit'}
              value={formatMoney(block.expected_profit, currency, { signed: true })}
              hint={partiallyPriced ? `over raw, ${asPercent(block.coverage)} of cases` : 'over selling raw'}
            />
            <Metric
              label="ROI"
              value={block.roi_pct === null ? '—' : `${formatNumber(block.roi_pct)}%`}
              hint="on the fee"
            />
            <Metric label="P(profit)" value={asPercent(block.probability_of_profit)} />
            <Metric
              label="Min. grade"
              value={
                block.minimum_profitable_grade === null
                  ? '—'
                  : formatNumber(block.minimum_profitable_grade, 1)
              }
              hint="to break even"
            />
          </div>

          <Comparison block={block} currency={currency} partiallyPriced={partiallyPriced} />

          {Object.keys(block.score_parts).length ? (
            <ScoreBreakdown parts={block.score_parts} />
          ) : null}

          {Object.keys(block.probability_of_target_profit).length ? (
            <ProfitLadder targets={block.probability_of_target_profit} currency={currency} />
          ) : null}

          {block.review_in_days !== null ? (
            <p className="flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-xs text-ink-muted">
              <CalendarClock className="size-3.5 shrink-0 text-ink-faint" />
              Worth another look in {block.review_in_days} days — this verdict rests on today's
              prices.
            </p>
          ) : null}

          {block.alternative ? (
            <RunnerUp
              outcome={block.alternative}
              note={block.alternative_note}
              currency={currency}
            />
          ) : null}

          {evaluation.blockers.length ? (
            <div className="rounded-lg border border-line bg-canvas px-4 py-3">
              <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">
                What would change this
              </p>
              <ul className="mt-2 space-y-1 text-sm text-ink-muted">
                {evaluation.blockers.map((blocker) => (
                  <li key={blocker}>• {blocker}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div>
            <p className="mb-2.5 text-xs font-medium uppercase tracking-wider text-ink-faint">
              Why?
            </p>
            <ExplanationList items={block.reasons.length ? block.reasons : evaluation.explanation} />
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-line pt-4">
            <p className="text-[0.7rem] leading-relaxed text-ink-faint">
              The engine advises. You decide — and your decision is what the collection totals
              count.
            </p>
            <Button size="sm" variant="ghost" onClick={() => setEditingOverride(true)}>
              {block.is_user_override ? 'Change yours' : 'Overrule'}
            </Button>
          </div>
        </PanelBody>
      </Panel>

      {leader ? (
        // The routes listed here are costed at the batch the *page* is showing,
        // which is not always the one the recommendation quotes — "grade it in a
        // submission of 25" prices a batch you have not asked for yet.
        <ExpectedOutcomes
          block={evaluation.expected_outcomes}
          currency={currency}
          assumedBatchSize={evaluation.grading_options.assumed_batch_size}
        />
      ) : null}

      <Dialog open={editingOverride} onOpenChange={setEditingOverride}>
        <DialogContent
          title="Your decision"
          description="Overrides the engine everywhere, including the collection totals. Clear it to hand the card back."
        >
          <OverrideForm
            cardId={cardId}
            current={block.is_user_override ? block.decision : null}
            onDone={() => {
              setEditingOverride(false)
              onOverridden()
            }}
            onCancel={() => setEditingOverride(false)}
          />
        </DialogContent>
      </Dialog>
    </>
  )
}

/** The score, and the band the profit is expected to land in. */
function ScoreHeadline({
  block,
  currency,
}: {
  block: RecommendationBlock
  currency: string
}) {
  if (block.opportunity_score === null) return null
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 rounded-lg border border-line px-4 py-3">
      <div>
        <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">Opportunity score</p>
        <p className="tabular mt-0.5 text-2xl font-semibold text-brand">
          {formatNumber(block.opportunity_score)}
          <span className="ml-1 text-sm font-normal text-ink-faint">/ 100</span>
        </p>
        {block.company_code ? (
          <p className="mt-1 text-xs text-ink-muted">
            via {block.company_code}
            {block.tier_name ? ` ${block.tier_name}` : ''}
            {block.assumed_batch_size > 1 ? `, in a submission of ${block.assumed_batch_size}` : ''}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-4">
        {/* One priced grade makes the two ends of the band identical, and a
            band with no width reads as certainty — which is the opposite of
            what one priced grade out of six means. */}
        {block.downside !== null && block.downside === block.upside ? (
          <div className="text-right">
            <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">
              Single priced outcome
            </p>
            <p className="tabular mt-0.5 font-semibold text-ink">
              {formatMoney(block.downside, currency, { signed: true })}
            </p>
            <p className="text-[0.7rem] text-ink-faint">no range to give</p>
          </div>
        ) : (
          <>
            <div className="text-right">
              <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">Downside</p>
              <p className="tabular mt-0.5 font-semibold text-ink">
                {formatMoney(block.downside, currency, { signed: true })}
              </p>
            </div>
            <span className="text-ink-faint">–</span>
            <div>
              <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">Upside</p>
              <p className="tabular mt-0.5 font-semibold text-ink">
                {formatMoney(block.upside, currency, { signed: true })}
              </p>
            </div>
          </>
        )}
        <div className="flex flex-col items-end gap-1">
          <ConfidenceBadge confidence={block.confidence} />
          {block.coverage < 0.999 ? <CoverageBadge coverage={block.coverage} /> : null}
        </div>
      </div>
    </div>
  )
}

/** What grading has to beat, spelled out rather than implied. */
function Comparison({
  block,
  currency,
  partiallyPriced,
}: {
  block: RecommendationBlock
  currency: string
  partiallyPriced: boolean
}) {
  if (block.net_raw_alternative === null && block.expected_net === null) return null
  const qualifier = partiallyPriced ? ` (in the ${asPercent(block.coverage)} that are priced)` : ''
  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <Ledger
        label="Sell it raw today"
        value={formatMoney(block.net_raw_alternative, currency)}
        hint="net of fees and postage"
      />
      <Ledger
        label={`Grade it${block.company_code ? ` with ${block.company_code}` : ''}`}
        value={formatMoney(block.expected_net, currency)}
        hint={
          block.grading_cost === null
            ? `expected net${qualifier}`
            : `after ${formatMoney(block.grading_cost, currency)} of fees${qualifier}`
        }
      />
      <Ledger
        label="Difference"
        value={formatMoney(block.expected_profit, currency, { signed: true })}
        hint={partiallyPriced ? 'only where a price exists' : 'what the decision is worth'}
        emphasis
      />
    </div>
  )
}

function Ledger({
  label,
  value,
  hint,
  emphasis,
}: {
  label: string
  value: string
  hint: string
  emphasis?: boolean
}) {
  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2.5',
        emphasis ? 'border-brand/30 bg-brand/5' : 'border-line',
      )}
    >
      <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className={cn('tabular mt-0.5 font-semibold', emphasis ? 'text-brand' : 'text-ink')}>
        {value}
      </p>
      <p className="mt-0.5 text-[0.7rem] leading-tight text-ink-faint">{hint}</p>
    </div>
  )
}

/**
 * The score is weighted by settings the user controls, so showing the total
 * without its parts would make it unarguable.
 */
function ScoreBreakdown({ parts }: { parts: Record<string, number> }) {
  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-faint">
        What made the score
      </p>
      <div className="space-y-1.5">
        {Object.entries(parts).map(([key, value]) => (
          <div key={key} className="flex items-center gap-3">
            <span className="w-24 shrink-0 text-xs text-ink-muted">
              {SCORE_LABELS[key] ?? key.replace(/_/g, ' ')}
            </span>
            <div
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-raised"
              role="presentation"
            >
              <div
                className="h-full rounded-full bg-brand"
                style={{ width: `${Math.max(0, Math.min(10, value)) * 10}%` }}
              />
            </div>
            <span className="tabular w-10 shrink-0 text-right text-xs text-ink-muted">
              {formatNumber(value, 1)}
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[0.7rem] leading-relaxed text-ink-faint">
        Each out of 10, weighted by your settings. Change the weights to change what the engine
        cares about.
      </p>
    </div>
  )
}

/** How likely each round-number profit is — a range beats a single expectation. */
function ProfitLadder({
  targets,
  currency,
}: {
  targets: Record<string, number>
  currency: string
}) {
  const entries = Object.entries(targets).sort(
    ([a], [b]) => Number(a) - Number(b),
  )
  if (!entries.length) return null
  return (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-ink-faint">
        <Target className="size-3.5" /> Chance of clearing
      </p>
      <div className="flex flex-wrap gap-2">
        {entries.map(([target, probability]) => (
          <div key={target} className="rounded-lg border border-line px-3 py-1.5">
            <span className="tabular text-xs text-ink-muted">
              {formatMoney(Number(target), currency)}
            </span>
            <span className="tabular ml-2 text-sm font-semibold text-ink">
              {asPercent(probability)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** The route with better headline economics that lost, and the reason it lost. */
function RunnerUp({
  outcome,
  note,
  currency,
}: {
  outcome: ExpectedOutcome
  note: string | null
  currency: string
}) {
  return (
    <div className="rounded-lg border border-line bg-canvas px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">
          Considered and rejected
        </p>
        <p className="text-sm text-ink">
          {outcome.company_code}
          {outcome.tier_name ? ` ${outcome.tier_name}` : ''}
          <span className="tabular ml-2 text-ink-muted">
            {formatMoney(outcome.expected_profit, currency, { signed: true })}
          </span>
        </p>
      </div>
      {note ? <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">{note}</p> : null}
    </div>
  )
}

/**
 * Every route the engine costed, and the per-grade table behind each one.
 *
 * Coverage is the number to read first: an expectation computed over 27% of the
 * outcomes is a conditional expectation, and saying so is the difference
 * between a useful figure and a misleading one.
 */
export function ExpectedOutcomes({
  block,
  currency,
  assumedBatchSize,
}: {
  block: CardEvaluation['expected_outcomes']
  currency: string
  assumedBatchSize: number
}) {
  const [expanded, setExpanded] = useState<string | null>(
    block.outcomes.length ? routeKey(block.outcomes[0]) : null,
  )

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Expected outcomes</PanelTitle>
          <PanelDescription>
            {block.reason ??
              `Every route, weighted by how likely each grade is${
                assumedBatchSize > 1 ? `, in a submission of ${assumedBatchSize}` : ''
              }.`}
          </PanelDescription>
        </div>
        <StatusBadge status={block.status} phase={block.phase} />
      </PanelHeader>

      {block.outcomes.length ? (
        <PanelBody className="space-y-2">
          {block.outcomes.map((outcome) => {
            const key = routeKey(outcome)
            const open = expanded === key
            return (
              <div key={key} className="rounded-lg border border-line">
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : key)}
                  aria-expanded={open}
                  className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 text-left hover:bg-canvas"
                >
                  <span className="min-w-0 flex-1">
                    <span className="text-sm font-medium text-ink">
                      {outcome.company_code}
                      {outcome.tier_name ? ` ${outcome.tier_name}` : ''}
                    </span>
                    <span className="tabular ml-2 text-xs text-ink-faint">
                      {formatMoney(outcome.grading_cost, currency)} to grade
                    </span>
                  </span>
                  <span className="tabular shrink-0 text-sm font-semibold text-ink">
                    {formatMoney(outcome.expected_profit, currency, { signed: true })}
                  </span>
                  <span className="tabular w-12 shrink-0 text-right text-xs text-ink-muted">
                    {asPercent(outcome.probability_of_profit)}
                  </span>
                  <CoverageBadge coverage={outcome.coverage} />
                  <ChevronDown
                    className={cn(
                      'size-4 shrink-0 text-ink-faint transition-transform',
                      open && 'rotate-180',
                    )}
                  />
                </button>

                {open ? (
                  <div className="space-y-3 border-t border-line px-3 py-3">
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Metric label="ROI" value={
                        outcome.roi_pct === null ? '—' : `${formatNumber(outcome.roi_pct)}%`
                      } />
                      <Metric
                        label="Downside"
                        value={formatMoney(outcome.downside, currency, { signed: true })}
                      />
                      <Metric
                        label="Upside"
                        value={formatMoney(outcome.upside, currency, { signed: true })}
                      />
                      <Metric
                        label="Slab liquidity"
                        value={
                          outcome.liquidity_score === null
                            ? '—'
                            : `${formatNumber(outcome.liquidity_score, 1)}/10`
                        }
                      />
                    </div>

                    <OutcomeTable outcome={outcome} currency={currency} />

                    <div className="flex flex-wrap items-center gap-2">
                      <ConfidenceBadge confidence={outcome.confidence} />
                      {outcome.minimum_profitable_grade !== null ? (
                        <Badge tone="outline">
                          Profitable from {formatNumber(outcome.minimum_profitable_grade, 1)} up
                          {outcome.probability_at_or_above_minimum !== null
                            ? ` · ${asPercent(outcome.probability_at_or_above_minimum)} likely`
                            : ''}
                        </Badge>
                      ) : null}
                    </div>

                    {outcome.notes.map((note) => (
                      <p
                        key={note}
                        className="flex items-start gap-2 text-[0.7rem] leading-relaxed text-ink-faint"
                      >
                        <Info className="mt-0.5 size-3.5 shrink-0" />
                        {note}
                      </p>
                    ))}
                  </div>
                ) : null}
              </div>
            )
          })}
        </PanelBody>
      ) : null}
    </Panel>
  )
}

/**
 * The per-grade working. Unpriced grades stay in the table with a dash rather
 * than being dropped: a grade nobody has sold is still a grade you might get.
 */
function OutcomeTable({ outcome, currency }: { outcome: ExpectedOutcome; currency: string }) {
  if (!outcome.rows.length) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[22rem] text-xs">
        <thead>
          <tr className="text-left text-ink-faint">
            <th className="pb-1.5 font-medium">Grade</th>
            <th className="pb-1.5 text-right font-medium">Likelihood</th>
            <th className="pb-1.5 text-right font-medium">Sells for</th>
            <th className="pb-1.5 text-right font-medium">You keep</th>
            <th className="pb-1.5 text-right font-medium">Profit</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {outcome.rows.map((row) => (
            <tr key={row.label} className={row.net_value === null ? 'text-ink-faint' : 'text-ink'}>
              <td className="py-1.5">{row.label}</td>
              <td className="tabular py-1.5 text-right">{asPercent(row.probability)}</td>
              <td className="tabular py-1.5 text-right">
                {formatMoney(row.gross_value, currency)}
              </td>
              <td className="tabular py-1.5 text-right">{formatMoney(row.net_value, currency)}</td>
              <td
                className={cn(
                  'tabular py-1.5 text-right font-medium',
                  row.profit === null
                    ? 'text-ink-faint'
                    : row.profit > 0
                      ? 'text-positive'
                      : 'text-negative',
                )}
              >
                {formatMoney(row.profit, currency, { signed: true })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1.5 text-[0.7rem] leading-relaxed text-ink-faint">
        Profit is measured against selling the card raw today, not against zero.
      </p>
    </div>
  )
}

/** Coverage is the honesty dial on every expectation, so it gets its own chip. */
function CoverageBadge({ coverage }: { coverage: number }) {
  if (coverage >= 0.999) return <Badge tone="positive">fully priced</Badge>
  const tone = coverage >= 0.8 ? 'caution' : 'negative'
  return (
    <Badge tone={tone} title="Share of the likely grades that have sales behind them">
      <Scale className="size-3" />
      {asPercent(coverage)} priced
    </Badge>
  )
}

function OverrideForm({
  cardId,
  current,
  onDone,
  onCancel,
}: {
  cardId: string
  current: Decision | null
  onDone: () => void
  onCancel: () => void
}) {
  const queryClient = useQueryClient()
  const [choice, setChoice] = useState<Decision | ''>(current ?? '')
  const [reason, setReason] = useState('')

  const save = useMutation({
    mutationFn: () =>
      api.updateCard(cardId, {
        decision_override: choice === '' ? null : choice,
        decision_override_reason: choice === '' ? null : reason.trim() || null,
      }),
    onSuccess: () => {
      toast.success(choice === '' ? 'Handed back to the engine' : 'Your decision saved')
      queryClient.invalidateQueries({ queryKey: keys.card(cardId) })
      queryClient.invalidateQueries({ queryKey: ['evaluation', cardId] })
      queryClient.invalidateQueries({ queryKey: ['collection-decisions'] })
      queryClient.invalidateQueries({ queryKey: keys.summary })
      onDone()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save your decision'),
  })

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <fieldset className="space-y-2">
        <legend className="mb-2 text-sm font-medium text-ink">Your decision</legend>
        <div className="flex flex-wrap gap-2">
          {OVERRIDE_CHOICES.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setChoice(option.value)}
              aria-pressed={choice === option.value}
              className={cn(
                'rounded-md border px-3 py-1.5 text-sm transition-colors',
                choice === option.value
                  ? 'border-brand bg-brand text-white'
                  : 'border-line text-ink-muted hover:bg-surface-raised hover:text-ink',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      </fieldset>

      <Field label="Why?" hint="Optional, but the you of six months from now will want it.">
        <Input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Waiting for the set to finish printing"
        />
      </Field>

      <div className="flex items-center justify-between gap-3 pt-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            setChoice('')
            save.mutate()
          }}
          disabled={save.isPending || current === null}
        >
          Hand it back to the engine
        </Button>
        <div className="flex gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" disabled={save.isPending || choice === ''}>
            Save
          </Button>
        </div>
      </div>
    </form>
  )
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className="tabular mt-0.5 text-base font-semibold text-ink">{value}</p>
      {hint ? <p className="text-[0.7rem] leading-tight text-ink-faint">{hint}</p> : null}
    </div>
  )
}

function routeKey(outcome: ExpectedOutcome): string {
  return `${outcome.company_code}:${outcome.tier_name ?? ''}`
}

/** Null stays an em dash: an unknown probability is not a zero one. */
function asPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}
