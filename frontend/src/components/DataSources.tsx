import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CloudDownload, ExternalLink, Info, Wifi, WifiOff } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { CardSyncOutcome, DataSource, SyncReport } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { ErrorState, LoadingPanel } from '@/components/ui/states'
import { cn, formatNumber, humanise, relativeTime } from '@/lib/utils'

/**
 * Turning the internet on, and seeing what it gave you.
 *
 * The framing matters here more than the layout. Every other screen in this
 * application works with no network at all; this is the one place that changes,
 * and it should read like a decision rather than a settings row.
 */
export function DataSources() {
  const queryClient = useQueryClient()
  const [report, setReport] = useState<SyncReport[] | null>(null)

  const sources = useQuery({ queryKey: keys.dataSources, queryFn: api.listDataSources })

  const toggle = useMutation({
    mutationFn: ({ code, enabled }: { code: string; enabled: boolean }) =>
      api.updateDataSource(code, { enabled }),
    onSuccess: (state) => {
      toast.success(state.enabled ? `${state.name} enabled` : `${state.name} disabled`)
      queryClient.invalidateQueries({ queryKey: keys.dataSources })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not change that source'),
  })

  const refresh = useMutation({
    mutationFn: () => api.refreshMarket(),
    onSuccess: (reports) => {
      setReport(reports)
      const updated = reports.reduce((sum, item) => sum + item.updated, 0)
      toast.success(updated ? `Updated ${updated} card(s)` : 'Nothing was updated')
      queryClient.invalidateQueries()
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'The refresh failed'),
  })

  if (sources.isLoading) return <LoadingPanel />
  if (sources.isError) return <ErrorState error={sources.error} />

  const live = sources.data!.filter((source) => source.has_adapter && isNetwork(source))
  const anyEnabled = live.some((source) => source.enabled)
  // A marketplace supplies individual sales; a catalogue supplies an index.
  // Which of those is switched on decides what this screen has to warn about.
  const salesSource = live.find((source) => source.enabled && source.kind === 'market_data')

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface-raised px-4 py-3 text-xs leading-relaxed text-ink-muted">
        The local database is the source of truth. Providers import into it — if one goes away you
        keep your collection, your price history and every past analysis. API keys are read from
        the environment and never stored here.
        <span className="mt-1.5 block">
          One source ships switched on: the card catalogue, which needs no account and no key.
          Everything else reaches the network only once you enable it below, and any of them can be
          switched off.
        </span>
      </div>

      {/* The thing a user most needs to know, and the thing a source that only
          prices raw cards will never tell them. */}
      {salesSource ? (
        <div className="flex items-start gap-2 rounded-lg border border-line bg-surface-raised px-4 py-3 text-xs leading-relaxed text-ink-muted">
          <Info className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="text-ink">{salesSource.name} supplies individual sales</strong>, so
            the graded side of the grading decision can finally be measured rather than typed in.
            Two things to know about it. The grade is read out of the seller&rsquo;s own listing
            title, so check the exclusions on a card before trusting a thin sample. And sold data
            covers a 90-day window — a longer trend still accrues forward from daily snapshots.
          </span>
        </div>
      ) : (
        <div className="flex items-start gap-2 rounded-lg border border-caution/30 bg-caution/10 px-4 py-3 text-xs leading-relaxed text-caution">
          <Info className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong>A catalogue price is not a grading decision.</strong> The card catalogue gives
            an aggregate price for the <em>raw</em> card and no graded prices at all, so it cannot
            say what a slab fetches — which is the whole of the grade-or-sell question. It carries
            no individual sales either, so liquidity stays unknown and a trend only accrues forward
            from today. Until a sales-level source is connected, graded comparables have to be
            entered or imported by hand.
          </span>
        </div>
      )}

      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>Sources</PanelTitle>
            <PanelDescription>
              A source with no adapter is listed to show what is planned, not to be switched on.
            </PanelDescription>
          </div>
          <Button
            size="sm"
            variant="primary"
            disabled={!anyEnabled || refresh.isPending}
            onClick={() => refresh.mutate()}
          >
            <CloudDownload className="size-3.5" />
            {refresh.isPending ? 'Fetching…' : 'Refresh prices'}
          </Button>
        </PanelHeader>
        <PanelBody className="space-y-2">
          {sources.data!.map((source) => (
            <SourceRow
              key={source.id}
              source={source}
              busy={toggle.isPending}
              onToggle={(enabled) => toggle.mutate({ code: source.code, enabled })}
            />
          ))}
        </PanelBody>
      </Panel>

      {report ? <SyncResult reports={report} /> : null}
    </div>
  )
}

