import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Info, Layers, Minus, Plus, Receipt } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { CardEvaluation, CompanyBestCase, GradingOption } from '@/lib/types'
import { ConfidenceBadge, StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/field'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { formatMoney, formatNumber } from '@/lib/utils'

/** Batch sizes worth a single click. The tier minimums SlabStack ships with are 20 and 25. */
const BATCH_PRESETS = [1, 5, 10, 20, 25, 50]

/**
 * What each grading route would actually cost, and what a sale would keep.
 *
 * The batch control is the point of this panel. Shipping belongs to the parcel,
 * not the card, so the same card can be plainly unprofitable on its own and
 * clearly worth grading in a batch of twenty-five. Every figure here is
 * labelled with the batch it assumed.
 */
export function GradingRoutes({
  cardId,
  evaluation,
  batchSize,
  onBatchSizeChange,
}: {
  cardId: string
  evaluation: CardEvaluation
  batchSize: number
  onBatchSizeChange: (size: number) => void
}) {
  const [editingDeclared, setEditingDeclared] = useState(false)
  const block = evaluation.grading_options
  const currency = block.currency || evaluation.currency
  const byCompany = groupByCompany(block.options)

  return (
    <>
      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>Grading routes</PanelTitle>
            <PanelDescription>
              {block.reason ??
                `Cost per card if you send ${
                  batchSize === 1 ? 'this card on its own' : `a batch of ${batchSize}`
                }.`}
            </PanelDescription>
          </div>
          <StatusBadge status={block.status} phase={block.phase} />
        </PanelHeader>

        <PanelBody className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-canvas px-4 py-3">
            <div className="flex items-center gap-2">
              <Layers className="size-4 shrink-0 text-ink-faint" />
              <div>
                <p className="text-xs font-medium text-ink">Cards in the submission</p>
                <p className="text-[0.7rem] text-ink-faint">
                  Shipping and insurance are shared, so the batch changes the price.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => onBatchSizeChange(Math.max(1, batchSize - 1))}
                className="rounded p-1.5 text-ink-faint hover:bg-surface-raised hover:text-ink"
                aria-label="One fewer card"
              >
                <Minus className="size-3.5" />
              </button>
              {BATCH_PRESETS.map((size) => (
                <button
                  key={size}
                  type="button"
                  onClick={() => onBatchSizeChange(size)}
                  className={`tabular rounded-md px-2.5 py-1 text-xs transition-colors ${
                    size === batchSize
                      ? 'bg-brand text-white'
                      : 'text-ink-muted hover:bg-surface-raised hover:text-ink'
                  }`}
                >
                  {size}
                </button>
              ))}
              <button
                type="button"
                onClick={() => onBatchSizeChange(Math.min(1000, batchSize + 1))}
                className="rounded p-1.5 text-ink-faint hover:bg-surface-raised hover:text-ink"
                aria-label="One more card"
              >
                <Plus className="size-3.5" />
              </button>
              {!BATCH_PRESETS.includes(batchSize) ? (
                <Badge tone="brand" className="ml-1">
                  {batchSize}
                </Badge>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-lg border border-line px-4 py-3">
            <div className="min-w-0">
              <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">
                Declared value
              </p>
              <p className="tabular mt-0.5 text-xl font-semibold text-ink">
                {formatMoney(block.declared_value, currency)}
              </p>
              {block.declared_value_basis ? (
                <p className="mt-1 max-w-xl text-[0.7rem] leading-relaxed text-ink-faint">
                  {block.declared_value_basis}
                </p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {block.declared_value_source === 'user' ? (
                <Badge tone="brand">Your figure</Badge>
              ) : null}
              <ConfidenceBadge confidence={block.declared_value_confidence} />
              <Button size="sm" variant="ghost" onClick={() => setEditingDeclared(true)}>
                Set yours
              </Button>
            </div>
          </div>

          {block.best_case.length ? (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-faint">
                Best case, per grader
              </p>
              <div className="space-y-1.5">
                {block.best_case.map((row) => (
                  <BestCaseRow key={row.company_code} row={row} currency={currency} />
                ))}
              </div>
              <p className="mt-2 text-[0.7rem] leading-relaxed text-ink-faint">
                Each grader's fee is paired with its own slab price — an ACE 10 does not sell for
                what a PSA 10 sells for. Best case only: the probability-weighted figure arrives
                with the decision engine.
              </p>
            </div>
          ) : null}

          <div className="space-y-4">
            {Object.entries(byCompany).map(([code, options]) => (
              <div key={code}>
                {/* Each grader declares against its own slabs, so the same card
                    can be worth £987 to CGC and £240 to a grader whose slabs
                    have never sold. That number drives the ceilings below it,
                    so it belongs beside them rather than only in the headline. */}
                <div className="mb-1.5 flex items-baseline justify-between gap-3">
                  <p className="text-xs font-medium text-ink-muted">{options[0].company_name}</p>
                  <p className="tabular text-[0.7rem] text-ink-faint">
                    declares at {formatMoney(options[0].declared_value, currency)}
                  </p>
                </div>
                <div className="space-y-1.5">
                  {options.map((option, index) => (
                    <OptionRow
                      key={`${option.tier_id ?? index}`}
                      option={option}
                      currency={currency}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>

          {block.allocation_note ? (
            <p className="flex items-start gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-[0.7rem] leading-relaxed text-ink-faint">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              {block.allocation_note}
            </p>
          ) : null}
        </PanelBody>
      </Panel>

      {block.net_values.length ? (
        <Panel>
          <PanelHeader>
            <div>
              <PanelTitle>What a sale actually keeps</PanelTitle>
              <PanelDescription>
                After fees, postage and packaging on {block.selling_profile_name ?? 'your platform'}.
                A slab posts heavier and insured, so it pays the graded postage.
              </PanelDescription>
            </div>
            <Receipt className="size-4 shrink-0 text-ink-faint" />
          </PanelHeader>
          <PanelBody className="space-y-1.5">
            {block.net_values.map((row) => {
              const kept = row.gross && row.net !== null ? row.net / row.gross : null
              return (
                <div
                  key={row.grade_label}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line px-3 py-2"
                >
                  <span className="w-20 shrink-0 text-sm text-ink">
                    {row.grade_label === 'raw' ? 'Raw' : row.grade_label}
                  </span>
                  <span className="tabular w-24 shrink-0 text-right text-sm text-ink-muted">
                    {formatMoney(row.gross, currency)}
                  </span>
                  <span className="shrink-0 text-xs text-ink-faint">−</span>
                  <span className="tabular w-20 shrink-0 text-right text-sm text-negative">
                    {formatMoney(row.total_costs, currency)}
                  </span>
                  <span className="shrink-0 text-xs text-ink-faint">=</span>
                  <span className="tabular w-24 shrink-0 text-right text-sm font-semibold text-ink">
                    {formatMoney(row.net, currency)}
                  </span>
                  <span className="text-[0.7rem] text-ink-faint">
                    {kept !== null ? `you keep ${(kept * 100).toFixed(0)}%` : ''}
                    {row.postage_cost
                      ? ` · ${formatMoney(row.postage_cost, currency)} postage`
                      : ''}
                  </span>
                </div>
              )
            })}
          </PanelBody>
        </Panel>
      ) : null}

      <DeclaredValueDialog
        cardId={cardId}
        open={editingDeclared}
        onOpenChange={setEditingDeclared}
        currency={currency}
        suggested={block.declared_value}
      />
    </>
  )
}

function groupByCompany(options: GradingOption[]): Record<string, GradingOption[]> {
  const grouped: Record<string, GradingOption[]> = {}
  for (const option of options) {
    grouped[option.company_code] = grouped[option.company_code] ?? []
    grouped[option.company_code].push(option)
  }
  return grouped
}

function BestCaseRow({ row, currency }: { row: CompanyBestCase; currency: string }) {
  if (row.upside_vs_raw === null) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-dashed border-line px-3 py-2">
        <span className="w-12 shrink-0 text-sm text-ink-faint">{row.company_code}</span>
        <span className="text-xs text-ink-faint">{row.reason}</span>
      </div>
    )
  }
  const positive = row.upside_vs_raw > 0
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line px-3 py-2">
      <span className="w-12 shrink-0 text-sm font-medium text-ink">{row.company_code}</span>
      <span className="text-xs text-ink-faint">
        {row.tier_name} at {formatMoney(row.grading_cost, currency)} →{' '}
        <span className="text-ink-muted">{row.best_grade_label}</span> nets{' '}
        {formatMoney(row.best_net, currency)}
      </span>
      <span
        className={`tabular ml-auto text-sm font-semibold ${
          positive ? 'text-positive' : 'text-negative'
        }`}
      >
        {formatMoney(row.upside_vs_raw, currency, { signed: true })}
      </span>
    </div>
  )
}

function OptionRow({ option, currency }: { option: GradingOption; currency: string }) {
  const [open, setOpen] = useState(false)
  const parts = [
    option.turnaround_days ? `${option.turnaround_days} days` : null,
    option.requires_batch ? `min ${option.minimum_cards}` : null,
    option.membership_code ? `${option.membership_code} discount applied` : null,
  ].filter(Boolean)

  return (
    <div
      className={`rounded-lg border px-3 py-2 ${
        option.available ? 'border-line' : 'border-dashed border-line bg-canvas'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="min-w-0 flex-1 text-left"
          disabled={option.total_cost === null}
        >
          <span className={`text-sm ${option.available ? 'text-ink' : 'text-ink-faint'}`}>
            {option.tier_name ?? option.company_code}
          </span>
          {parts.length ? (
            <span className="ml-2 text-[0.7rem] text-ink-faint">{parts.join(' · ')}</span>
          ) : null}
        </button>
        <span
          className={`tabular shrink-0 text-sm font-semibold ${
            option.available ? 'text-ink' : 'text-ink-faint line-through'
          }`}
        >
          {option.total_cost !== null ? formatMoney(option.total_cost, currency) : '—'}
        </span>
      </div>

      {option.blockers.length ? (
        <ul className="mt-1 space-y-0.5">
          {option.blockers.map((blocker) => (
            <li key={blocker} className="text-[0.7rem] leading-relaxed text-caution">
              {blocker}
            </li>
          ))}
        </ul>
      ) : null}

      {open && option.total_cost !== null ? (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line pt-2 text-[0.7rem]">
          <Line label="Tier price" value={formatMoney(option.base_fee, currency)} />
          {option.membership_discount ? (
            <Line
              label="Membership discount"
              value={`−${formatMoney(option.membership_discount, currency)}`}
            />
          ) : null}
          {option.per_card_fees ? (
            <Line label="Per-card fees" value={formatMoney(option.per_card_fees, currency)} />
          ) : null}
          {option.declared_value_fee ? (
            <Line
              label="Declared-value fee"
              value={formatMoney(option.declared_value_fee, currency)}
            />
          ) : null}
          <Line
            label={`Share of ${formatMoney(option.shared_total, currency)} shared`}
            value={formatMoney(option.allocated_overhead, currency)}
            detail={`split ${formatNumber(option.assumed_batch_size)} ways`}
          />
        </dl>
      ) : null}
    </div>
  )
}

function Line({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <>
      <dt className="text-ink-faint">
        {label}
        {detail ? <span className="ml-1 opacity-70">({detail})</span> : null}
      </dt>
      <dd className="tabular text-right text-ink-muted">{value}</dd>
    </>
  )
}

function DeclaredValueDialog({
  cardId,
  open,
  onOpenChange,
  currency,
  suggested,
}: {
  cardId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  currency: string
  suggested: number | null
}) {
  const [value, setValue] = useState('')
  const queryClient = useQueryClient()

  const save = useMutation({
    mutationFn: (declared: number | null) =>
      api.updateCard(cardId, { user_declared_value: declared }),
    onSuccess: (_card, declared) => {
      toast.success(declared === null ? 'Using the estimate again' : 'Declared value saved')
      queryClient.invalidateQueries({ queryKey: keys.card(cardId) })
      queryClient.invalidateQueries({ queryKey: ['evaluation', cardId] })
      onOpenChange(false)
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save'),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Declared value"
        description="What you tell the grader the card is worth. It decides which tiers you are eligible for and what the parcel costs to insure — so over-declaring buys a more expensive tier than you need, and under-declaring leaves you short if they lose it."
      >
        <div className="space-y-4 px-5 py-4">
          <div className="rounded-lg border border-line bg-canvas px-4 py-3 text-xs text-ink-muted">
            SlabStack suggests{' '}
            <span className="tabular font-medium text-ink">
              {formatMoney(suggested, currency)}
            </span>
            . Your figure is stored separately, so clearing it brings the estimate straight back.
          </div>
          <Field label={`Your declared value (${currency})`} hint="Leave empty to use the estimate.">
            <Input
              type="number"
              step="0.01"
              min="0"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={suggested !== null ? String(suggested) : '0.00'}
            />
          </Field>
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate(value.trim() === '' ? null : Number(value))}
            disabled={save.isPending}
          >
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
