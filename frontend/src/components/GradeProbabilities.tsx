import { useState } from 'react'
import { AlertTriangle, UserPen } from 'lucide-react'
import type { CompanyGradePrediction, GradePredictionBlock } from '@/lib/types'
import { ConfidenceBadge, StatusBadge } from '@/components/DecisionBadge'
import { Badge } from '@/components/ui/badge'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

/**
 * The grade distribution, per grading company.
 *
 * Shown as a distribution rather than a single number on purpose: graders are
 * not deterministic, and "PSA 9, probably" is a decision the user can act on
 * while "PSA 9" is a claim the model cannot support.
 */
export function GradeProbabilities({ block }: { block: GradePredictionBlock }) {
  const companies = block.by_company
  const [selected, setSelected] = useState(companies[0]?.company_code ?? '')

  if (!companies.length) {
    return (
      <Panel>
        <PanelHeader>
          <div>
            <PanelTitle>Grade probabilities</PanelTitle>
            <PanelDescription>{block.reason}</PanelDescription>
          </div>
          <StatusBadge status={block.status} phase={block.phase} />
        </PanelHeader>
      </Panel>
    )
  }

  const active = companies.find((item) => item.company_code === selected) ?? companies[0]

  return (
    <Panel>
      <PanelHeader>
        <div>
          <PanelTitle>Grade probabilities</PanelTitle>
          <PanelDescription>
            {block.reason ??
              `Estimated from the condition assessment. Model ${block.model_version ?? ''}.`}
          </PanelDescription>
        </div>
        <div className="flex items-center gap-2">
          <ConfidenceBadge confidence={active.confidence} />
          <StatusBadge status={block.status} phase={block.phase} />
        </div>
      </PanelHeader>

      <PanelBody className="space-y-4">
        <Tabs value={active.company_code} onValueChange={setSelected}>
          <TabsList>
            {companies.map((company) => (
              <TabsTrigger key={company.company_code} value={company.company_code}>
                {company.company_code}
                {company.likely_grade !== null ? (
                  <span className="ml-1.5 text-ink-faint">{company.likely_grade}</span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>

          {companies.map((company) => (
            <TabsContent key={company.company_code} value={company.company_code} className="mt-4">
              <CompanyDistribution company={company} baseGrade={block.base_grade} />
            </TabsContent>
          ))}
        </Tabs>
      </PanelBody>
    </Panel>
  )
}

function CompanyDistribution({
  company,
  baseGrade,
}: {
  company: CompanyGradePrediction
  baseGrade: number | null
}) {
  const peak = Math.max(...company.probabilities.map((item) => item.probability), 0.01)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">Likely grade</p>
          <p className="tabular mt-0.5 text-3xl font-semibold leading-none text-brand">
            {company.likely_grade ?? '—'}
          </p>
        </div>
        <div className="text-right">
          <p className="text-[0.7rem] uppercase tracking-wider text-ink-faint">Likely range</p>
          <p className="tabular mt-0.5 text-lg text-ink">
            {company.grade_min !== null && company.grade_max !== null
              ? `${company.grade_min}–${company.grade_max}`
              : '—'}
          </p>
        </div>
      </div>

      <div className="space-y-1.5">
        {company.probabilities.map((item) => (
          <div key={item.grade} className="flex items-center gap-3">
            <span className="tabular w-10 shrink-0 text-right text-sm text-ink-muted">
              {item.grade}
            </span>
            <div className="h-5 flex-1 overflow-hidden rounded bg-surface-raised">
              <div
                className={cn(
                  'h-full rounded transition-[width]',
                  item.grade === company.likely_grade ? 'bg-brand' : 'bg-brand/40',
                )}
                style={{ width: `${Math.max((item.probability / peak) * 100, 2)}%` }}
              />
            </div>
            <span className="tabular w-12 shrink-0 text-right text-sm text-ink">
              {(item.probability * 100).toFixed(0)}%
            </span>
          </div>
        ))}
      </div>

      {company.caps_applied.length ? (
        <div className="flex items-start gap-2 rounded-lg border border-negative/30 bg-negative/10 px-3 py-2">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-negative" />
          <div className="text-xs text-negative">
            <p className="font-medium">
              Capped at {company.max_grade_cap} by {company.caps_applied.length} rule
              {company.caps_applied.length === 1 ? '' : 's'}
            </p>
            <p className="mt-0.5 opacity-90">{company.caps_applied.join(' · ')}</p>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3 text-xs text-ink-faint">
        {company.is_user_override ? (
          <Badge tone="brand">
            <UserPen className="size-3" /> Your numbers, not the model's
          </Badge>
        ) : baseGrade !== null ? (
          <span>Condition sub-scores put this card at {baseGrade.toFixed(1)}/10 before rules.</span>
        ) : null}
      </div>
    </div>
  )
}
