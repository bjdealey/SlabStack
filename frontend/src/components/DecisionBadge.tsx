import { Badge } from '@/components/ui/badge'
import type { BlockStatus, Confidence, Decision } from '@/lib/types'
import { cn } from '@/lib/utils'

const DECISION_LABELS: Record<Decision, string> = {
  grade: 'Grade',
  grade_if_batch_filled: 'Grade if batch filled',
  sell_raw: 'Sell raw',
  keep_raw: 'Keep raw',
  hold: 'Hold',
  do_not_grade: 'Do not grade',
  insufficient_data: 'Not enough data',
}

const DECISION_TONES: Record<Decision, 'positive' | 'caution' | 'negative' | 'brand' | 'neutral'> = {
  grade: 'positive',
  grade_if_batch_filled: 'caution',
  sell_raw: 'brand',
  keep_raw: 'neutral',
  hold: 'caution',
  do_not_grade: 'negative',
  insufficient_data: 'neutral',
}

export function DecisionBadge({
  decision,
  className,
}: {
  decision: Decision
  className?: string
}) {
  return (
    <Badge tone={DECISION_TONES[decision]} className={cn('uppercase tracking-wide', className)}>
      {DECISION_LABELS[decision]}
    </Badge>
  )
}

const STATUS_LABELS: Record<BlockStatus, string> = {
  ok: 'Ready',
  partial: 'Partial',
  not_assessed: 'Not assessed',
  insufficient_data: 'No data',
  not_implemented: 'Later phase',
}

const STATUS_TONES: Record<BlockStatus, 'positive' | 'caution' | 'neutral' | 'outline'> = {
  ok: 'positive',
  partial: 'caution',
  not_assessed: 'outline',
  insufficient_data: 'outline',
  not_implemented: 'neutral',
}

export function StatusBadge({ status, phase }: { status: BlockStatus; phase?: number | null }) {
  const label =
    status === 'not_implemented' && phase ? `Phase ${phase}` : STATUS_LABELS[status] ?? status
  return <Badge tone={STATUS_TONES[status] ?? 'neutral'}>{label}</Badge>
}

const CONFIDENCE_TONES: Record<Confidence, 'positive' | 'caution' | 'negative' | 'outline'> = {
  high: 'positive',
  medium: 'caution',
  low: 'negative',
  none: 'outline',
}

/**
 * Spec section 36: a number without its confidence is false precision, so
 * confidence travels next to every figure that has one.
 */
export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <Badge tone={CONFIDENCE_TONES[confidence]} className="uppercase tracking-wide">
      {confidence === 'none' ? 'no confidence' : `${confidence} confidence`}
    </Badge>
  )
}
