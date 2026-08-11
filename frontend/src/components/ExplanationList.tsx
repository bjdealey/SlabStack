import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'
import type { ExplanationItem } from '@/lib/types'

const ICONS = {
  pass: <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-positive" />,
  warn: <AlertTriangle className="mt-0.5 size-4 shrink-0 text-caution" />,
  fail: <XCircle className="mt-0.5 size-4 shrink-0 text-negative" />,
  info: <Info className="mt-0.5 size-4 shrink-0 text-ink-faint" />,
}

/**
 * The "Why?" panel from spec section 30. Every recommendation has to be able to
 * show its working, or the user has no reason to trust it.
 */
export function ExplanationList({ items }: { items: ExplanationItem[] }) {
  if (!items.length) return null
  return (
    <ul className="space-y-2.5">
      {items.map((item, index) => (
        <li key={`${item.text}-${index}`} className="flex gap-2.5 text-sm">
          {ICONS[item.kind]}
          <div className="min-w-0">
            <p className="text-ink">{item.text}</p>
            {item.detail ? <p className="mt-0.5 text-xs text-ink-faint">{item.detail}</p> : null}
          </div>
        </li>
      ))}
    </ul>
  )
}
