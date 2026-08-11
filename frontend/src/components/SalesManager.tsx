import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { EyeOff, ExternalLink, Eye, Receipt, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { ExclusionReason, ImportResult, MarketSale } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input, Select, Textarea } from '@/components/ui/field'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, LoadingPanel } from '@/components/ui/states'
import { formatDate, formatMoney, humanise } from '@/lib/utils'

/** Why a sale was set aside, in words rather than an enum value. */
const EXCLUSION_LABELS: Record<ExclusionReason, string> = {
  lot_or_bundle: 'Lot or bundle',
  damaged: 'Damaged',
  wrong_card: 'Wrong card',
  wrong_language: 'Wrong language',
  wrong_variant: 'Wrong variant',
  wrong_grade: 'Wrong grade',
  price_outlier: 'Price outlier',
  suspected_fake: 'Suspected fake',
  best_offer_unknown: 'Best offer — price unknown',
  user_excluded: 'You excluded it',
}

/**
 * Every comparable sale, including the ones that were filtered out.
 *
 * Excluded sales are shown rather than hidden because the filters are
 * heuristics reading listing titles, and a heuristic that quietly deletes a
 * real comparable is worse than one that shows its working. Each exclusion is
 * one click from being reversed.
 */
/** Rows shown before the list needs asking for. Enough to judge the sample by eye. */
const VISIBLE_SALES = 12

