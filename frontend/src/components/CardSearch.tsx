import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Keyboard, Search, WifiOff } from 'lucide-react'
import { api, keys } from '@/lib/api'
import type { CardMatch, CardWrite } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/field'
import { Skeleton } from '@/components/ui/states'

/**
 * Find the card first, then add it.
 *
 * Typing a name, a set code, a number, a rarity and a printing correctly — for
 * a card that a catalogue already describes perfectly — is work nobody should
 * do. So this comes before the form: search, pick, and everything is filled in,
 * including the provider's own id, which means the card is linked for price
 * syncing from the moment it exists rather than needing a second pass later.
 *
 * Manual entry is always one click away and never blocked. The catalogue is
 * English-only and misses plenty — promos, misprints, anything very new — and a
 * card you cannot find is still a card you own.
 */
export function CardSearch({
  onPick,
  onSkip,
}: {
  onPick: (values: Partial<CardWrite>, match: CardMatch) => void
  onSkip: () => void
}) {
  const [term, setTerm] = useState('')
  const [submitted, setSubmitted] = useState('')

  const params = { name: submitted || undefined }
  const lookup = useQuery({
    queryKey: keys.catalogLookup(params),
    queryFn: () => api.lookupCard(params),
    enabled: Boolean(submitted),
  })

  const search = () => setSubmitted(term.trim())

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1.5 block text-xs font-medium text-ink-muted" htmlFor="card-search">
          Search the card catalogue
        </label>
        <div className="flex gap-2">
          <Input
            id="card-search"
            value={term}
            autoFocus
            placeholder="Umbreon VMAX"
            onChange={(event) => setTerm(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                search()
              }
            }}
          />
          <Button type="button" variant="primary" onClick={search} disabled={!term.trim()}>
            <Search className="size-3.5" /> Search
          </Button>
        </div>
        <p className="pt-1.5 text-[0.7rem] text-ink-faint">
          Picking a result fills in the set, number and rarity, and links the card for price
          updates.
        </p>
      </div>

      {lookup.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-14 w-full" />
          ))}
        </div>
      ) : null}

      {lookup.data?.status === 'unavailable' ? (
        <div className="flex items-start gap-2 rounded-lg border border-line px-3 py-2.5 text-xs leading-relaxed text-ink-muted">
          <WifiOff className="mt-0.5 size-4 shrink-0" />
          <span>
            {lookup.data.reason ?? 'No catalogue source is available.'} You can still add the card
            by hand.
          </span>
        </div>
      ) : null}

      {lookup.data?.status === 'insufficient_data' ? (
        <div className="rounded-lg border border-line px-3 py-2.5 text-xs leading-relaxed text-ink-muted">
          Nothing matched “{submitted}”. Try fewer words, or add it by hand — the catalogue is
          English-only and does not have everything.
        </div>
      ) : null}

      {lookup.isError ? (
        <div className="rounded-lg border border-caution/30 bg-caution/10 px-3 py-2.5 text-xs leading-relaxed text-caution">
          The catalogue could not be reached. Add the card by hand and link it later from its
          Market panel.
        </div>
      ) : null}

      {lookup.data?.matches.length ? (
        <div className="max-h-80 space-y-1.5 overflow-y-auto">
          {lookup.data.matches.map((match) => (
            <button
              key={match.external_id}
              type="button"
              onClick={() => onPick(toValues(match), match)}
              className="flex w-full items-center gap-3 rounded-lg border border-line px-3 py-2.5 text-left transition-colors hover:border-brand/50 hover:bg-canvas"
            >
              {match.image_url ? (
                <img
                  src={match.image_url}
                  alt=""
                  className="h-14 w-10 shrink-0 rounded object-cover"
                  loading="lazy"
                />
              ) : (
                <span className="h-14 w-10 shrink-0 rounded bg-surface-raised" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-ink">{match.name}</span>
                <span className="block truncate text-xs text-ink-faint">
                  {match.set_name ?? 'Unknown set'}
                  {match.card_number ? ` · ${match.card_number}` : ''}
                  {match.rarity ? ` · ${match.rarity}` : ''}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between border-t border-line pt-3">
        <p className="text-[0.7rem] text-ink-faint">Not in the catalogue?</p>
        <Button type="button" variant="secondary" size="sm" onClick={onSkip}>
          <Keyboard className="size-3.5" /> Enter it by hand
        </Button>
      </div>
    </div>
  )
}

/** What a catalogue match contributes to a new card. */
function toValues(match: CardMatch): Partial<CardWrite> {
  return {
    name: match.name,
    set_code: match.set_code,
    set_name: match.set_name,
    card_number: match.card_number,
    rarity: match.rarity,
    // The catalogue is English-only, so this is a fact rather than a guess.
    language: match.language ?? 'English',
  }
}
