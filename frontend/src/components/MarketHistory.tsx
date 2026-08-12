import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { LineChart as LineChartIcon } from 'lucide-react'
import { api, keys } from '@/lib/api'
import type { SnapshotSeries } from '@/lib/types'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn, formatMoney } from '@/lib/utils'

/** Enough to tell several grades apart without inventing a meaning for each. */
const SERIES_COLOURS = [
  'var(--color-brand)',
  'var(--color-positive)',
  'var(--color-caution)',
  'var(--color-negative)',
  'var(--color-ink-muted)',
  'var(--color-ink-faint)',
]

const RANGES = [
  { days: 30, label: '30d' },
  { days: 90, label: '90d' },
  { days: 365, label: '1y' },
] as const

/**
 * What this card has done since you started watching it.
 *
 * Three things about this chart are deliberately awkward, and each of them is
 * the honest reading of what `price_snapshots` actually is.
 *
 * **It starts when you did.** A snapshot is written on the days you refresh, so
 * the left edge is your first refresh and not the card's history. No source in
 * this build sells historic prices — PriceCharting's documentation says so
 * outright — which means this is the one dataset here that cannot be
 * re-fetched, and also the one that begins empty.
 *
 * **The line connects observations, not days.** Refresh on Monday and Friday
 * and there is no Tuesday. A straight segment between two points is drawn
 * because the eye needs it, not because the price moved that way, so every
 * observation is marked and the caption says so.
 *
 * **One point is not a trend.** A single dot with a line through nothing is the
 * exact false precision the rest of this application refuses, so below two
 * points it says how many it has instead of drawing anything.
 */
