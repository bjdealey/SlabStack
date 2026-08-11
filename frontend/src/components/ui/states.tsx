import type { ReactNode } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Panel } from './panel'

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-4 animate-spin text-ink-faint', className)} />
}

export function LoadingPanel({ label = 'Loading…' }: { label?: string }) {
  return (
    <Panel className="flex items-center justify-center gap-3 px-5 py-12 text-sm text-ink-faint">
      <Spinner />
      {label}
    </Panel>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-surface-raised', className)} />
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
      {icon ? <div className="text-ink-faint">{icon}</div> : null}
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? <p className="max-w-md text-xs text-ink-faint">{description}</p> : null}
      {action}
    </div>
  )
}

/**
 * Shows what actually went wrong. A 501 from a later-phase endpoint is not an
 * error the user caused, so it reads differently from a real failure.
 */
export function ErrorState({ error, className }: { error: unknown; className?: string }) {
  const isApi = error instanceof ApiError
  const notImplemented = isApi && error.isNotImplemented
  const message = isApi ? error.message : 'Something went wrong.'

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-lg border px-4 py-3 text-xs',
        notImplemented
          ? 'border-line bg-surface-raised text-ink-muted'
          : 'border-negative/30 bg-negative/10 text-negative',
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="space-y-1">
        <p>{message}</p>
        {notImplemented && error.phase ? (
          <p className="text-ink-faint">Arrives in Phase {error.phase}.</p>
        ) : null}
      </div>
    </div>
  )
}
