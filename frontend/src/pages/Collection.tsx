import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, ClipboardCheck, ImageOff, LayoutGrid, List, Search, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys, type CardListParams } from '@/lib/api'
import type { Card, CardWrite } from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { CardForm } from '@/components/CardForm'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input, Select } from '@/components/ui/field'
import { Panel } from '@/components/ui/panel'
import { EmptyState, ErrorState, LoadingPanel } from '@/components/ui/states'
import { cardSubtitle, cardTitle, cn, formatMoney, formatNumber } from '@/lib/utils'

const PAGE_SIZE = 24

export function Collection() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()

  const [search, setSearch] = useState(params.get('q') ?? '')
  const [debounced, setDebounced] = useState(search)
  const [view, setView] = useState<'grid' | 'table'>('grid')
  const [page, setPage] = useState(1)
  const [adding, setAdding] = useState(params.get('new') === '1')

  // Typing in a search box should not fire a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 250)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => setPage(1), [debounced, params])

  const filters = useMemo<CardListParams>(() => {
    const value = (key: string) => params.get(key) ?? undefined
    const boolValue = (key: string) => {
      const raw = params.get(key)
      return raw === null ? undefined : raw === 'true'
    }
    return {
      q: debounced || undefined,
      set_code: value('set_code'),
      language: value('language'),
      variant: value('variant'),
      has_images: boolValue('has_images'),
      has_condition: boolValue('has_condition'),
      sort: value('sort') ?? 'created_at',
      order: (value('order') as 'asc' | 'desc') ?? 'desc',
      page,
      page_size: PAGE_SIZE,
    }
  }, [params, debounced, page])

  const cards = useQuery({ queryKey: keys.cards(filters), queryFn: () => api.listCards(filters) })
  const facets = useQuery({ queryKey: keys.facets, queryFn: api.facets })

  const create = useMutation({
    mutationFn: (payload: CardWrite) => api.createCard(payload),
    onSuccess: (card) => {
      toast.success(`${card.name} added`)
      setAdding(false)
      queryClient.invalidateQueries({ queryKey: ['cards'] })
      queryClient.invalidateQueries({ queryKey: keys.summary })
      queryClient.invalidateQueries({ queryKey: keys.facets })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not add the card'),
  })

  const setFilter = (key: string, value: string | undefined) => {
    const next = new URLSearchParams(params)
    if (value === undefined || value === '') next.delete(key)
    else next.set(key, value)
    next.delete('new')
    setParams(next, { replace: true })
  }

  const activeFilters = ['set_code', 'language', 'variant', 'has_images', 'has_condition'].filter(
    (key) => params.get(key) !== null,
  )

  return (
    <>
      <PageHeader
        title="Collection"
        description={
          cards.data
            ? `${formatNumber(cards.data.total)} card${cards.data.total === 1 ? '' : 's'} matching`
            : undefined
        }
        actions={
          <>
            <div className="flex rounded-lg border border-line p-0.5">
              <button
                onClick={() => setView('grid')}
                className={cn(
                  'rounded-md px-2 py-1',
                  view === 'grid' ? 'bg-surface-raised text-ink' : 'text-ink-faint',
                )}
                title="Grid"
              >
                <LayoutGrid className="size-4" />
              </button>
              <button
                onClick={() => setView('table')}
                className={cn(
                  'rounded-md px-2 py-1',
                  view === 'table' ? 'bg-surface-raised text-ink' : 'text-ink-faint',
                )}
                title="Table"
              >
                <List className="size-4" />
              </button>
            </div>
            <Button variant="primary" onClick={() => setAdding(true)}>
              Add a card
            </Button>
          </>
        }
      />

      <div className="space-y-4 p-6">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-64 flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, set, number or notes…"
              className="pl-9"
            />
          </div>

          <Select
            value={params.get('set_code') ?? ''}
            onChange={(event) => setFilter('set_code', event.target.value)}
            className="w-auto min-w-32"
          >
            <option value="">All sets</option>
            {(facets.data?.sets ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>

          <Select
            value={params.get('language') ?? ''}
            onChange={(event) => setFilter('language', event.target.value)}
            className="w-auto min-w-32"
          >
            <option value="">All languages</option>
            {(facets.data?.languages ?? []).map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>

          <Select
            value={params.get('sort') ?? 'created_at'}
            onChange={(event) => setFilter('sort', event.target.value)}
            className="w-auto"
          >
            <option value="created_at">Recently added</option>
            <option value="name">Name</option>
            <option value="set_code">Set</option>
            <option value="purchase_price">Purchase price</option>
            <option value="release_date">Release date</option>
          </Select>

          <Button
            size="sm"
            variant={params.get('has_images') === 'false' ? 'primary' : 'secondary'}
            onClick={() =>
              setFilter('has_images', params.get('has_images') === 'false' ? undefined : 'false')
            }
          >
            <ImageOff /> No photos
          </Button>
          <Button
            size="sm"
            variant={params.get('has_condition') === 'false' ? 'primary' : 'secondary'}
            onClick={() =>
              setFilter(
                'has_condition',
                params.get('has_condition') === 'false' ? undefined : 'false',
              )
            }
          >
            <ClipboardCheck /> Unassessed
          </Button>

          {activeFilters.length ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setParams(search ? new URLSearchParams({ q: search }) : new URLSearchParams())}
            >
              <X /> Clear
            </Button>
          ) : null}
        </div>

        {cards.isLoading || !cards.data ? (
          <LoadingPanel />
        ) : cards.isError ? (
          <ErrorState error={cards.error} />
        ) : cards.data.items.length === 0 ? (
          <Panel>
            <EmptyState
              title={debounced || activeFilters.length ? 'Nothing matches those filters' : 'No cards yet'}
              description={
                debounced || activeFilters.length
                  ? 'Try a broader search, or clear the filters.'
                  : 'Add your first card to get started.'
              }
              action={
                <Button variant="primary" onClick={() => setAdding(true)}>
                  Add a card
                </Button>
              }
            />
          </Panel>
        ) : view === 'grid' ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
            {cards.data.items.map((card) => (
              <CardTile key={card.id} card={card} />
            ))}
          </div>
        ) : (
          <CardTable cards={cards.data.items} />
        )}

        {cards.data && cards.data.total_pages > 1 ? (
          <div className="flex items-center justify-between border-t border-line pt-4 text-sm">
            <p className="text-ink-faint">
              Page {cards.data.page} of {cards.data.total_pages}
            </p>
            <div className="flex gap-2">
              <Button size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                Previous
              </Button>
              <Button
                size="sm"
                disabled={page >= cards.data.total_pages}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        ) : null}
      </div>

      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent
          title="Add a card"
          description="Only the name is required — everything else can be filled in later."
        >
          <CardForm
            onSubmit={(payload) => create.mutate(payload)}
            onCancel={() => setAdding(false)}
            submitting={create.isPending}
          />
        </DialogContent>
      </Dialog>
    </>
  )
}

