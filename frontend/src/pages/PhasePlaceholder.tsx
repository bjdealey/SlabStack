import type { ReactNode } from 'react'
import { PageHeader } from '@/components/AppShell'
import { Panel, PanelBody, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { Badge } from '@/components/ui/badge'

/**
 * A named page for work that is modelled but not built. It states what already
 * exists in the database, so the phase reads as scheduled rather than missing.
 */
export function PhasePlaceholder({
  title,
  phase,
  summary,
  ready,
  remaining,
  icon,
}: {
  title: string
  phase: number
  summary: string
  ready: string[]
  remaining: string[]
  icon?: ReactNode
}) {
  return (
    <>
      <PageHeader title={title} description={summary} />
      <div className="grid gap-6 p-6 lg:grid-cols-2">
        <Panel>
          <PanelHeader>
            <div>
              <PanelTitle className="flex items-center gap-2">
                {icon}
                Already in place
              </PanelTitle>
            </div>
            <Badge tone="positive">Built</Badge>
          </PanelHeader>
          <PanelBody>
            <ul className="space-y-2 text-sm text-ink-muted">
              {ready.map((item) => (
                <li key={item}>• {item}</li>
              ))}
            </ul>
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader>
            <div>
              <PanelTitle>Still to build</PanelTitle>
            </div>
            <Badge tone="neutral">Phase {phase}</Badge>
          </PanelHeader>
          <PanelBody>
            <ul className="space-y-2 text-sm text-ink-muted">
              {remaining.map((item) => (
                <li key={item}>• {item}</li>
              ))}
            </ul>
          </PanelBody>
        </Panel>
      </div>
    </>
  )
}

export function SubmissionsPage() {
  return (
    <PhasePlaceholder
      title="Submissions"
      phase={6}
      summary="Batching cards into grading submissions and allocating the shared costs."
      ready={[
        'Grading companies, tiers, minimums and value ceilings are configurable rows',
        'Memberships modelled so "does membership pay for itself?" is answerable',
        'Submission and per-card tables exist, including declared value and allocated overhead',
        'Penny-exact cost allocation (equal and value-weighted) is implemented and tested',
      ]}
      remaining={[
        'Batch builder with drag-and-drop between submissions',
        'Whole-collection optimisation against tier minimums and value ceilings',
        'Membership break-even comparison across a planned submission',
        'Submission cost and expected-value summary',
      ]}
    />
  )
}

export function AnalyticsPage() {
  return (
    <PhasePlaceholder
      title="Analytics"
      phase={7}
      summary="Ranked opportunities, price history and how your predictions have actually performed."
      ready={[
        'Price snapshots table, so your own long-term history builds even without a provider',
        'Sales, listings and derived prices are stored with sample size and confidence',
        'Predicted-vs-actual results table for the calibration loop',
      ]}
      remaining={[
        'Best grading opportunities, ranked by risk-adjusted profit',
        'Raw selling queue with suggested listing prices',
        'Price, volume and liquidity charts per card',
        'Predicted vs actual grade accuracy and personal grading bias',
      ]}
    />
  )
}
