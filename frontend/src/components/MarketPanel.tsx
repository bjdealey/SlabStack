import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Minus,
  Plus,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  Upload,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type {
  Card,
  CardEvaluation,
  MarketPrice,
  MarketSummary,
  MarketValueRow,
} from '@/lib/types'
import { CatalogLookup } from '@/components/CatalogLookup'
import { ConfidenceBadge, StatusBadge } from '@/components/DecisionBadge'
import { AddSaleDialog, ImportSalesDialog } from '@/components/SalesManager'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input, Textarea } from '@/components/ui/field'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { formatDate, formatMoney, formatPercent, humanise } from '@/lib/utils'

const BAND_TONES: Record<string, 'positive' | 'caution' | 'negative' | 'neutral'> = {
  very_liquid: 'positive',
  liquid: 'positive',
  moderate: 'caution',
  illiquid: 'negative',
  very_illiquid: 'negative',
  unknown: 'neutral',
}

const DIRECTION_TONES: Record<string, 'positive' | 'caution' | 'negative' | 'neutral'> = {
  strong_up: 'positive',
  up: 'positive',
  stable: 'neutral',
  down: 'negative',
  strong_down: 'negative',
  insufficient_data: 'neutral',
}

/**
 * The market half of the card page.
 *
 * Every figure here is shown with the evidence behind it — how many sales, over
 * what window, how recently, at what confidence — because "£152" from thirty
 * sales in ninety days and "£152" from two sales in nine months are not the
 * same claim and should not look the same (spec section 36).
 */
