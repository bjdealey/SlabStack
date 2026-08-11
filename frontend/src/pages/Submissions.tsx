import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  Info,
  PackageCheck,
  Plus,
  Scale,
  Sparkles,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { OptimiserPlan, ProposedBatch, Submission } from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { SubmissionDetail } from '@/components/SubmissionDetail'
import { DecisionBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, ErrorState, LoadingPanel, Skeleton } from '@/components/ui/states'
import { cn, formatMoney, formatNumber, humanise } from '@/lib/utils'

const STATUS_TONES: Record<string, 'neutral' | 'brand' | 'positive' | 'caution' | 'outline'> = {
  draft: 'outline',
  planned: 'brand',
  shipped: 'caution',
  received: 'caution',
  grading: 'caution',
  returned: 'positive',
  cancelled: 'neutral',
}

export function Submissions() {
  const queryClient = useQueryClient()
  const [openId, setOpenId] = useState<string | null>(null)
  const [planning, setPlanning] = useState(false)

  const submissions = useQuery({ queryKey: keys.submissions, queryFn: api.listSubmissions })
  const companies = useQuery({ queryKey: keys.companies, queryFn: api.listGradingCompanies })

  // Only fetched when asked for: it runs the decision engine twice over every
  // ready card, which is not something to do on page load.
  const plan = useQuery({
    queryKey: keys.optimiserPlan(),
    queryFn: () => api.optimiseSubmissions(),
    enabled: planning,
  })

  const create = useMutation({
    mutationFn: (payload: { company_id: string; card_ids?: string[]; tier_id?: string | null }) =>
      api.createSubmission(payload),
    onSuccess: (submission) => {
      toast.success(`Started ${submission.reference}`)
      queryClient.invalidateQueries({ queryKey: keys.submissions })
      setOpenId(submission.id)
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not start a submission'),
  })

  if (submissions.isLoading) return <LoadingPanel label="Loading submissions…" />
  if (submissions.isError) {
    return (
      <div className="p-6">
        <ErrorState error={submissions.error} />
      </div>
    )
  }

  const rows = submissions.data ?? []
  const defaultCompany = companies.data?.find((company) => company.active)

  return (
    <>
      <PageHeader
        title="Submissions"
        description="What each parcel costs, and which cards are worth putting in it."
        actions={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setPlanning(true)
                queryClient.invalidateQueries({ queryKey: keys.optimiserPlan() })
              }}
              disabled={plan.isFetching}
            >
              <Sparkles /> {plan.isFetching ? 'Planning…' : 'Plan from my collection'}
            </Button>
            <Button
              variant="primary"
              onClick={() => defaultCompany && create.mutate({ company_id: defaultCompany.id })}
              disabled={!defaultCompany || create.isPending}
            >
              <Plus /> New submission
            </Button>
          </>
        }
      />

      <div className="space-y-6 p-6">
        {planning ? (
          <OptimiserPanel
            plan={plan.data}
            loading={plan.isFetching}
            error={plan.error}
            onBuild={(batch) => {
              if (!batch.cards.length) return
              create.mutate({
                company_id: batch.company_id,
                tier_id: batch.tier_id,
                card_ids: batch.cards.filter((card) => card.still_pays).map((card) => card.card_id),
              })
            }}
            building={create.isPending}
          />
        ) : null}

        {!rows.length ? (
          <Panel>
            <EmptyState
              icon={<PackageCheck className="size-8" />}
              title="No submissions yet"
              description="A submission is a parcel: the cards in it share the postage and insurance, so adding a card lowers the cost of every card already in it. Plan one from your collection, or start an empty one and add cards yourself."
              action={
                <Button
                  variant="primary"
                  onClick={() => {
                    setPlanning(true)
                    queryClient.invalidateQueries({ queryKey: keys.optimiserPlan() })
                  }}
                >
                  <Sparkles /> Plan from my collection
                </Button>
              }
            />
          </Panel>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {rows.map((submission) => (
              <SubmissionCard
                key={submission.id}
                submission={submission}
                onOpen={() => setOpenId(submission.id)}
              />
            ))}
          </div>
        )}
      </div>

      <Dialog open={openId !== null} onOpenChange={(open) => !open && setOpenId(null)}>
        <DialogContent title="Submission" className="max-w-4xl">
          {openId ? (
            <SubmissionDetail submissionId={openId} onClosed={() => setOpenId(null)} />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}

function SubmissionCard({
  submission,
  onOpen,
}: {
  submission: Submission
  onOpen: () => void
}) {
  return (
    <Panel className="flex flex-col">
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle className="truncate">{submission.name || submission.reference}</PanelTitle>
          <PanelDescription>
            {submission.company_code ?? 'No grader'} ·{' '}
            {formatNumber(submission.card_count)} card(s)
          </PanelDescription>
        </div>
        <Badge tone={STATUS_TONES[submission.status] ?? 'neutral'}>
          {humanise(submission.status)}
        </Badge>
      </PanelHeader>
      <PanelBody className="flex-1 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Figure label="Total cost" value={formatMoney(submission.total_cost, submission.currency)} />
          <Figure
            label="Per card"
            value={formatMoney(submission.cost_per_card, submission.currency)}
            hint={submission.card_count ? 'average' : 'no cards yet'}
          />
        </div>
        {submission.blockers.length ? (
          <p className="flex items-start gap-2 rounded-lg border border-caution/30 bg-caution/5 px-3 py-2 text-xs leading-relaxed text-ink-muted">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-caution" />
            {submission.blockers[0]}
            {submission.blockers.length > 1
              ? ` (+${submission.blockers.length - 1} more)`
              : ''}
          </p>
        ) : null}
      </PanelBody>
      <div className="px-5 pb-4">
        <Button size="sm" variant="secondary" onClick={onOpen}>
          Open <ArrowRight />
        </Button>
      </div>
    </Panel>
  )
}

/**
 * The optimiser's plan.
 *
 * The part that earns its place is `stopped_paying`: cards that were worth
 * grading in a full batch and are not in the batch they actually landed in. A
 * planner that hid those would be proposing a plan it had never costed.
 */
function OptimiserPanel({
  plan,
  loading,
  error,
  onBuild,
  building,
}: {
  plan: OptimiserPlan | undefined
  loading: boolean
  error: unknown
  onBuild: (batch: ProposedBatch) => void
  building: boolean
}) {
  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Suggested submissions</PanelTitle>
          <PanelDescription>
            {plan
              ? `Routed ${formatNumber(plan.worth_grading)} card(s) worth grading at a batch of ${plan.routed_at_batch_size}, then re-costed each one at the size its batch actually came out at.`
              : 'Grouping the cards worth grading, then checking they still pay at the size they end up in.'}
          </PanelDescription>
        </div>
        {plan ? (
          <div className="text-right">
            <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">
              Expected profit
            </p>
            <p className="tabular text-lg font-semibold text-positive">
              {formatMoney(plan.expected_profit, plan.currency, { signed: true })}
            </p>
          </div>
        ) : null}
      </PanelHeader>

      <PanelBody className="space-y-4">
        {error ? <ErrorState error={error} /> : null}
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        ) : null}

        {plan && !loading ? (
          <>
            {!plan.batches.length ? (
              <EmptyState
                icon={<Sparkles className="size-8" />}
                title="Nothing to send"
                description={plan.reason ?? 'No card clears your bar for grading right now.'}
              />
            ) : null}

            {plan.batches.map((batch) => (
              <BatchCard
                key={`${batch.company_code}:${batch.tier_name}`}
                batch={batch}
                currency={plan.currency}
                onBuild={() => onBuild(batch)}
                building={building}
              />
            ))}

            {plan.stopped_paying.length ? (
              <div className="rounded-lg border border-caution/30 bg-caution/5 px-4 py-3">
                <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-caution">
                  <AlertTriangle className="size-3.5" />
                  Stopped paying once the batch was real
                </p>
                <div className="mt-2 space-y-2">
                  {plan.stopped_paying.map((card) => (
                    <div key={card.card_id} className="text-sm">
                      <Link
                        to={`/cards/${card.card_id}`}
                        className="font-medium text-ink hover:underline"
                      >
                        {card.name}
                      </Link>
                      <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">{card.reason}</p>
                    </div>
                  ))}
                </div>
                <p className="mt-2 text-[0.7rem] leading-relaxed text-ink-faint">
                  These are left out of every total above. Fill the batch they belong to and they
                  come back.
                </p>
              </div>
            ) : null}

            {plan.notes.map((note) => (
              <p
                key={note}
                className="flex items-start gap-2 text-[0.7rem] leading-relaxed text-ink-faint"
              >
                <Info className="mt-0.5 size-3.5 shrink-0" />
                {note}
              </p>
            ))}
          </>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

function BatchCard({
  batch,
  currency,
  onBuild,
  building,
}: {
  batch: ProposedBatch
  currency: string
  onBuild: () => void
  building: boolean
}) {
  const paying = batch.cards.filter((card) => card.still_pays)
  const misrouted = batch.effective_tier_name !== batch.tier_name

  return (
    <div className="rounded-lg border border-line">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">
            {batch.company_code} {batch.tier_name}
            {/* A short batch is graded at whatever tier it qualifies for today,
                which is not the one it was routed to. Quoting the routed tier's
                name against this tier's price would describe a route that does
                not exist at this size. */}
            {misrouted ? (
              <span className="ml-2 text-xs font-normal text-caution">
                → graded at {batch.effective_tier_name} as it stands
              </span>
            ) : null}
          </p>
          <p className="text-xs text-ink-faint">
            {formatNumber(paying.length)} card(s)
            {batch.minimum_cards > 1 ? ` · minimum ${batch.minimum_cards}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="tabular text-sm font-semibold text-ink">
              {formatMoney(batch.expected_profit, currency, { signed: true })}
            </p>
            <p className="text-[0.7rem] text-ink-faint">
              for {formatMoney(batch.grading_cost, currency)}
            </p>
          </div>
          <Badge tone={batch.viable ? 'positive' : 'caution'}>
            {batch.viable ? 'ready' : `${batch.short_by} short`}
          </Badge>
          <Button size="sm" onClick={onBuild} disabled={building || !paying.length}>
            Build it
          </Button>
        </div>
      </div>

      {batch.reason ? (
        <p className="flex items-start gap-2 border-b border-line px-4 py-2 text-xs leading-relaxed text-ink-muted">
          <Scale className="mt-0.5 size-3.5 shrink-0 text-ink-faint" />
          {batch.reason}
        </p>
      ) : null}

      <div className="divide-y divide-line">
        {batch.cards.map((card) => (
          <div
            key={card.card_id}
            className={cn(
              'flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2',
              !card.still_pays && 'opacity-60',
            )}
          >
            <Link
              to={`/cards/${card.card_id}`}
              className="min-w-0 flex-1 truncate text-sm text-ink hover:underline"
            >
              {card.name}
            </Link>
            {card.cheaper_tier_name ? (
              <Badge tone="brand" className="shrink-0">
                {card.cheaper_tier_name} saves{' '}
                {formatMoney(card.cheaper_tier_saving, currency)}
              </Badge>
            ) : null}
            <span className="tabular shrink-0 text-sm text-ink">
              {formatMoney(card.expected_profit, currency, { signed: true })}
            </span>
            <DecisionBadge decision={card.decision_in_batch} />
          </div>
        ))}
      </div>
    </div>
  )
}

function Figure({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className="tabular mt-0.5 font-semibold text-ink">{value}</p>
      {hint ? <p className="text-[0.7rem] text-ink-faint">{hint}</p> : null}
    </div>
  )
}
