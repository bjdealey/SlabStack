import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCheck, Link2, Search } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { Card, CardMatch } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn } from '@/lib/utils'

/** Which catalogue values to accept. Anything unticked is left as you had it. */
const FIELDS = [
  { key: 'set_code', label: 'Set code' },
  { key: 'set_name', label: 'Set name' },
  { key: 'card_number', label: 'Card number' },
  { key: 'rarity', label: 'Rarity' },
] as const

/**
 * Find this card in a provider's catalogue, and link it.
 *
 * The link is the point, not the metadata: once a card is matched, every future
 * price sync asks for it by the provider's own id instead of re-searching by
 * name and risking a different printing.
 *
 * Nothing is applied without being confirmed. A confident API silently
 * rewriting somebody's card is the exact failure the provider abstraction was
 * shaped to prevent, so this shows candidates and waits.
 */
export function CatalogLookup({ card }: { card: Card }) {
  const [open, setOpen] = useState(false)
  const links = (card.external_ids ?? {}) as Record<string, string>
  const anyLink = Object.keys(links).length > 0

  return (
    <>
      <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
        <Search className="size-3.5" />
        {anyLink ? 'Re-link to a source' : 'Find in catalogue'}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-w-3xl"
          title="Find this card at a provider"
          description="Candidates from the provider. Nothing is changed until you pick one."
        >
          <LookupBody card={card} links={links} onDone={() => setOpen(false)} />
        </DialogContent>
      </Dialog>
    </>
  )
}

function LookupBody({
  card,
  links,
  onDone,
}: {
  card: Card
  links: Record<string, string>
  onDone: () => void
}) {
  const queryClient = useQueryClient()
  const [accept, setAccept] = useState<string[]>(['set_code', 'set_name', 'rarity'])
  const [source, setSource] = useState('pokemontcg_io')

  // A card is linked per source, not once. Each provider has its own ids, and
  // a price only syncs from a source that has been told which card this is —
  // so a card linked to the catalogue and not to PriceCharting gets a raw
  // price from one and nothing from the other.
  const sources = useQuery({ queryKey: keys.dataSources, queryFn: api.listDataSources })
  const searchable = (sources.data ?? []).filter(
    (row) => row.enabled && row.has_adapter && !['manual', 'csv'].includes(row.code),
  )
  const linked = links[source]

  const params = {
    name: card.name,
    set_code: card.set_code ?? undefined,
    card_number: card.card_number ?? undefined,
    source_code: source,
  }
  const lookup = useQuery({
    queryKey: keys.catalogLookup(params),
    queryFn: () => api.lookupCard(params),
  })

  const link = useMutation({
    mutationFn: (match: CardMatch) =>
      api.linkCard(card.id, {
        external_id: match.external_id,
        source_code: source,
        apply_fields: accept,
        set_code: match.set_code,
        set_name: match.set_name,
        card_number: match.card_number,
        rarity: match.rarity,
      }),
    onSuccess: (result) => {
      toast.success(
        result.applied_fields.length
          ? `Linked, and took ${result.applied_fields.length} field(s) from the catalogue`
          : 'Linked. Nothing else was changed.',
      )
      queryClient.invalidateQueries()
      onDone()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not link that card'),
  })

  return (
    <Panel className="border-0">
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Why this matters</PanelTitle>
          <PanelDescription>
            Linking stores the provider's own id, so future price refreshes ask for this exact
            card instead of searching by name and risking a different printing.
          </PanelDescription>
        </div>
        {linked ? <Badge tone="brand">Already linked</Badge> : null}
      </PanelHeader>

      <PanelBody className="space-y-4">
        {searchable.length > 1 ? (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2">
            <span className="text-xs text-ink-faint">Search:</span>
            {searchable.map((row) => (
              <button
                key={row.code}
                type="button"
                onClick={() => setSource(row.code)}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-xs transition-colors',
                  row.code === source
                    ? 'border-brand bg-brand/10 text-brand'
                    : 'border-line text-ink-muted hover:border-brand/50',
                )}
              >
                {row.name}
                {links[row.code] ? ' ·  linked' : ''}
              </button>
            ))}
          </div>
        ) : null}

        {lookup.isError ? <ErrorState error={lookup.error} /> : null}
        {lookup.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-14 w-full" />
            ))}
          </div>
        ) : null}

        {lookup.data && lookup.data.status === 'unavailable' ? (
          <EmptyState
            icon={<Link2 className="size-8" />}
            title="No catalogue source is enabled"
            description={
              lookup.data.reason ??
              'Enable one in Settings → Data sources. Nothing here reaches the internet until you do.'
            }
          />
        ) : null}

        {lookup.data && lookup.data.status === 'insufficient_data' ? (
          <EmptyState
            icon={<Search className="size-8" />}
            title="Nothing matched"
            description={lookup.data.reason ?? 'Try a broader search.'}
          />
        ) : null}

        {lookup.data?.matches.length ? (
          <>
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2">
              <span className="text-xs text-ink-faint">Take from the catalogue:</span>
              {FIELDS.map((field) => (
                <button
                  key={field.key}
                  type="button"
                  onClick={() =>
                    setAccept((current) =>
                      current.includes(field.key)
                        ? current.filter((item) => item !== field.key)
                        : [...current, field.key],
                    )
                  }
                  className={cn(
                    'rounded-md border px-2 py-1 text-xs transition-colors',
                    accept.includes(field.key)
                      ? 'border-brand/40 bg-brand/10 text-brand'
                      : 'border-line text-ink-faint hover:text-ink',
                  )}
                >
                  {accept.includes(field.key) ? '✓ ' : ''}
                  {field.label}
                </button>
              ))}
            </div>

            <div className="space-y-1.5">
              {lookup.data.matches.map((match) => (
                <button
                  key={match.external_id}
                  type="button"
                  disabled={link.isPending}
                  onClick={() => link.mutate(match)}
                  className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line px-3 py-2.5 text-left transition-colors hover:bg-canvas disabled:opacity-60"
                >
                  {match.image_url ? (
                    <img
                      src={match.image_url}
                      alt=""
                      className="h-14 w-10 shrink-0 rounded object-cover"
                      loading="lazy"
                    />
                  ) : null}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">
                      {match.name}
                    </span>
                    <span className="block truncate text-xs text-ink-faint">
                      {match.set_name ?? 'Unknown set'}
                      {match.card_number ? ` · ${match.card_number}` : ''}
                      {match.rarity ? ` · ${match.rarity}` : ''}
                    </span>
                  </span>
                  {linked === match.external_id ? (
                    <CheckCheck className="size-4 shrink-0 text-brand" />
                  ) : null}
                  <span className="tabular w-12 shrink-0 text-right text-xs text-ink-muted">
                    {Math.round(match.confidence * 100)}%
                  </span>
                </button>
              ))}
            </div>

            <p className="text-[0.7rem] leading-relaxed text-ink-faint">
              The percentage is how well a candidate matches what you already recorded — for
              ordering these, nothing more. Pick the one that is actually your card.
            </p>
          </>
        ) : null}
      </PanelBody>
    </Panel>
  )
}