export function MarketPanel({
  cardId,
  evaluation,
  card,
}: {
  cardId: string
  evaluation: CardEvaluation
  card?: Card
}) {
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState(false)
  const [overriding, setOverriding] = useState<MarketPrice | null>(null)

  const market = useQuery({
    queryKey: keys.market(cardId),
    queryFn: () => api.cardMarket(cardId),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: keys.market(cardId) })
    queryClient.invalidateQueries({ queryKey: keys.sales(cardId) })
    queryClient.invalidateQueries({ queryKey: keys.marketHistory(cardId) })
    queryClient.invalidateQueries({ queryKey: ['evaluation', cardId] })
    queryClient.invalidateQueries({ queryKey: keys.summary })
  }

  const recompute = useMutation({
    mutationFn: () => api.recomputeMarket(cardId),
    onSuccess: (summary: MarketSummary) => {
      toast.success(
        summary.prices.length
          ? `Repriced from ${summary.sale_count} sale${summary.sale_count === 1 ? '' : 's'}`
          : 'Nothing to price yet',
      )
      refresh()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not recompute'),
  })

  const currency = evaluation.currency
  const summary = market.data
  const block = evaluation.market
  const liquidity = evaluation.liquidity
  const trend = evaluation.trend

  return (
    <>
      <Panel>
        <PanelHeader>
          <div>
            <PanelTitle>Market</PanelTitle>
            <PanelDescription>
              {block.reason ??
                (summary?.computed_at
                  ? `Computed from your sales, last on ${formatDate(summary.computed_at)}.`
                  : 'Raw and graded values, with their evidence.')}
            </PanelDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <StatusBadge status={block.status} phase={block.phase} />
            {card ? <CatalogLookup card={card} /> : null}
            <Button size="sm" variant="ghost" onClick={() => setImporting(true)}>
              <Upload /> Import
            </Button>
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus /> Sale
            </Button>
          </div>
        </PanelHeader>

        <PanelBody className="space-y-5">
          {block.raw || block.graded.length ? (
            <div className="space-y-2">
              {block.raw ? (
                <ValueRow
                  row={block.raw}
                  currency={currency}
                  price={findPrice(summary, block.raw.grade_label)}
                  onOverride={setOverriding}
                />
              ) : null}
              {block.graded.map((row) => (
                <ValueRow
                  key={row.grade_label}
                  row={row}
                  currency={currency}
                  price={findPrice(summary, row.grade_label)}
                  onOverride={setOverriding}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-line bg-canvas px-4 py-6 text-center">
              <p className="text-sm text-ink-muted">No valuation yet.</p>
              <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-ink-faint">
                {block.reason}
              </p>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-line px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                  <Activity className="size-3.5" /> Liquidity
                </p>
                <Badge tone={BAND_TONES[liquidity.band] ?? 'neutral'}>
                  {humanise(liquidity.band)}
                </Badge>
              </div>
              {liquidity.score === null ? (
                <p className="mt-2 text-xs leading-relaxed text-ink-faint">{liquidity.reason}</p>
              ) : (
                <>
                  <p className="tabular mt-1 text-2xl font-semibold leading-none text-ink">
                    {liquidity.score.toFixed(1)}
                    <span className="text-sm font-normal text-ink-faint">/10</span>
                  </p>
                  <div className="mt-2.5 grid grid-cols-4 gap-2 text-center">
                    <Count label="7d" value={liquidity.sales_7d ?? 0} />
                    <Count label="30d" value={liquidity.sales_30d ?? 0} />
                    <Count label="90d" value={liquidity.sales_90d ?? 0} />
                    <Count label="1y" value={liquidity.sales_365d ?? 0} />
                  </div>
                  <p className="mt-2.5 border-t border-line pt-2 text-[0.7rem] leading-relaxed text-ink-faint">
                    {liquidity.days_since_last_sale !== null
                      ? `Last sale ${liquidity.days_since_last_sale} days ago`
                      : 'No sale date'}
                    {liquidity.median_days_between_sales
                      ? `, typically ${Math.round(liquidity.median_days_between_sales)} days apart`
                      : ''}
                    {liquidity.active_listings
                      ? `. ${liquidity.active_listings} listed unsold`
                      : ''}
                    . Across every grade — a card whose slabs rarely appear but whose raw copies
                    sell weekly still trades.
                  </p>
                </>
              )}
            </div>

            <div className="rounded-lg border border-line px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                  <TrendIcon direction={trend.direction} /> Trend
                </p>
                <Badge tone={DIRECTION_TONES[trend.direction] ?? 'neutral'}>
                  {humanise(trend.direction)}
                </Badge>
              </div>
              {trend.direction === 'insufficient_data' ? (
                <p className="mt-2 text-xs leading-relaxed text-ink-faint">{trend.reason}</p>
              ) : (
                <>
                  <div className="mt-2 grid grid-cols-5 gap-1 text-center">
                    <Change label="7d" value={trend.change_7d_pct} />
                    <Change label="30d" value={trend.change_30d_pct} />
                    <Change label="90d" value={trend.change_90d_pct} />
                    <Change label="180d" value={trend.change_180d_pct} />
                    <Change label="1y" value={trend.change_365d_pct} />
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2">
                    <span className="text-[0.7rem] text-ink-faint">
                      {trend.sample_size}{' '}
                      {trend.grade_label === 'raw' ? 'raw' : (trend.grade_label ?? '')} sale
                      {trend.sample_size === 1 ? '' : 's'}
                    </span>
                    <ConfidenceBadge confidence={trend.confidence} />
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
            <p className="text-[0.7rem] text-ink-faint">
              {summary
                ? `${summary.sale_count} sale${summary.sale_count === 1 ? '' : 's'} counted` +
                  (summary.excluded_count ? `, ${summary.excluded_count} excluded` : '')
                : ''}
              {block.sources.length ? ` · ${block.sources.join(', ')}` : ''}
            </p>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => recompute.mutate()}
              disabled={recompute.isPending}
            >
              <RefreshCw className={recompute.isPending ? 'animate-spin' : undefined} /> Recompute
            </Button>
          </div>
        </PanelBody>
      </Panel>

      <AddSaleDialog
        cardId={cardId}
        open={adding}
        onOpenChange={setAdding}
        currency={currency}
        onSaved={refresh}
      />
      <ImportSalesDialog
        cardId={cardId}
        open={importing}
        onOpenChange={setImporting}
        onImported={refresh}
      />
      <OverrideDialog
        price={overriding}
        currency={currency}
        onClose={() => setOverriding(null)}
        onSaved={refresh}
      />
    </>
  )
}

function findPrice(summary: MarketSummary | undefined, label: string): MarketPrice | undefined {
  return summary?.prices.find((price) => price.grade_label === label)
}

function TrendIcon({ direction }: { direction: string }) {
  if (direction.includes('up')) return <TrendingUp className="size-3.5" />
  if (direction.includes('down')) return <TrendingDown className="size-3.5" />
  return <Minus className="size-3.5" />
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="tabular text-sm font-medium text-ink">{value}</p>
      <p className="text-[0.65rem] uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  )
}

function Change({ label, value }: { label: string; value: number | null }) {
  const tone =
    value === null ? 'text-ink-faint' : value > 2 ? 'text-positive' : value < -2 ? 'text-negative' : 'text-ink'
  return (
    <div>
      <p className={`tabular text-xs font-medium ${tone}`}>
        {value === null ? '—' : formatPercent(value, 0)}
      </p>
      <p className="text-[0.65rem] uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  )
}

/** One grade's valuation, with everything needed to judge how much to trust it. */
function ValueRow({
  row,
  currency,
  price,
  onOverride,
}: {
  row: MarketValueRow
  currency: string
  price: MarketPrice | undefined
  onOverride: (price: MarketPrice) => void
}) {
  const evidence = [
    `${row.sample_size} sale${row.sample_size === 1 ? '' : 's'}`,
    row.window_days ? `${row.window_days}d window` : null,
    row.last_sale_at ? `last ${formatDate(row.last_sale_at)}` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="rounded-lg border border-line px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink">
            {row.grade_label === 'raw' ? 'Raw' : row.grade_label}
          </span>
          {row.premium_vs_raw_pct !== null ? (
            <Badge tone={row.premium_vs_raw_pct > 0 ? 'positive' : 'neutral'}>
              {formatPercent(row.premium_vs_raw_pct, 0)} vs raw
            </Badge>
          ) : null}
          {row.is_user_override ? <Badge tone="brand">Your value</Badge> : null}
        </div>
        <div className="flex items-center gap-2">
          <ConfidenceBadge confidence={row.confidence} />
          {price ? (
            <button
              type="button"
              onClick={() => onOverride(price)}
              className="text-[0.7rem] text-ink-faint underline-offset-2 hover:text-ink hover:underline"
            >
              Set yours
            </button>
          ) : null}
        </div>
      </div>

      <div className="mt-2.5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Figure label="Realistic" value={formatMoney(row.realistic_sale, currency)} emphasis />
        <Figure label="Quick sale" value={formatMoney(row.quick_sale, currency)} />
        <Figure label="Median" value={formatMoney(row.median, currency)} />
        <Figure
          label="Range"
          value={
            row.low_quartile === null || row.high_quartile === null
              ? '—'
              : `${formatMoney(row.low_quartile, currency)}–${formatMoney(row.high_quartile, currency)}`
          }
        />
      </div>

      <p className="mt-2 border-t border-line pt-2 text-[0.7rem] text-ink-faint">{evidence}</p>
    </div>
  )
}

function Figure({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div>
      <p className="text-[0.65rem] uppercase tracking-wider text-ink-faint">{label}</p>
      <p
        className={`tabular mt-0.5 font-semibold ${
          emphasis ? 'text-base text-brand' : 'text-sm text-ink'
        }`}
      >
        {value}
      </p>
    </div>
  )
}

function OverrideDialog({
  price,
  currency,
  onClose,
  onSaved,
}: {
  price: MarketPrice | null
  currency: string
  onClose: () => void
  onSaved: () => void
}) {
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')

  const save = useMutation({
    mutationFn: (payload: { value: number | null; note: string }) =>
      api.overridePrice(price!.id, payload.value, payload.note || null),
    onSuccess: () => {
      toast.success('Your value saved')
      onSaved()
      onClose()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save'),
  })

  return (
    <Dialog
      open={price !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
        else {
          setValue(price?.user_value !== null && price ? String(price.user_value) : '')
          setNote(price?.user_value_note ?? '')
        }
      }}
    >
      <DialogContent
        title={`Your value for ${price?.grade_label ?? ''}`}
        description="Stored alongside the computed figure, not over it. Both stay visible, and clearing yours brings the computed one straight back."
      >
        <div className="space-y-4 px-5 py-4">
          <div className="rounded-lg border border-line bg-canvas px-4 py-3 text-xs text-ink-muted">
            SlabStack computes{' '}
            <span className="tabular font-medium text-ink">
              {formatMoney(price?.realistic_sale ?? null, currency)}
            </span>{' '}
            from {price?.sample_size ?? 0} sale{price?.sample_size === 1 ? '' : 's'} at{' '}
            {price?.confidence ?? 'no'} confidence.
          </div>
          <Field label={`Your value (${currency})`} hint="Leave empty to clear the override.">
            <Input
              type="number"
              step="0.01"
              min="0"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder="0.00"
            />
          </Field>
          <Field label="Why?" hint="Optional, but future you will want to know.">
            <Textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Signed copy — sells higher than the plain card."
            />
          </Field>
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() =>
              save.mutate({ value: value.trim() === '' ? null : Number(value), note })
            }
            disabled={save.isPending}
          >
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