export function MarketHistory({ cardId, currency }: { cardId: string; currency: string }) {
  const [days, setDays] = useState<number>(90)
  const [hidden, setHidden] = useState<string[]>([])

  const history = useQuery({
    queryKey: [...keys.marketHistory(cardId), days],
    queryFn: () => api.marketHistory(cardId, days),
  })

  const series = history.data ?? []
  const { rows, grades, observations, mixed } = useMemo(() => toRows(series), [series])
  const shown = grades.filter((grade) => !hidden.includes(grade))

  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle className="flex items-center gap-2">
            <LineChartIcon className="size-4" /> History
          </PanelTitle>
          <PanelDescription>
            Your own record of this card, one point per day you refreshed. Nothing here can be
            re-fetched if a source disappears.
          </PanelDescription>
        </div>
        <div className="flex shrink-0 gap-1">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              onClick={() => setDays(range.days)}
              className={cn(
                'rounded-md border px-2 py-1 text-xs transition-colors',
                range.days === days
                  ? 'border-brand bg-brand/10 text-brand'
                  : 'border-line text-ink-muted hover:border-brand/50',
              )}
            >
              {range.label}
            </button>
          ))}
        </div>
      </PanelHeader>

      <PanelBody>
        {history.isLoading ? <Skeleton className="h-56 w-full" /> : null}
        {history.isError ? <ErrorState error={history.error} /> : null}

        {history.data && observations < 2 ? (
          <EmptyState
            icon={<LineChartIcon className="size-8" />}
            title={observations === 1 ? 'One day recorded so far' : 'No history yet'}
            description={
              observations === 1
                ? 'A line needs two days. Refresh again tomorrow and this starts filling in — no ' +
                  'source sells historic prices, so this accrues a day at a time from here.'
                : 'History is written each time prices are refreshed. Add sales, or refresh a ' +
                  'connected source, and the first point appears today.'
            }
          />
        ) : null}

        {history.data && observations >= 2 ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {grades.map((grade, index) => (
                <button
                  key={grade}
                  type="button"
                  onClick={() =>
                    setHidden((current) =>
                      current.includes(grade)
                        ? current.filter((item) => item !== grade)
                        : [...current, grade],
                    )
                  }
                  className={cn(
                    'flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors',
                    hidden.includes(grade)
                      ? 'border-line text-ink-faint'
                      : 'border-line text-ink-muted hover:border-brand/50',
                  )}
                >
                  <span
                    className="inline-block size-2 rounded-sm"
                    style={{
                      background: hidden.includes(grade)
                        ? 'var(--color-line)'
                        : SERIES_COLOURS[index % SERIES_COLOURS.length],
                    }}
                  />
                  {grade === 'raw' ? 'Raw' : grade}
                </button>
              ))}
            </div>

            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                  <CartesianGrid stroke="var(--color-line)" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={{ stroke: 'var(--color-line)' }}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
                    tickLine={false}
                    axisLine={false}
                    width={54}
                    tickFormatter={(value) => formatMoney(Number(value), currency, { compact: true })}
                  />
                  <Tooltip content={<PointTooltip currency={currency} />} />
                  {grades.map((grade, index) =>
                    hidden.includes(grade) ? null : (
                      <Line
                        key={grade}
                        type="monotone"
                        dataKey={grade}
                        name={grade === 'raw' ? 'Raw' : grade}
                        stroke={SERIES_COLOURS[index % SERIES_COLOURS.length]}
                        strokeWidth={2}
                        /* Every observation is marked. The segments between them
                           are drawn for the eye; the dots are the evidence. */
                        dot={{ r: 2.5 }}
                        activeDot={{ r: 4 }}
                        /* A gap is a day you did not refresh, and joining across
                           it would invent the prices in between. */
                        connectNulls={false}
                        isAnimationActive={false}
                      />
                    ),
                  )}
                </LineChart>
              </ResponsiveContainer>
            </div>

            {mixed.length ? (
              <p className="rounded-lg border border-caution/30 bg-caution/10 px-3 py-2 text-[0.7rem] leading-relaxed text-caution">
                {mixed.length === 1
                  ? `The ${label(mixed[0])} line mixes`
                  : 'Some lines mix'}{' '}
                days valued from your own sales with days taken from a source&rsquo;s index. Where a
                line steps between the two it is the <em>basis</em> that changed, not the market —
                hover a point to see which it was.
              </p>
            ) : null}

            <Depth rows={rows} shown={shown} />

            <p className="border-t border-line pt-2 text-[0.7rem] leading-relaxed text-ink-faint">
              Each dot is a day a price was recorded; the line between two dots is drawn to be
              readable, not because the price moved that way. The chart starts at your first
              refresh, not at the card&rsquo;s release — no source in this build sells historic
              prices, so this is the one record here that only ever grows forward.
            </p>
          </div>
        ) : null}
      </PanelBody>
    </Panel>
  )
}

/**
 * How much was behind each price, on the same dates.
 *
 * The prices above are worth very different amounts depending on this: a point
 * computed from thirty sales and one from a provider's index with no sales at
 * all look identical on a line. Shown as its own small chart rather than a
 * second axis, because two axes on one plot invite reading a correlation into
 * whatever the scales happen to make line up.
 */
