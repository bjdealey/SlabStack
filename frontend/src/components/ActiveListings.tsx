import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink, Gavel, ShoppingBag, TriangleAlert } from 'lucide-react'
import { api, keys } from '@/lib/api'
import type { Listing, MarketBlock } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, LoadingPanel } from '@/components/ui/states'
import { formatMoney, humanise, relativeTime, timeUntil } from '@/lib/utils'

/** Rows per grade before the rest need asking for. The cheapest are the ones that matter. */
const VISIBLE = 6

/** Beyond this, a fetch describes a shop window that has probably changed. */
const STALE_AFTER_DAYS = 7

/**
 * What is on sale right now — the supply you would be competing with.
 *
 * This data has been fetched and stored since the eBay adapter shipped, and
 * nothing ever showed it. The card page could say "12 listed unsold" and never
 * what those twelve were asking, which is the half of the question that decides
 * whether your copy sells this month or sits.
 *
 * Two things shape how it is presented, both of them refusals:
 *
 * **An asking price is not a sale.** Anyone can ask anything, and an *unsold*
 * listing is evidence that nobody paid it. Displayed as a price ladder beside
 * the valuation it would read as corroboration, when it is closer to the
 * opposite — so each group says plainly how its cheapest ask compares to what
 * the grade actually sells for, and the wording never calls an ask a value.
 *
 * **Grades are separate markets.** Listings are stored per identity, pooled
 * across grades, so an ungrouped list puts a slabbed 10 at £900 three rows above
 * a raw copy at £40 and invites the average of the two. Same error as the
 * pooled-grade trend in Phase 3, and grouped for the same reason.
 */
export function ActiveListings({
  cardId,
  currency,
  market,
  activeListings,
}: {
  cardId: string
  currency: string
  market: MarketBlock | null
  /** What liquidity counted, which can exceed what we hold rows for. */
  activeListings: number | null
}) {
  const listings = useQuery({
    queryKey: keys.listings(cardId),
    queryFn: () => api.cardListings(cardId),
  })

  if (listings.isLoading) return <LoadingPanel label="Loading listings…" />

  const rows = listings.data ?? []

  const groups = new Map<string, Listing[]>()
  for (const row of rows) {
    const bucket = groups.get(row.grade_label)
    if (bucket) bucket.push(row)
    else groups.set(row.grade_label, [row])
  }
  // Raw first: it is the one you own until you decide otherwise.
  const ordered = [...groups.entries()].sort(([a], [b]) =>
    a === 'raw' ? -1 : b === 'raw' ? 1 : a.localeCompare(b),
  )

  const newest = rows.reduce<string | null>(
    (latest, row) => (latest === null || row.seen_at > latest ? row.seen_at : latest),
    null,
  )
  const staleDays = newest
    ? Math.floor((Date.now() - new Date(newest).getTime()) / 86_400_000)
    : null

  return (
    <Panel>
      <PanelHeader>
        <div>
          <PanelTitle>On sale now</PanelTitle>
          <PanelDescription>
            Asking prices, not sales — what other people want for this card, which is not evidence
            that anyone pays it. An unsold listing is the opposite of a comparable.
          </PanelDescription>
        </div>
        {newest ? (
          <Badge tone={staleDays !== null && staleDays > STALE_AFTER_DAYS ? 'caution' : 'neutral'}>
            checked {relativeTime(newest)}
          </Badge>
        ) : null}
      </PanelHeader>

      <PanelBody className="space-y-3">
        {staleDays !== null && staleDays > STALE_AFTER_DAYS ? (
          <p className="flex items-start gap-1.5 text-xs leading-relaxed text-caution">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
            Last checked {staleDays} days ago. Listings end and prices get cut, so treat this as
            what the market looked like then. Refresh the card to see it now.
          </p>
        ) : null}

        {!rows.length ? (
          activeListings ? (
            /* The count and the rows come from different places: liquidity can
               be told "twelve are listed" by a source that does not share the
               listings themselves. Saying nothing is on sale would contradict
               the number on the same page. */
            <p className="text-xs leading-relaxed text-ink-faint">
              A source reports {activeListings} listed unsold, but does not publish the individual
              listings — only the count, which is what the sold-to-active ratio uses. Enabling a
              marketplace source fills this in.
            </p>
          ) : (
            <EmptyState
              title="Nothing on sale"
              description="No active listing has been fetched for this card. Either no marketplace source has looked yet, or nobody is currently selling one."
            />
          )
        ) : null}

        {ordered.map(([label, group]) => (
          <GradeGroup
            key={label}
            label={label}
            listings={group}
            currency={currency}
            realistic={realisticFor(market, label)}
          />
        ))}
      </PanelBody>
    </Panel>
  )
}