/**
 * Does this source actually reach out?
 *
 * Manual entry and CSV import are sources in the same table and are always on,
 * because they are the user's own data — asking them to refresh would be asking
 * a spreadsheet to phone home. Counting them as network sources made the
 * refresh button look available when nothing could be fetched.
 */
function isNetwork(source: DataSource): boolean {
  return !['manual', 'csv'].includes(source.code)
}

function SourceRow({
  source,
  busy,
  onToggle,
}: {
  source: DataSource
  busy: boolean
  onToggle: (enabled: boolean) => void
}) {
  const network = source.has_adapter && isNetwork(source)

  return (
    <div className="rounded-lg border border-line px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-sm font-medium text-ink">
            {source.name}
            {network ? (
              source.enabled ? (
                <Wifi className="size-3.5 text-positive" />
              ) : (
                <WifiOff className="size-3.5 text-ink-faint" />
              )
            ) : null}
          </span>
          <span className="block text-xs text-ink-faint">{humanise(source.kind)}</span>
        </span>

        <span className="shrink-0 text-xs text-ink-faint">
          {source.credentials?.length ? (
            <span className="flex flex-col items-end gap-0.5">
              {source.credentials.map((credential) => (
                <span
                  key={credential.env_var}
                  className={credential.present ? 'text-positive' : undefined}
                  title={
                    credential.present
                      ? 'Set in this environment. The value is never read or stored here.'
                      : 'Not set. Add it to the environment and restart.'
                  }
                >
                  {credential.env_var} {credential.present ? 'set' : 'not set'}
                </span>
              ))}
            </span>
          ) : (
            'No key needed'
          )}
        </span>

        {source.last_sync_at ? (
          <span className="shrink-0 text-xs text-ink-faint">
            synced {relativeTime(source.last_sync_at)}
          </span>
        ) : null}

        {!source.has_adapter ? (
          <Badge tone="neutral">No adapter yet</Badge>
        ) : network ? (
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onToggle(!source.enabled)}>
            {source.enabled ? 'Disable' : 'Enable'}
          </Button>
        ) : (
          <Badge tone="positive">Always on</Badge>
        )}
      </div>

      {source.notes ? (
        <p className="pt-1.5 text-[0.7rem] leading-relaxed text-ink-faint">{source.notes}</p>
      ) : null}
      {source.terms_url ? (
        <a
          href={source.terms_url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-[0.7rem] text-brand hover:underline"
        >
          Terms of use <ExternalLink className="size-3" />
        </a>
      ) : null}
    </div>
  )
}

/**
 * One card's line in a refresh report.
 *
 * The two kinds of source want different things said. An index source has one
 * number to report and the interesting question is whether it converted; a
 * marketplace has a pile of sales and the interesting question is which grades
 * they landed in and how much was thrown away. Rendering the second as the first
 * would show "—" beside a card that just gained fourteen comparables.
 */
