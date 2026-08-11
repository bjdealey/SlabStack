import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ClipboardCheck, Pencil, Scissors, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { CardWrite, ConditionWrite } from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { CardForm } from '@/components/CardForm'
import { ConditionForm } from '@/components/ConditionForm'
import { ImageUploader } from '@/components/ImageUploader'
import { ExplanationList } from '@/components/ExplanationList'
import { GradeProbabilities } from '@/components/GradeProbabilities'
import { MarketPanel } from '@/components/MarketPanel'
import { DecisionBadge, StatusBadge } from '@/components/DecisionBadge'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { ErrorState, LoadingPanel } from '@/components/ui/states'
import { cardSubtitle, cardTitle, formatDate, formatMoney, formatScore, humanise } from '@/lib/utils'

export function CardDetail() {
  const { cardId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [assessing, setAssessing] = useState(false)

  const card = useQuery({ queryKey: keys.card(cardId), queryFn: () => api.getCard(cardId) })
  const evaluation = useQuery({
    queryKey: keys.evaluation(cardId),
    queryFn: () => api.evaluateCard(cardId),
    enabled: Boolean(cardId),
  })
  const condition = useQuery({
    queryKey: keys.condition(cardId),
    queryFn: () => api.getCondition(cardId),
    retry: false,
    // A card with no assessment yet is the normal case, not an error.
    enabled: Boolean(card.data?.has_condition_assessment),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: keys.card(cardId) })
    queryClient.invalidateQueries({ queryKey: keys.evaluation(cardId) })
    queryClient.invalidateQueries({ queryKey: keys.condition(cardId) })
    queryClient.invalidateQueries({ queryKey: keys.summary })
  }

  const update = useMutation({
    mutationFn: (payload: Partial<CardWrite>) => api.updateCard(cardId, payload),
    onSuccess: () => {
      toast.success('Card updated')
      setEditing(false)
      refresh()
      queryClient.invalidateQueries({ queryKey: ['cards'] })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Update failed'),
  })

  const saveCondition = useMutation({
    mutationFn: (payload: ConditionWrite) => api.putCondition(cardId, payload),
    onSuccess: () => {
      toast.success('Condition assessment saved')
      setAssessing(false)
      refresh()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save the assessment'),
  })

  const split = useMutation({
    mutationFn: () => api.splitCard(cardId),
    onSuccess: (cards) => {
      toast.success(`Split into ${cards.length} individual cards`)
      refresh()
      queryClient.invalidateQueries({ queryKey: ['cards'] })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not split the card'),
  })

  const remove = useMutation({
    mutationFn: () => api.deleteCard(cardId),
    onSuccess: () => {
      toast.success('Card deleted')
      queryClient.invalidateQueries({ queryKey: ['cards'] })
      queryClient.invalidateQueries({ queryKey: keys.summary })
      navigate('/collection')
    },
  })

  if (card.isLoading) return <LoadingPanel />
  if (card.isError) {
    return (
      <div className="p-6">
        <ErrorState error={card.error} />
      </div>
    )
  }

  const data = card.data!
  const evaluated = evaluation.data

  return (
    <>
      <PageHeader
        title={cardTitle(data)}
        description={cardSubtitle(data)}
        actions={
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/collection">
                <ArrowLeft /> Collection
              </Link>
            </Button>
            {data.quantity > 1 ? (
              <Button size="sm" onClick={() => split.mutate()} disabled={split.isPending}>
                <Scissors /> Split ×{data.quantity}
              </Button>
            ) : null}
            <Button size="sm" onClick={() => setEditing(true)}>
              <Pencil /> Edit
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                if (window.confirm(`Delete ${data.name}? This also deletes its images.`)) {
                  remove.mutate()
                }
              }}
            >
              <Trash2 />
            </Button>
          </>
        }
      />

      <div className="grid gap-6 p-6 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-6">
          <Panel>
            <PanelHeader>
              <div>
                <PanelTitle>Photographs</PanelTitle>
                <PanelDescription>
                  Front and back. The back is what makes back centering and edge wear checkable.
                </PanelDescription>
              </div>
            </PanelHeader>
            <PanelBody>
              <ImageUploader card={data} />
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader>
              <div>
                <PanelTitle>Card details</PanelTitle>
              </div>
            </PanelHeader>
            <PanelBody className="space-y-2 text-sm">
              <Detail label="Set" value={data.set_name ?? data.set_code} />
              <Detail label="Number" value={data.card_number} />
              <Detail label="Variant" value={data.variant} />
              <Detail label="Printing" value={data.printing} />
              <Detail label="Rarity" value={data.rarity} />
              <Detail label="Language" value={data.language} />
              <Detail label="Quantity" value={String(data.quantity)} />
              <Detail label="Status" value={humanise(data.status)} />
              <Detail label="Purchase price" value={formatMoney(data.purchase_price)} />
              <Detail label="Purchased" value={formatDate(data.purchase_date)} />
              <Detail label="Your raw estimate" value={formatMoney(data.user_raw_value)} />
              <Detail label="Market identity" value={data.catalog_key} mono />
              {data.notes ? (
                <p className="border-t border-line pt-3 text-xs leading-relaxed text-ink-muted">
                  {data.notes}
                </p>
              ) : null}
            </PanelBody>
          </Panel>
        </div>

        <div className="space-y-6">
          {evaluation.isLoading ? <LoadingPanel label="Evaluating…" /> : null}
          {evaluation.isError ? <ErrorState error={evaluation.error} /> : null}

          {evaluated ? (
            <>
              <Panel>
                <PanelHeader>
                  <div>
                    <PanelTitle>Recommendation</PanelTitle>
                    <PanelDescription>{evaluated.recommendation.headline}</PanelDescription>
                  </div>
                  <DecisionBadge decision={evaluated.recommendation.decision} />
                </PanelHeader>
                <PanelBody className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Metric
                      label="Expected profit"
                      value={
                        evaluated.recommendation.expected_profit === null
                          ? '—'
                          : formatMoney(evaluated.recommendation.expected_profit, evaluated.currency, {
                              signed: true,
                            })
                      }
                    />
                    <Metric
                      label="ROI"
                      value={
                        evaluated.recommendation.roi_pct === null
                          ? '—'
                          : `${evaluated.recommendation.roi_pct.toFixed(0)}%`
                      }
                    />
                    <Metric
                      label="P(profit)"
                      value={
                        evaluated.recommendation.probability_of_profit === null
                          ? '—'
                          : `${Math.round(evaluated.recommendation.probability_of_profit * 100)}%`
                      }
                    />
                    <Metric
                      label="Min. grade"
                      value={
                        evaluated.recommendation.minimum_profitable_grade === null
                          ? '—'
                          : String(evaluated.recommendation.minimum_profitable_grade)
                      }
                    />
                  </div>

                  {evaluated.blockers.length ? (
                    <div className="rounded-lg border border-line bg-canvas px-4 py-3">
                      <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">
                        Still needed
                      </p>
                      <ul className="mt-2 space-y-1 text-sm text-ink-muted">
                        {evaluated.blockers.map((blocker) => (
                          <li key={blocker}>• {blocker}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  <div>
                    <p className="mb-2.5 text-xs font-medium uppercase tracking-wider text-ink-faint">
                      Why?
                    </p>
                    <ExplanationList items={evaluated.explanation} />
                  </div>
                </PanelBody>
              </Panel>

              <Panel>
                <PanelHeader>
                  <div>
                    <PanelTitle>Condition</PanelTitle>
                    <PanelDescription>
                      {evaluated.condition.reason ??
                        `Assessed ${formatDate(evaluated.condition.assessed_at)}`}
                    </PanelDescription>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={evaluated.condition.status} />
                    <Button size="sm" onClick={() => setAssessing(true)}>
                      <ClipboardCheck />
                      {evaluated.condition.assessment_id ? 'Reassess' : 'Assess'}
                    </Button>
                  </div>
                </PanelHeader>
                <PanelBody className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                    <Metric label="Centering" value={formatScore(evaluated.condition.scores.centering)} />
                    <Metric label="Corners" value={formatScore(evaluated.condition.scores.corners)} />
                    <Metric label="Edges" value={formatScore(evaluated.condition.scores.edges)} />
                    <Metric label="Surface" value={formatScore(evaluated.condition.scores.surface)} />
                    <Metric
                      label="Overall"
                      value={formatScore(evaluated.condition.scores.overall)}
                      emphasis
                    />
                  </div>

                  {evaluated.condition.notable_defects.length ? (
                    <div className="flex flex-wrap gap-1.5">
                      {evaluated.condition.notable_defects.map((defect) => (
                        <Badge key={defect} tone="caution">
                          {defect}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </PanelBody>
              </Panel>

              {evaluated.grade_prediction.status === 'not_assessed' ? (
                <Panel>
                  <PanelHeader>
                    <div>
                      <PanelTitle>Grade probabilities</PanelTitle>
                      <PanelDescription>{evaluated.grade_prediction.reason}</PanelDescription>
                    </div>
                    <Button size="sm" onClick={() => setAssessing(true)}>
                      <ClipboardCheck /> Assess
                    </Button>
                  </PanelHeader>
                </Panel>
              ) : (
                <GradeProbabilities block={evaluated.grade_prediction} />
              )}

              <MarketPanel cardId={cardId} evaluation={evaluated} />

              <Panel>
                <PanelHeader>
                  <div>
                    <PanelTitle>Grading routes</PanelTitle>
                    <PanelDescription>{evaluated.grading_options.reason}</PanelDescription>
                  </div>
                  <div className="text-right">
                    <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">
                      Net raw sale
                    </p>
                    <p className="tabular text-sm text-ink">
                      {formatMoney(evaluated.raw.net_raw_sale_value, evaluated.currency)}
                      <span className="ml-1 text-[0.7rem] text-ink-faint">Phase 4</span>
                    </p>
                  </div>
                </PanelHeader>
                <PanelBody className="grid gap-2 sm:grid-cols-2">
                  {evaluated.grading_options.options.map((option, index) => (
                    <div
                      key={`${option.company_code}-${option.tier_id ?? index}`}
                      className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-ink">
                          {option.company_code}
                          {option.tier_name ? ` · ${option.tier_name}` : ''}
                        </p>
                        <p className="truncate text-xs text-ink-faint">
                          {option.available
                            ? [
                                option.requires_batch ? `min ${option.minimum_cards} cards` : null,
                                option.turnaround_days ? `${option.turnaround_days} days` : null,
                              ]
                                .filter(Boolean)
                                .join(' · ') || 'No minimum'
                            : option.blockers[0]}
                        </p>
                      </div>
                      <span className="tabular shrink-0 text-sm text-ink">
                        {option.available ? formatMoney(option.grading_fee, option.currency) : '—'}
                      </span>
                    </div>
                  ))}
                </PanelBody>
              </Panel>
            </>
          ) : null}
        </div>
      </div>

      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent title="Edit card">
          <CardForm
            card={data}
            onSubmit={(payload) => update.mutate(payload)}
            onCancel={() => setEditing(false)}
            submitting={update.isPending}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={assessing} onOpenChange={setAssessing}>
        <DialogContent
          title="Condition assessment"
          description="Record what you can see. Previous assessments are kept — re-examining a card is a new opinion, not a correction."
        >
          <ConditionForm
            existing={condition.data}
            onSubmit={(payload) => saveCondition.mutate(payload)}
            onCancel={() => setAssessing(false)}
            submitting={saveCondition.isPending}
          />
        </DialogContent>
      </Dialog>
    </>
  )
}

function Detail({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="shrink-0 text-xs text-ink-faint">{label}</span>
      <span className={mono ? 'truncate font-mono text-xs text-ink-muted' : 'truncate text-ink'}>
        {value || '—'}
      </span>
    </div>
  )
}

function Metric({
  label,
  value,
  hint,
  emphasis,
}: {
  label: string
  value: string
  hint?: string
  emphasis?: boolean
}) {
  return (
    <div>
      <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className={`tabular mt-0.5 font-semibold ${emphasis ? 'text-lg text-brand' : 'text-base text-ink'}`}>
        {value}
      </p>
      {hint ? <p className="text-[0.7rem] text-ink-faint">{hint}</p> : null}
    </div>
  )
}

