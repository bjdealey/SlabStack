import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AlertTriangle, GraduationCap, Scale, Target } from 'lucide-react'
import { api, keys } from '@/lib/api'
import type {
  AccuracyReport,
  CalibrationEntry,
  CalibrationState,
  CompanyAccuracy,
} from '@/lib/types'
import { StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { EmptyState, ErrorState, Skeleton } from '@/components/ui/states'
import { cn, formatNumber } from '@/lib/utils'

/**
 * How the model has actually done, and what it learned from that.
 *
 * Two questions, deliberately shown together. The report card is interesting;
 * the correction is the one with teeth, and a user who sees a prediction move
 * needs to be able to find out why without hunting.
 */
export function Accuracy() {
  const accuracy = useQuery({ queryKey: keys.accuracy, queryFn: api.accuracy })
  const calibration = useQuery({ queryKey: keys.calibration, queryFn: api.calibration })

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader>
          <div className="min-w-0">
            <PanelTitle>Predicted against what came back</PanelTitle>
            <PanelDescription>
              {accuracy.data
                ? summarise(accuracy.data)
                : 'Every recorded grade, marked against the prediction held when the card was sent.'}
            </PanelDescription>
          </div>
          {accuracy.data ? <StatusBadge status={accuracy.data.status} /> : null}
        </PanelHeader>

        <PanelBody className="space-y-5">
          {accuracy.isError ? <ErrorState error={accuracy.error} /> : null}
          {accuracy.isLoading ? <Skeletons /> : null}

          {accuracy.data && !accuracy.data.scored ? (
            <EmptyState
              icon={<GraduationCap className="size-8" />}
              title="Nothing marked yet"
              description={
                accuracy.data.reason ??
                'Send a submission and record the grades when it comes back. This is the only thing here that cannot be rebuilt from public data.'
              }
            />
          ) : null}

          {accuracy.data?.companies.map((company) => (
            <CompanyCard
              key={company.company_id}
              company={company}
              calibration={calibration.data?.companies.find(
                (row) => row.company_id === company.company_id,
              )}
            />
          ))}

          {accuracy.data?.awaiting ? (
            <p className="flex items-start gap-1.5 text-[0.7rem] leading-relaxed text-caution">
              <AlertTriangle className="mt-px size-3 shrink-0" />
              {accuracy.data.reason}
            </p>
          ) : null}
        </PanelBody>
      </Panel>

      {calibration.data ? <CalibrationPanel state={calibration.data} /> : null}

      {accuracy.data?.results.length ? (
        <Panel>
          <PanelHeader>
            <div className="min-w-0">
              <PanelTitle>Every result</PanelTitle>
              <PanelDescription>
                Newest first. A Brier score marks the whole distribution, not just the grade it
                called most likely — lower is better.
              </PanelDescription>
            </div>
          </PanelHeader>
          <PanelBody>
            <div className="space-y-1.5">
              {accuracy.data.results.map((row) => (
                <Link
                  key={`${row.card_id}-${row.graded_at}-${row.actual_grade}`}
                  to={`/cards/${row.card_id}`}
                  className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line px-3 py-2 transition-colors hover:bg-canvas"
                >
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{row.name}</span>
                  <span className="w-12 shrink-0 text-xs text-ink-faint">{row.company_code}</span>
                  <span className="tabular shrink-0 text-xs text-ink-muted">
                    predicted {row.predicted_grade ?? '—'} →{' '}
                    <span className="font-semibold text-ink">got {row.actual_grade}</span>
                  </span>
                  <span
                    className={cn(
                      'tabular w-14 shrink-0 text-right text-sm',
                      row.surprise === null
                        ? 'text-ink-faint'
                        : row.surprise > 0
                          ? 'text-positive'
                          : row.surprise < 0
                            ? 'text-caution'
                            : 'text-ink-muted',
                    )}
                  >
                    {row.surprise === null
                      ? '—'
                      : row.surprise > 0
                        ? `+${row.surprise}`
                        : row.surprise}
                  </span>
                  <span className="tabular w-14 shrink-0 text-right text-xs text-ink-faint">
                    {row.brier === null ? '—' : row.brier.toFixed(2)}
                  </span>
                </Link>
              ))}
            </div>
          </PanelBody>
        </Panel>
      ) : null}
    </div>
  )
}

