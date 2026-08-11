import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Info, Plus, Scale, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { Submission, SubmissionCardLine } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, Input, Select } from '@/components/ui/field'
import { ErrorState, LoadingPanel } from '@/components/ui/states'
import { cn, formatMoney, formatNumber, humanise } from '@/lib/utils'

const LIFECYCLE = ['draft', 'planned', 'shipped', 'received', 'grading', 'returned'] as const

/** Statuses after which the parcel is a record of what you sent, not a draft. */
const SEALED = new Set(['shipped', 'received', 'grading', 'returned', 'cancelled'])

export function SubmissionDetail({
  submissionId,
  onClosed,
}: {
  submissionId: string
  onClosed: () => void
}) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)

  const submission = useQuery({
    queryKey: keys.submission(submissionId),
    queryFn: () => api.getSubmission(submissionId),
  })

  const refresh = (updated: Submission) => {
    queryClient.setQueryData(keys.submission(submissionId), updated)
    queryClient.invalidateQueries({ queryKey: keys.submissions })
  }

  const update = useMutation({
    mutationFn: (payload: Parameters<typeof api.updateSubmission>[1]) =>
      api.updateSubmission(submissionId, payload),
    onSuccess: refresh,
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the submission'),
  })

  const removeCard = useMutation({
    mutationFn: (lineId: string) => api.removeSubmissionCard(submissionId, lineId),
    onSuccess: refresh,
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not remove the card'),
  })

  const remove = useMutation({
    mutationFn: () => api.deleteSubmission(submissionId),
    onSuccess: () => {
      toast.success('Submission deleted')
      queryClient.invalidateQueries({ queryKey: keys.submissions })
      onClosed()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not delete the submission'),
  })

  if (submission.isLoading) return <LoadingPanel />
  if (submission.isError) return <ErrorState error={submission.error} />

  const data = submission.data!
  const sealed = SEALED.has(data.status)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-xs text-ink-faint">{data.reference}</p>
          <p className="text-lg font-semibold text-ink">
            {data.company_name ?? 'No grading company'}
          </p>
          <p className="text-xs text-ink-faint">
            {formatNumber(data.card_count)} card(s) ·{' '}
            {formatMoney(data.declared_value_total, data.currency)} declared
          </p>
        </div>
        <Field label="Status" className="w-40">
          <Select
            value={data.status}
            onChange={(event) => update.mutate({ status: event.target.value })}
          >
            {LIFECYCLE.map((option) => (
              <option key={option} value={option}>
                {humanise(option)}
              </option>
            ))}
            <option value="cancelled">Cancelled</option>
          </Select>
        </Field>
      </div>

      {sealed ? (
        <p className="flex items-start gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-xs leading-relaxed text-ink-muted">
          <Info className="mt-0.5 size-3.5 shrink-0" />
          This parcel is {humanise(data.status).toLowerCase()}, so its cards can no longer change.
          What you sent is a record — the actual grades recorded against it are what the accuracy
          analysis will learn from.
        </p>
      ) : null}

      {/* --- The money ---------------------------------------------------- */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Figure label="Grading fees" value={formatMoney(data.grading_fees, data.currency)} />
        <Figure
          label="Shared costs"
          value={formatMoney(data.shared_pot, data.currency)}
          hint="postage and insurance"
        />
        <Figure label="Total" value={formatMoney(data.total_cost, data.currency)} emphasis />
        <Figure
          label="Per card"
          value={formatMoney(data.cost_per_card, data.currency)}
          hint={data.card_count ? 'average' : 'no cards yet'}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Shipping out" hint="What it costs to send the parcel.">
          <Input
            type="number"
            step="0.01"
            defaultValue={data.shipping_out ?? 0}
            disabled={sealed}
            onBlur={(event) => update.mutate({ shipping_out: Number(event.target.value) })}
          />
        </Field>
        <Field label="Shipping back" hint="Return postage, usually insured.">
          <Input
            type="number"
            step="0.01"
            defaultValue={data.shipping_return ?? 0}
            disabled={sealed}
            onBlur={(event) => update.mutate({ shipping_return: Number(event.target.value) })}
          />
        </Field>
      </div>

      <Field
        label="How shared costs are split"
        hint="Equal gives every card the same share. Weighted by value puts more of the postage on the cards it is protecting."
      >
        <Select
          value={data.allocation_method}
          disabled={sealed}
          onChange={(event) => update.mutate({ cost_allocation_method: event.target.value })}
        >
          <option value="equal">Equally</option>
          <option value="value_weighted">By declared value</option>
        </Select>
      </Field>

      {data.allocation_note ? (
        <p className="flex items-start gap-2 text-[0.7rem] leading-relaxed text-ink-faint">
          <Scale className="mt-0.5 size-3.5 shrink-0" />
          {data.allocation_note}
        </p>
      ) : null}

      {/* --- Tier groups --------------------------------------------------- */}
      {data.tiers.length ? (
        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-faint">
            Tiers in this parcel
          </p>
          <div className="space-y-1.5">
            {data.tiers.map((group) => (
              <div
                key={group.tier_id ?? 'none'}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line px-3 py-2 text-sm"
              >
                <span className="flex-1 text-ink">
                  {group.company_code} {group.tier_name ?? 'No tier'}
                </span>
                <span className="tabular text-xs text-ink-muted">
                  {formatNumber(group.card_count)}
                  {group.minimum_cards > 1 ? ` / ${formatNumber(group.minimum_cards)}` : ''}
                </span>
                {group.short_by ? (
                  <Badge tone="caution">{group.short_by} short</Badge>
                ) : (
                  <Badge tone="positive">ok</Badge>
                )}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {/* --- Cards --------------------------------------------------------- */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-medium uppercase tracking-wider text-ink-faint">Cards</p>
          {!sealed ? (
            <Button size="sm" variant="ghost" onClick={() => setAdding((open) => !open)}>
              <Plus /> Add cards
            </Button>
          ) : null}
        </div>

        {adding && !sealed ? (
          <AddCards
            submissionId={submissionId}
            already={new Set(data.cards.map((line) => line.card_id))}
            onAdded={(updated) => {
              refresh(updated)
              setAdding(false)
            }}
          />
        ) : null}

        {data.cards.length ? (
          <div className="space-y-1.5">
            {data.cards.map((line) => (
              <CardRow
                key={line.submission_card_id}
                line={line}
                currency={data.currency}
                sealed={sealed}
                showGrade={data.status === 'returned' || data.status === 'grading'}
                onRemove={() => removeCard.mutate(line.submission_card_id)}
                onGrade={(grade) =>
                  api
                    .updateSubmissionCard(submissionId, line.submission_card_id, {
                      actual_grade: grade,
                      status: 'graded',
                    })
                    .then(refresh)
                    .catch(() => toast.error('Could not record the grade'))
                }
              />
            ))}
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-ink-faint">
            No cards yet. A parcel of one carries all the postage on its own.
          </p>
        )}
      </div>

      {data.blockers.length ? (
        <div className="rounded-lg border border-caution/30 bg-caution/5 px-4 py-3">
          <p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-caution">
            <AlertTriangle className="size-3.5" /> Before you send it
          </p>
          <ul className="mt-2 space-y-1 text-sm text-ink-muted">
            {data.blockers.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.warnings.map((item) => (
        <p
          key={item}
          className="flex items-start gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-xs leading-relaxed text-ink-muted"
        >
          <Info className="mt-0.5 size-3.5 shrink-0 text-ink-faint" />
          {item}
        </p>
      ))}

      <div className="flex justify-between border-t border-line pt-4">
        <Button
          variant="danger"
          size="sm"
          onClick={() => {
            if (window.confirm(`Delete ${data.reference}?`)) remove.mutate()
          }}
          disabled={remove.isPending}
        >
          <Trash2 /> Delete
        </Button>
        <Button variant="ghost" onClick={onClosed}>
          Close
        </Button>
      </div>
    </div>
  )
}

function CardRow({
  line,
  currency,
  sealed,
  showGrade,
  onRemove,
  onGrade,
}: {
  line: SubmissionCardLine
  currency: string
  sealed: boolean
  showGrade: boolean
  onRemove: () => void
  onGrade: (grade: number) => void
}) {
  return (
    <div
      className={cn(
        'rounded-lg border px-3 py-2',
        line.blockers.length ? 'border-caution/40' : 'border-line',
      )}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          to={`/cards/${line.card_id}`}
          className="min-w-0 flex-1 truncate text-sm text-ink hover:underline"
        >
          {line.name}
        </Link>
        <span className="shrink-0 text-xs text-ink-faint">{line.tier_name ?? 'No tier'}</span>
        <span className="tabular shrink-0 text-xs text-ink-muted">
          {formatMoney(line.declared_value, currency)} declared
        </span>
        {line.declared_value_source === 'user' ? <Badge tone="brand">yours</Badge> : null}
        <span className="tabular w-16 shrink-0 text-right text-xs text-ink-faint">
          +{formatMoney(line.allocated_overhead, currency)}
        </span>
        <span className="tabular w-16 shrink-0 text-right text-sm font-semibold text-ink">
          {formatMoney(line.total_cost, currency)}
        </span>
        {showGrade ? (
          <Input
            type="number"
            step="0.5"
            className="w-20 shrink-0"
            placeholder="grade"
            defaultValue={line.actual_grade ?? ''}
            onBlur={(event) => {
              const value = Number(event.target.value)
              if (value > 0 && value !== line.actual_grade) onGrade(value)
            }}
          />
        ) : null}
        {!sealed ? (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${line.name}`}
            className="shrink-0 rounded p-1 text-ink-faint hover:bg-surface-raised hover:text-negative"
          >
            <Trash2 className="size-3.5" />
          </button>
        ) : null}
      </div>
      {line.blockers.map((blocker) => (
        <p key={blocker} className="mt-1 text-[0.7rem] leading-relaxed text-caution">
          {blocker}
        </p>
      ))}
    </div>
  )
}

/** Pick cards to add. Kept simple: search the collection, tick what you want. */
function AddCards({
  submissionId,
  already,
  onAdded,
}: {
  submissionId: string
  already: Set<string>
  onAdded: (submission: Submission) => void
}) {
  const [term, setTerm] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())

  const cards = useQuery({
    queryKey: keys.cards({ q: term, page_size: 25 }),
    queryFn: () => api.listCards({ q: term || undefined, page_size: 25 }),
  })

  const add = useMutation({
    mutationFn: () => api.addSubmissionCards(submissionId, [...chosen]),
    onSuccess: (updated) => {
      toast.success(`Added ${chosen.size} card(s)`)
      setChosen(new Set())
      onAdded(updated)
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not add the cards'),
  })

  const available = (cards.data?.items ?? []).filter((card) => !already.has(card.id))

  return (
    <div className="mb-3 space-y-2 rounded-lg border border-line bg-canvas p-3">
      <Field label="Find cards">
        <Input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="Search your collection"
        />
      </Field>

      <div className="max-h-48 space-y-1 overflow-y-auto">
        {available.map((card) => (
          <label
            key={card.id}
            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-surface-raised"
          >
            <input
              type="checkbox"
              checked={chosen.has(card.id)}
              onChange={(event) => {
                const next = new Set(chosen)
                if (event.target.checked) next.add(card.id)
                else next.delete(card.id)
                setChosen(next)
              }}
            />
            <span className="min-w-0 flex-1 truncate text-ink">
              {card.name} {card.card_number ?? ''}
            </span>
            <span className="tabular shrink-0 text-xs text-ink-faint">
              {formatMoney(card.user_declared_value ?? card.user_raw_value)}
            </span>
          </label>
        ))}
        {!available.length ? (
          <p className="px-2 py-3 text-center text-xs text-ink-faint">
            {cards.isLoading ? 'Searching…' : 'Nothing else to add.'}
          </p>
        ) : null}
      </div>

      <Button size="sm" onClick={() => add.mutate()} disabled={!chosen.size || add.isPending}>
        Add {chosen.size || ''} card(s)
      </Button>
    </div>
  )
}

function Figure({
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
      <p className={cn('tabular mt-0.5 font-semibold', emphasis ? 'text-brand' : 'text-ink')}>
        {value}
      </p>
      {hint ? <p className="text-[0.7rem] text-ink-faint">{hint}</p> : null}
    </div>
  )
}