function Depth({ rows, shown }: { rows: Row[]; shown: string[] }) {
  const hasDepth = rows.some((row) => (row.samples ?? 0) > 0 || (row.listings ?? 0) > 0)
  if (!shown.length || !hasDepth) return null

  return (
    <div>
      <p className="mb-1 text-[0.7rem] text-ink-faint">
        Sales behind each price, and copies listed unsold
      </p>
      <div className="h-20 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
            <XAxis dataKey="date" hide />
            <YAxis hide />
            <Tooltip
              cursor={{ fill: 'var(--color-surface-raised)' }}
              contentStyle={{
                background: 'var(--color-surface-raised)',
                border: '1px solid var(--color-line)',
                borderRadius: 8,
                fontSize: 12,
              }}
            />
            <Bar dataKey="samples" name="Sales" fill="var(--color-ink-faint)" radius={[2, 2, 0, 0]} />
            <Bar
              dataKey="listings"
              name="Listed"
              fill="var(--color-line)"
              radius={[2, 2, 0, 0]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

interface Row {
  date: string
  samples: number | null
  listings: number | null
  [grade: string]: string | number | null
}

/**
 * One row per date, one column per grade — the shape Recharts wants.
 *
 * Dates with no reading for a grade are left *absent* rather than zeroed.
 * A zero would be plotted as a price of nothing, which is a far worse lie than
 * a gap in the line.
 */
function label(grade: string): string {
  return grade === 'raw' ? 'Raw' : grade
}

function toRows(series: SnapshotSeries[]): {
  rows: Row[]
  grades: string[]
  observations: number
  /** Grades whose points do not all rest on the same kind of evidence. */
  mixed: string[]
} {
  const byDate = new Map<string, Row>()
  let observations = 0

  for (const entry of series) {
    for (const point of entry.points) {
      const row = byDate.get(point.snapshot_date) ?? {
        date: point.snapshot_date,
        samples: null,
        listings: null,
      }
      row[entry.grade_label] = point.value
      // Zero means a provider's index; anything above it means your own sales.
      // Kept per grade so the tooltip can say which, because a line that
      // switches between the two changes *basis*, not price.
      row[`${entry.grade_label}__n`] = point.sample_size ?? 0
      // Depth is a fact about the day, so the largest reading on it wins rather
      // than the last one parsed.
      row.samples = Math.max(row.samples ?? 0, point.sample_size ?? 0)
      if (point.active_listings !== null) {
        row.listings = Math.max(row.listings ?? 0, point.active_listings)
      }
      byDate.set(point.snapshot_date, row)
      observations += 1
    }
  }

  const grades = series
    .map((entry) => entry.grade_label)
    // Raw first, then the graded ladder, so the legend reads the way the card
    // page does.
    .sort((left, right) => (left === 'raw' ? -1 : right === 'raw' ? 1 : left.localeCompare(right)))

  // A line that switches basis part-way steps up or down for a reason that has
  // nothing to do with the market, so it is called out rather than left to be
  // discovered by hovering the one point that explains it.
  const mixed = series
    .filter((entry) => {
      const own = entry.points.filter((point) => (point.sample_size ?? 0) > 0).length
      return own > 0 && own < entry.points.length
    })
    .map((entry) => entry.grade_label)

  return {
    rows: [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date)),
    grades,
    mixed,
    // Days recorded, not points: three grades on one day is still one day, and
    // a chart of one day is not a chart.
    observations: byDate.size,
  }
}

/**
 * What each point rests on, not merely what it says.
 *
 * A series can switch between your own valuation and a provider's index from
 * one day to the next — the resolver picks the better evidence per day, and
 * yours wins when it exists. On a line that reads as a price movement, and it
 * is not one. Naming the basis per point is what stops a change of source being
 * mistaken for a change of market.
 */
function PointTooltip({
  active,
  payload,
  label,
  currency,
}: {
  active?: boolean
  payload?: { name?: string; dataKey?: string; value?: number; color?: string }[]
  label?: string
  currency: string
}) {
  if (!active || !payload?.length) return null
  const row = (payload[0] as { payload?: Row }).payload

  return (
    <div className="rounded-lg border border-line bg-surface-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink">{label}</p>
      {payload.map((item) => {
        const samples = Number(row?.[`${item.dataKey}__n`] ?? 0)
        return (
          <p key={String(item.dataKey)} className="flex items-center gap-1.5 text-ink-muted">
            <span
              className="inline-block size-2 rounded-sm"
              style={{ background: item.color }}
            />
            {item.name}: {formatMoney(Number(item.value), currency)}
            <span className="text-ink-faint">
              {samples > 0 ? `· your ${samples} sale${samples === 1 ? '' : 's'}` : '· source index'}
            </span>
          </p>
        )
      })}
    </div>
  )
}
