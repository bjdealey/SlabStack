import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The dashboard's unit of information: one big number, one label, and — when
 * the number is not calculable yet — the reason, rather than a misleading zero.
 */
export function StatTile({
  label,
  value,
  hint,
  tone = 'neutral',
  icon,
  pending,
  className,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'neutral' | 'brand' | 'positive' | 'caution' | 'negative'
  icon?: ReactNode
  pending?: boolean
  className?: string
}) {
  const toneClass = {
    neutral: 'text-ink',
    brand: 'text-brand',
    positive: 'text-positive',
    caution: 'text-caution',
    negative: 'text-negative',
  }[tone]

  return (
    <div
      className={cn(
        'rounded-[var(--radius-card)] border border-line bg-surface px-4 py-3.5',
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[0.7rem] font-medium uppercase tracking-wider text-ink-faint">{label}</p>
        {icon ? <span className="text-ink-faint">{icon}</span> : null}
      </div>
      <p
        className={cn(
          'tabular mt-1.5 text-2xl font-semibold leading-tight',
          pending ? 'text-ink-faint' : toneClass,
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs leading-snug text-ink-faint">{hint}</p> : null}
    </div>
  )
}