function CardTile({ card }: { card: Card }) {
  return (
    <Link
      to={`/cards/${card.id}`}
      className="group flex flex-col overflow-hidden rounded-[var(--radius-card)] border border-line bg-surface transition-colors hover:border-brand/50"
    >
      <div className="relative flex aspect-[5/7] items-center justify-center bg-canvas">
        {card.primary_image_url ? (
          <img
            src={card.primary_image_url}
            alt={card.name}
            className="size-full object-contain"
            loading="lazy"
          />
        ) : (
          <Camera className="size-6 text-ink-faint" />
        )}
        {card.quantity > 1 ? (
          <span className="absolute right-2 top-2 rounded-full bg-canvas/90 px-2 py-0.5 text-xs text-ink">
            ×{card.quantity}
          </span>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <p className="truncate text-sm font-medium text-ink group-hover:text-brand">
          {cardTitle(card)}
        </p>
        <p className="truncate text-xs text-ink-faint">{cardSubtitle(card) || '—'}</p>
        <div className="mt-auto flex items-center justify-between gap-2 pt-2">
          <span className="tabular text-sm text-ink">
            {formatMoney(card.user_raw_value ?? card.purchase_price)}
          </span>
          {card.has_condition_assessment ? (
            <Badge tone="positive">Assessed</Badge>
          ) : (
            <Badge tone="outline">Unassessed</Badge>
          )}
        </div>
      </div>
    </Link>
  )
}

function CardTable({ cards }: { cards: Card[] }) {
  return (
    <Panel className="overflow-x-auto">
      <table className="w-full min-w-3xl text-sm">
        <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-faint">
          <tr>
            <th className="px-4 py-3 font-medium">Card</th>
            <th className="px-4 py-3 font-medium">Set</th>
            <th className="px-4 py-3 font-medium">Variant</th>
            <th className="px-4 py-3 text-right font-medium">Qty</th>
            <th className="px-4 py-3 text-right font-medium">Value</th>
            <th className="px-4 py-3 font-medium">Condition</th>
          </tr>
        </thead>
        <tbody>
          {cards.map((card) => (
            <tr key={card.id} className="border-b border-line/60 last:border-0 hover:bg-surface-raised/40">
              <td className="px-4 py-2.5">
                <Link to={`/cards/${card.id}`} className="font-medium text-ink hover:text-brand">
                  {cardTitle(card)}
                </Link>
              </td>
              <td className="px-4 py-2.5 text-ink-muted">{card.set_name ?? card.set_code ?? '—'}</td>
              <td className="px-4 py-2.5 text-ink-muted">{card.variant ?? '—'}</td>
              <td className="tabular px-4 py-2.5 text-right text-ink-muted">{card.quantity}</td>
              <td className="tabular px-4 py-2.5 text-right text-ink">
                {formatMoney(card.user_raw_value ?? card.purchase_price)}
              </td>
              <td className="px-4 py-2.5">
                {card.has_condition_assessment ? (
                  <Badge tone="positive">Assessed</Badge>
                ) : (
                  <Badge tone="outline">Unassessed</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  )
}