function CompanyCard({
  company,
  calibration,
}: {
  company: CompanyAccuracy
  calibration: CalibrationEntry | undefined
}) {
  return (
    <div className="rounded-lg border border-line">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-3 py-2.5">
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-ink">{company.company_name}</span>
          <span className="block text-xs text-ink-faint">
            {formatNumber(company.scored)} result(s) marked
          </span>
        </span>
        {calibration?.applied ? (
          <Badge tone="brand">Correcting new predictions</Badge>
        ) : (
          <Badge tone="outline">Measuring only</Badge>
        )}
        <StatusBadge status={company.status} />
      </div>

      {/* The one sentence worth reading, which is the point of the whole page. */}
      {company.headline ? (
        <p className="border-b border-line px-3 py-2.5 text-sm text-ink">{company.headline}</p>
      ) : null}

      <div className="grid gap-3 px-3 py-3 sm:grid-cols-4">
        <Figure
          label="Bias"
          value={
            company.mean_error === null
              ? '—'
              : `${company.mean_error > 0 ? '+' : ''}${company.mean_error.toFixed(2)}`
          }
          note={
            company.mean_error === null
              ? 'not measurable'
              : company.mean_error > 0
                ? 'comes back better'
                : company.mean_error < 0
                  ? 'comes back worse'
                  : 'on the nose'
          }
        />
        <Figure
          label="Exact"
          value={company.exact_pct === null ? '—' : `${company.exact_pct}%`}
          note="landed on the predicted grade"
        />
        <Figure
          label="Within half"
          value={company.within_half_pct === null ? '—' : `${company.within_half_pct}%`}
          note="half a grade either way"
        />
        <Figure
          label="Brier"
          value={company.mean_brier === null ? '—' : company.mean_brier.toFixed(2)}
          note="lower is better"
        />
      </div>

      {company.bands.length ? <CalibrationCurve company={company} /> : null}

      {company.reason ? (
        <p className="px-3 pb-3 text-[0.7rem] leading-relaxed text-ink-faint">{company.reason}</p>
      ) : null}
    </div>
  )
}

/**
 * The calibration curve: how often each grade was predicted against how often
 * it actually happened. Two bars per grade rather than a line, because the
 * comparison *is* the reading — a single accuracy number hides which end of the
 * ladder the model is wrong about.
 */