function CardRow({ card }: { card: CardSyncOutcome }) {
  const sales = card.sales_imported + card.sales_updated
  const graded = card.grades.filter((grade) => grade !== 'raw')

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
      <span className="min-w-0 flex-1 truncate text-sm text-ink">{card.name}</span>

      {sales || card.listings_seen ? (
        <>
          {graded.length ? (
            <span className="flex shrink-0 flex-wrap gap-1">
              {/* The graded labels are the point of a marketplace source: they
                  are the side of the comparison nothing else here can fill. */}
              {graded.map((grade) => (
                <Badge key={grade} tone="positive">
                  {grade}
                </Badge>
              ))}
            </span>
          ) : null}
          <span className="tabular shrink-0 text-xs text-ink-faint">
            {card.listings_seen ? (
              <>
                {formatNumber(card.listings_seen)}
                {card.listings_reported && card.listings_reported > card.listings_seen
                  ? ` of ${formatNumber(card.listings_reported)}`
                  : ''}{' '}
                listed
              </>
            ) : null}
          </span>
          <span className="tabular w-28 shrink-0 text-right text-sm text-ink">
            {formatNumber(sales)} sale{sales === 1 ? '' : 's'}
            {card.sales_excluded ? (
              <span
                className="text-ink-faint"
                title="Fetched and deliberately not counted — job lots, wrong language, wrong printing, wrong grade. Kept in the table so you can inspect and reverse any of them."
              >
                {' '}
                −{formatNumber(card.sales_excluded)}
              </span>
            ) : null}
          </span>
        </>
      ) : (
        <>
          {/* Both numbers, always. The quoted figure and the rate are what make
              the converted one checkable. */}
          {card.source_value !== null ? (
            <span className="tabular shrink-0 text-xs text-ink-faint">
              {card.source_currency} {card.source_value.toFixed(2)}
              {card.fx_rate !== null ? ` × ${card.fx_rate}` : ''}
            </span>
          ) : null}
          <span
            className={cn(
              'tabular w-20 shrink-0 text-right text-sm',
              card.status === 'updated' ? 'text-ink' : 'text-ink-faint',
            )}
          >
            {card.value === null ? '—' : `${card.currency} ${card.value.toFixed(2)}`}
          </span>
        </>
      )}

      {card.reason ? (
        <p className="w-full text-[0.7rem] leading-relaxed text-ink-faint">{card.reason}</p>
      ) : null}
    </div>
  )
}

function SyncResult({ reports }: { reports: SyncReport[] }) {
  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle>Last refresh</PanelTitle>
          <PanelDescription>
            What was fetched, what was skipped, and why — per card, so a partial run never reads
            as a whole one.
          </PanelDescription>
        </div>
      </PanelHeader>
      <PanelBody className="space-y-4">
        {reports.map((report) => (
          <div key={report.source_code} className="rounded-lg border border-line">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-3 py-2.5">
              <span className="min-w-0 flex-1 text-sm font-medium text-ink">
                {report.source_name}
              </span>
              <span className="text-xs text-ink-muted">
                {formatNumber(report.updated)} updated · {formatNumber(report.skipped)} skipped ·{' '}
                {formatNumber(report.failed)} failed
                {report.sales_imported ? (
                  <> · {formatNumber(report.sales_imported)} sales</>
                ) : null}
                {report.listings_seen ? (
                  <> · {formatNumber(report.listings_seen)} listings</>
                ) : null}
              </span>
              <Badge
                tone={
                  report.status === 'ok'
                    ? 'positive'
                    : report.status === 'error'
                      ? 'negative'
                      : 'caution'
                }
              >
                {humanise(report.status)}
              </Badge>
            </div>

            {report.reason ? (
              <p className="px-3 py-2 text-xs leading-relaxed text-ink-muted">{report.reason}</p>
            ) : null}
            {report.notes.map((note) => (
              <p
                key={note}
                className="flex items-start gap-1.5 px-3 pb-2 text-[0.7rem] leading-relaxed text-caution"
              >
                <AlertTriangle className="mt-px size-3 shrink-0" />
                {note}
              </p>
            ))}

            {report.cards.length ? (
              <div className="divide-y divide-line border-t border-line">
                {report.cards.map((card) => (
                  <CardRow key={card.card_id} card={card} />
                ))}
              </div>
            ) : null}
          </div>
        ))}
      </PanelBody>
    </Panel>
  )
}