/**
 * What this grade actually sells for, if anything has.
 *
 * Deliberately reads the rows rather than the block's status: a `partial`
 * market holds real prices for the grades it could price, and treating anything
 * short of `ok` as "no data" hid the comparison on most cards that had one.
 */
function realisticFor(market: MarketBlock | null, label: string): number | null {
  if (!market) return null
  const row =
    market.raw?.grade_label === label
      ? market.raw
      : market.graded.find((item) => item.grade_label === label)
  return row?.realistic_sale ?? null
}

function GradeGroup({
  label,
  listings,
  currency,
  realistic,
}: {
  label: string
  listings: Listing[]
  currency: string
  realistic: number | null
}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? listings : listings.slice(0, VISIBLE)

  /**
   * A live auction bid is not an asking price — it is an unfinished result, and
   * one that usually rises. Taking it as "the cheapest you can buy this for"
   * understates the competition and flatters the case for selling, so the
   * headline and the comparison come from fixed-price listings only and the
   * auctions are counted beside them.
   */
  const fixed = listings.filter((row) => !row.is_auction)
  const auctions = listings.length - fixed.length
  const cheapest = fixed[0] ?? null

  return (
    <div className="rounded-lg border border-line">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-3 py-2">
        <span className="text-sm font-medium text-ink">{humanise(label)}</span>
        <span className="text-xs text-ink-muted">
          {listings.length} listed
          {cheapest ? ` · from ${formatMoney(cheapest.price, currency)}` : ''}
          {auctions ? ` · ${auctions} at auction` : ''}
        </span>
        {cheapest && realistic !== null ? (
          <span className="ml-auto text-[0.7rem] text-ink-faint">
            <Comparison cheapest={cheapest.price} realistic={realistic} currency={currency} />
          </span>
        ) : !cheapest ? (
          <span className="ml-auto text-[0.7rem] text-ink-faint">
            All at auction — a bid in progress is not a price yet
          </span>
        ) : null}
      </div>

      <ul className="divide-y divide-line">
        {visible.map((listing) => (
          <Row key={listing.id} listing={listing} currency={currency} />
        ))}
      </ul>

      {listings.length > VISIBLE ? (
        <div className="border-t border-line px-3 py-1.5">
          <Button size="sm" variant="ghost" onClick={() => setExpanded((open) => !open)}>
            {expanded ? 'Show fewer' : `Show all ${listings.length}`}
          </Button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * The cheapest ask against what the grade actually sells for.
 *
 * This is the only inference on the panel, and it is the useful one: asks above
 * the realised price are why a card sits unsold, and asks below it are what your
 * copy has to beat. Stated as a comparison rather than as a target, because the
 * decision it feeds is whether to sell at all.
 */
function Comparison({
  cheapest,
  realistic,
  currency,
}: {
  cheapest: number
  realistic: number
  currency: string
}) {
  if (realistic <= 0) return null
  const difference = Math.round(((cheapest - realistic) / realistic) * 100)
  if (Math.abs(difference) < 5) {
    return <>Cheapest ask is about what it sells for ({formatMoney(realistic, currency)})</>
  }
  return (
    <>
      Cheapest ask is{' '}
      <strong className={difference > 0 ? 'text-caution' : 'text-ink-muted'}>
        {Math.abs(difference)}% {difference > 0 ? 'above' : 'below'}
      </strong>{' '}
      the {formatMoney(realistic, currency)} it sells for
    </>
  )
}

function Row({ listing, currency }: { listing: Listing; currency: string }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1.5">
      <span className="tabular text-sm text-ink">
        {formatMoney(listing.price, currency)}
        {/* Named, because an auction sorts by its current bid and can therefore
            sit above the "from" price in the header. Unlabelled, that reads as
            a contradiction rather than as two different kinds of number. */}
        {listing.is_auction ? <span className="text-xs text-ink-faint"> bid</span> : null}
      </span>
      <span className="text-[0.7rem] text-ink-faint">
        {listing.shipping === null
          ? 'postage not stated'
          : listing.shipping === 0
            ? 'free postage'
            : `+ ${formatMoney(listing.shipping, currency)} post`}
      </span>
      {listing.is_auction ? (
        <span className="flex items-center gap-1 text-[0.7rem] text-ink-faint">
          <Gavel className="size-3" />
          {listing.ends_at ? `ends ${timeUntil(listing.ends_at)}` : 'auction'}
        </span>
      ) : (
        <ShoppingBag className="size-3 text-ink-faint" />
      )}
      <span className="min-w-0 flex-1 truncate text-[0.7rem] text-ink-faint">
        {listing.listing_title ?? listing.platform ?? ''}
      </span>
      {listing.source_url ? (
        <a
          href={listing.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-ink-faint hover:text-ink"
          aria-label="Open the listing"
        >
          <ExternalLink className="size-3.5" />
        </a>
      ) : null}
    </li>
  )
}