function CalibrationCurve({ company }: { company: CompanyAccuracy }) {
  const data = company.bands.map((band) => ({
    grade: `${band.grade}`,
    Predicted: Math.round((band.predicted_rate ?? 0) * 1000) / 10,
    Actual: Math.round((band.actual_rate ?? 0) * 1000) / 10,
  }))

  return (
    <div className="px-3 pb-3">
      {/* Two bars per grade rather than a line, because the comparison *is* the
          reading — a single accuracy number hides which end of the ladder the
          model is wrong about. The key is inline: an unlabelled grey bar next
          to a blue one is a puzzle, not a chart. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-2">
        <p className="text-[0.7rem] uppercase tracking-wide text-ink-faint">
          How often predicted, how often it happened
        </p>
        <span className="flex items-center gap-1.5 text-[0.7rem] text-ink-faint">
          <span className="inline-block size-2 rounded-sm bg-ink-faint" />
          predicted
        </span>
        <span className="flex items-center gap-1.5 text-[0.7rem] text-ink-faint">
          <span className="inline-block size-2 rounded-sm bg-brand" />
          actual
        </span>
      </div>
      <div className="h-48 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" vertical={false} />
            <XAxis
              dataKey="grade"
              tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
              tickLine={false}
              axisLine={{ stroke: 'var(--color-line)' }}
            />
            <YAxis
              tick={{ fontSize: 11, fill: 'var(--color-ink-faint)' }}
              tickLine={false}
              axisLine={false}
              width={38}
              unit="%"
            />
            <Tooltip
              cursor={{ fill: 'var(--color-surface-raised)' }}
              contentStyle={{
                background: 'var(--color-surface-raised)',
                border: '1px solid var(--color-line)',
                borderRadius: 8,
                fontSize: 12,
              }}
              formatter={(value) => `${value ?? 0}%`}
            />
            <Bar dataKey="Predicted" radius={[3, 3, 0, 0]}>
              {data.map((entry) => (
                <Cell key={`p-${entry.grade}`} fill="var(--color-ink-faint)" />
              ))}
            </Bar>
            <Bar dataKey="Actual" radius={[3, 3, 0, 0]}>
              {data.map((entry) => (
                <Cell key={`a-${entry.grade}`} fill="var(--color-brand)" />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function CalibrationPanel({ state }: { state: CalibrationState }) {
  return (
    <Panel>
      <PanelHeader>
        <div className="min-w-0">
          <PanelTitle className="flex items-center gap-2">
            <Scale className="size-4" />
            What that is doing to new predictions
          </PanelTitle>
          <PanelDescription>
            Per grader, because PSA's bias is not CGC's. A correction is measured from the first
            result and applied only past {state.minimum_sample} — below that it would be fitted to
            noise.
          </PanelDescription>
        </div>
        <Badge tone={state.enabled ? 'positive' : 'outline'}>
          {state.enabled ? 'Learning on' : 'Learning off'}
        </Badge>
      </PanelHeader>
      <PanelBody>
        <div className="space-y-1.5">
          {state.companies.map((row) => (
            <div key={row.company_id} className="rounded-lg border border-line px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="w-14 shrink-0 text-sm font-medium text-ink">
                  {row.company_code}
                </span>
                <span className="tabular shrink-0 text-xs text-ink-muted">
                  {formatNumber(row.sample_size)}/{formatNumber(row.minimum_sample)} results
                </span>

                {/* Shown whether or not it is applied: "measured 0.4 high across
                    three cards, not correcting yet" is worth knowing. */}
                <span className="tabular shrink-0 text-right">
                  <span
                    className={cn(
                      'text-sm font-semibold',
                      row.applied ? 'text-ink' : 'text-ink-faint',
                    )}
                  >
                    {row.grade_offset > 0 ? '+' : ''}
                    {row.grade_offset.toFixed(2)} grades
                  </span>
                </span>
                {row.spread_multiplier > 1 ? (
                  <span className="shrink-0 text-xs text-caution">
                    range ×{row.spread_multiplier.toFixed(2)}
                  </span>
                ) : null}

                <span className="flex-1" />
                <Badge tone={row.applied ? 'brand' : 'outline'}>
                  {row.applied ? `applied · ${row.confidence}` : 'not applied'}
                </Badge>
              </div>
              {row.reason ? (
                <p className="pt-1.5 text-[0.7rem] leading-relaxed text-ink-faint">{row.reason}</p>
              ) : null}
            </div>
          ))}
        </div>
      </PanelBody>
    </Panel>
  )
}

function Figure({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <p className="flex items-center gap-1.5 text-[0.7rem] uppercase tracking-wide text-ink-faint">
        <Target className="size-3" />
        {label}
      </p>
      <p className="tabular pt-0.5 text-lg font-semibold text-ink">{value}</p>
      <p className="text-[0.7rem] text-ink-faint">{note}</p>
    </div>
  )
}

function Skeletons() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }, (_, index) => (
        <Skeleton key={index} className="h-20 w-full" />
      ))}
    </div>
  )
}

/** Always the total *and* what it could not see. */
function summarise(report: AccuracyReport): string {
  if (!report.scored) return report.reason ?? 'Nothing has been marked yet.'
  const scope = `${formatNumber(report.scored)} result(s) marked`
  return report.awaiting
    ? `${scope}; ${formatNumber(report.awaiting)} graded card(s) had no prediction behind them.`
    : `${scope}.`
}
