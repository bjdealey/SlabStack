import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium',
  {
    variants: {
      tone: {
        neutral: 'border-line bg-surface-raised text-ink-muted',
        brand: 'border-brand/30 bg-brand/10 text-brand',
        positive: 'border-positive/30 bg-positive/10 text-positive',
        caution: 'border-caution/30 bg-caution/10 text-caution',
        negative: 'border-negative/30 bg-negative/10 text-negative',
        outline: 'border-line text-ink-faint',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}