export function SalesList({
  cardId,
  currency,
  onChange,
}: {
  cardId: string
  currency: string
  onChange: () => void
}) {
  const [showExcluded, setShowExcluded] = useState(true)
  const [expanded, setExpanded] = useState(false)
  const sales = useQuery({
    queryKey: keys.sales(cardId),
    queryFn: () => api.cardSales(cardId),
  })

  const toggle = useMutation({
    mutationFn: (payload: { id: string; excluded: boolean }) =>
      api.setSaleExclusion(payload.id, payload.excluded),
    onSuccess: (sale) => {
      toast.success(sale.is_excluded ? 'Sale excluded' : 'Sale included')
      onChange()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the sale'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSale(id),
    onSuccess: () => {
      toast.success('Sale deleted')
      onChange()
    },
  })

  if (sales.isLoading) return <LoadingPanel label="Loading sales…" />

  const rows = sales.data ?? []
  const excluded = rows.filter((sale) => sale.is_excluded)
  const matching = showExcluded ? rows : rows.filter((sale) => !sale.is_excluded)
  const visible = expanded ? matching : matching.slice(0, VISIBLE_SALES)
  const hidden = matching.length - visible.length

  return (
    <Panel>
      <PanelHeader>
        <div>
          <PanelTitle>Comparable sales</PanelTitle>
          <PanelDescription>
            {rows.length
              ? `${rows.length - excluded.length} counted, ${excluded.length} excluded. ` +
                'Every exclusion is reversible — nothing is deleted by the filters.'
              : 'The evidence behind every number above.'}
          </PanelDescription>
        </div>
        {excluded.length ? (
          <Button size="sm" variant="ghost" onClick={() => setShowExcluded((value) => !value)}>
            {showExcluded ? <EyeOff /> : <Eye />}
            {showExcluded ? 'Hide excluded' : `Show ${excluded.length} excluded`}
          </Button>
        ) : null}
      </PanelHeader>

      {visible.length === 0 ? (
        <EmptyState
          icon={<Receipt className="size-8" />}
          title="No comparable sales yet"
          description="Add one by hand or import a CSV export of sold listings. Everything is computed from these — no sales, no valuation."
        />
      ) : (
        <PanelBody className="space-y-1.5">
          {visible.map((sale) => (
            <SaleRow
              key={sale.id}
              sale={sale}
              currency={currency}
              onToggle={() => toggle.mutate({ id: sale.id, excluded: !sale.is_excluded })}
              onDelete={() => {
                if (window.confirm('Delete this sale? To stop it counting, exclude it instead.')) {
                  remove.mutate(sale.id)
                }
              }}
            />
          ))}
          {hidden > 0 || expanded ? (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="w-full rounded-lg border border-dashed border-line py-2 text-xs text-ink-faint transition-colors hover:border-brand/40 hover:text-ink"
            >
              {hidden > 0 ? `Show ${hidden} more` : 'Show fewer'}
            </button>
          ) : null}
        </PanelBody>
      )}
    </Panel>
  )
}

function SaleRow({
  sale,
  currency,
  onToggle,
  onDelete,
}: {
  sale: MarketSale
  currency: string
  onToggle: () => void
  onDelete: () => void
}) {
  return (
    <div
      className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${
        sale.is_excluded ? 'border-dashed border-line bg-canvas' : 'border-line'
      }`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`truncate text-sm ${sale.is_excluded ? 'text-ink-faint' : 'text-ink'}`}>
            {sale.listing_title || `${sale.grade_label === 'raw' ? 'Raw' : sale.grade_label} sale`}
          </span>
          {sale.source_url ? (
            <a
              href={sale.source_url}
              target="_blank"
              rel="noreferrer"
              className="shrink-0 text-ink-faint hover:text-ink"
            >
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.7rem] text-ink-faint">
          <span>{formatDate(sale.sale_date)}</span>
          {sale.grade_label !== 'raw' ? <span>· {sale.grade_label}</span> : null}
          {sale.platform ? <span>· {sale.platform}</span> : null}
          {sale.lot_size > 1 ? <span>· lot of {sale.lot_size}</span> : null}
          {sale.exclusion_reason ? (
            <Badge tone={sale.excluded_by === 'user' ? 'neutral' : 'caution'}>
              {EXCLUSION_LABELS[sale.exclusion_reason] ?? humanise(sale.exclusion_reason)}
              {sale.excluded_by === 'user' ? ' (yours)' : ''}
            </Badge>
          ) : null}
        </div>
      </div>

      <span
        className={`tabular shrink-0 text-sm ${
          sale.is_excluded ? 'text-ink-faint line-through' : 'text-ink'
        }`}
      >
        {formatMoney(sale.sale_price, sale.currency || currency)}
      </span>

      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={onToggle}
          title={sale.is_excluded ? 'Include this sale' : 'Exclude this sale'}
          className="rounded p-1.5 text-ink-faint transition-colors hover:bg-surface-raised hover:text-ink"
        >
          {sale.is_excluded ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
        </button>
        <button
          type="button"
          onClick={onDelete}
          title="Delete this sale"
          className="rounded p-1.5 text-ink-faint transition-colors hover:bg-negative/10 hover:text-negative"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  )
}

export function AddSaleDialog({
  cardId,
  open,
  onOpenChange,
  currency,
  onSaved,
}: {
  cardId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  currency: string
  onSaved: () => void
}) {
  const today = new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({
    sale_date: today,
    sale_price: '',
    shipping: '',
    company_id: '',
    grade: '',
    platform: '',
    listing_title: '',
    source_url: '',
    apply_filters: true,
  })

  const companies = useQuery({
    queryKey: keys.companies,
    queryFn: () => api.listGradingCompanies(),
    enabled: open,
  })

  const create = useMutation({
    mutationFn: () =>
      api.createSale(cardId, {
        sale_date: form.sale_date,
        sale_price: Number(form.sale_price),
        shipping: form.shipping ? Number(form.shipping) : null,
        company_id: form.company_id || null,
        grade: form.company_id && form.grade ? Number(form.grade) : null,
        platform: form.platform || null,
        listing_title: form.listing_title || null,
        source_url: form.source_url || null,
        apply_filters: form.apply_filters,
      }),
    onSuccess: (sale) => {
      if (sale.is_excluded) {
        toast.warning(
          `Saved, but excluded: ${
            EXCLUSION_LABELS[sale.exclusion_reason!] ?? humanise(sale.exclusion_reason)
          }. Include it from the sales list if that is wrong.`,
        )
      } else {
        toast.success('Sale recorded')
      }
      setForm({ ...form, sale_price: '', listing_title: '', source_url: '', shipping: '' })
      onOpenChange(false)
      onSaved()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save the sale'),
  })

  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [key]: event.target.value }))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Record a sale"
        description="One completed sale of this card. Leave the grading company empty for a raw sale."
      >
        <div className="grid gap-4 px-5 py-4 sm:grid-cols-2">
          <Field label="Sale date">
            <Input type="date" value={form.sale_date} onChange={set('sale_date')} max={today} />
          </Field>
          <Field label={`Price paid (${currency})`}>
            <Input
              type="number"
              step="0.01"
              min="0.01"
              value={form.sale_price}
              onChange={set('sale_price')}
              placeholder="0.00"
            />
          </Field>
          <Field label="Graded by" hint="Empty for a raw sale.">
            <Select value={form.company_id} onChange={set('company_id')}>
              <option value="">Raw / ungraded</option>
              {(companies.data ?? []).map((company) => (
                <option key={company.id} value={company.id}>
                  {company.code}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Grade">
            <Input
              type="number"
              step="0.5"
              min="0"
              max="10"
              value={form.grade}
              onChange={set('grade')}
              disabled={!form.company_id}
              placeholder={form.company_id ? '10' : '—'}
            />
          </Field>
          <Field label="Platform" className="sm:col-span-1">
            <Input value={form.platform} onChange={set('platform')} placeholder="eBay" />
          </Field>
          <Field label={`Postage (${currency})`}>
            <Input
              type="number"
              step="0.01"
              min="0"
              value={form.shipping}
              onChange={set('shipping')}
              placeholder="0.00"
            />
          </Field>
          <Field
            label="Listing title"
            className="sm:col-span-2"
            hint="Pasting the real title lets the filters spot lots, damage and wrong languages."
          >
            <Textarea
              value={form.listing_title}
              onChange={set('listing_title')}
              className="min-h-16"
              placeholder="Pokemon Umbreon VMAX Alt Art 215/203 Evolving Skies NM"
            />
          </Field>
          <Field label="Link" className="sm:col-span-2">
            <Input value={form.source_url} onChange={set('source_url')} placeholder="https://…" />
          </Field>
          <label className="flex items-center gap-2 text-xs text-ink-muted sm:col-span-2">
            <input
              type="checkbox"
              checked={form.apply_filters}
              onChange={(event) =>
                setForm((current) => ({ ...current, apply_filters: event.target.checked }))
              }
              className="size-3.5 accent-[var(--color-brand)]"
            />
            Check this sale against the exclusion filters
          </label>
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => create.mutate()}
            disabled={create.isPending || !form.sale_price || !form.sale_date}
          >
            Save sale
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

const SAMPLE_CSV = `date,price,title,item id
2026-05-20,152.00,Umbreon VMAX Alt Art 215/203,111`

export function ImportSalesDialog({
  cardId,
  open,
  onOpenChange,
  onImported,
}: {
  cardId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: () => void
}) {
  const [csv, setCsv] = useState('')
  const [dayFirst, setDayFirst] = useState(true)
  const [result, setResult] = useState<ImportResult | null>(null)
  const queryClient = useQueryClient()

  const run = useMutation({
    mutationFn: () => api.importSales(cardId, csv, { day_first: dayFirst }),
    onSuccess: (data) => {
      setResult(data)
      queryClient.invalidateQueries({ queryKey: keys.sales(cardId) })
      onImported()
      if (data.imported || data.updated) {
        toast.success(`${data.imported} imported, ${data.updated} updated`)
      } else {
        toast.warning('Nothing imported — see the report below.')
      }
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Import failed'),
  })

  const readFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => setCsv(String(reader.result ?? ''))
    reader.readAsText(file)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) setResult(null)
      }}
    >
      <DialogContent
        title="Import sold listings"
        description="Paste a CSV or choose a file. Column names are matched loosely, so most marketplace exports work unchanged — a sale date and a price are all that is required."
      >
        <div className="space-y-4 px-5 py-4">
          <Field label="CSV file">
            <input
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) readFile(file)
              }}
              className="w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border file:border-line file:bg-surface-raised file:px-3 file:py-1.5 file:text-xs file:text-ink"
            />
          </Field>

          <Field
            label="Or paste it"
            hint={`Understood columns include date, price, shipping, title, platform, grade, company, seller, url and item id. Example:\n${SAMPLE_CSV}`}
          >
            <Textarea
              value={csv}
              onChange={(event) => setCsv(event.target.value)}
              className="min-h-40 font-mono text-xs"
              placeholder={SAMPLE_CSV}
            />
          </Field>

          <Field label="Date format" hint="How to read 03/04/2025. ISO dates are unaffected.">
            <Select
              value={dayFirst ? 'day' : 'month'}
              onChange={(event) => setDayFirst(event.target.value === 'day')}
            >
              <option value="day">Day first — 3 April (UK)</option>
              <option value="month">Month first — 4 March (US)</option>
            </Select>
          </Field>

          {result ? <ImportReport result={result} /> : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {result ? 'Done' : 'Cancel'}
          </Button>
          <Button onClick={() => run.mutate()} disabled={run.isPending || !csv.trim()}>
            Import
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function ImportReport({ result }: { result: ImportResult }) {
  const exclusions = Object.entries(result.exclusions)
  return (
    <div className="space-y-3 rounded-lg border border-line bg-canvas px-4 py-3">
      <div className="grid grid-cols-4 gap-3 text-center">
        <Tally label="Imported" value={result.imported} />
        <Tally label="Updated" value={result.updated} />
        <Tally label="Excluded" value={result.excluded} />
        <Tally label="Outliers" value={result.outliers_flagged} />
      </div>

      {exclusions.length ? (
        <div className="flex flex-wrap gap-1.5 border-t border-line pt-2.5">
          {exclusions.map(([reason, count]) => (
            <Badge key={reason} tone="caution">
              {EXCLUSION_LABELS[reason as ExclusionReason] ?? humanise(reason)} × {count}
            </Badge>
          ))}
        </div>
      ) : null}

      {result.errors.length ? (
        <div className="border-t border-line pt-2.5">
          <p className="text-xs font-medium text-negative">
            {result.errors.length} row{result.errors.length === 1 ? '' : 's'} could not be read
          </p>
          <ul className="mt-1 space-y-0.5 text-[0.7rem] text-ink-faint">
            {result.errors.slice(0, 6).map((error, index) => (
              <li key={`${error.line_number}-${index}`}>
                {error.line_number ? `Line ${error.line_number}: ` : ''}
                {error.message}
              </li>
            ))}
            {result.errors.length > 6 ? <li>…and {result.errors.length - 6} more.</li> : null}
          </ul>
        </div>
      ) : null}

      {result.excluded ? (
        <p className="border-t border-line pt-2.5 text-[0.7rem] leading-relaxed text-ink-faint">
          Excluded sales are kept and listed on the card. If a filter got one wrong, include it
          again from the sales list.
        </p>
      ) : null}
    </div>
  )
}

function Tally({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="tabular text-lg font-semibold text-ink">{value}</p>
      <p className="text-[0.65rem] uppercase tracking-wide text-ink-faint">{label}</p>
    </div>
  )
}
