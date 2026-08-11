import type { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Boxes,
  Database,
  LayoutDashboard,
  PackageCheck,
  Settings2,
  TrendingUp,
} from 'lucide-react'
import { api, keys } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { DemoBanner } from '@/components/DemoBanner'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  phase?: number
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: <LayoutDashboard className="size-4" /> },
  { to: '/collection', label: 'Collection', icon: <Boxes className="size-4" /> },
  { to: '/submissions', label: 'Submissions', icon: <PackageCheck className="size-4" /> },
  { to: '/analytics', label: 'Analytics', icon: <TrendingUp className="size-4" /> },
  { to: '/settings', label: 'Settings', icon: <Settings2 className="size-4" /> },
]

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const health = useQuery({ queryKey: keys.health, queryFn: api.health, retry: false })

  return (
    <div className="flex min-h-screen bg-canvas">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-surface md:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="grid size-8 place-items-center rounded-lg bg-brand text-sm font-bold text-canvas">
            S
          </div>
          <div>
            <p className="text-sm font-semibold leading-none text-ink">SlabStack</p>
            <p className="mt-1 text-[0.65rem] uppercase tracking-wider text-ink-faint">
              Grading decisions
            </p>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-3">
          {NAV.map((item) => {
            const active =
              item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={cn(
                  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                  active
                    ? 'bg-surface-raised font-medium text-ink'
                    : 'text-ink-muted hover:bg-surface-raised/60 hover:text-ink',
                )}
              >
                {item.icon}
                <span className="flex-1">{item.label}</span>
                {item.phase ? (
                  <span className="text-[0.6rem] uppercase text-ink-faint">P{item.phase}</span>
                ) : null}
              </NavLink>
            )
          })}
        </nav>

        <div className="space-y-2 border-t border-line px-5 py-4 text-[0.7rem] text-ink-faint">
          <div className="flex items-center gap-2">
            <Database className="size-3.5" />
            <span>{health.isSuccess ? 'Local SQLite' : 'API unreachable'}</span>
          </div>
          {health.isSuccess ? (
            <>
              <p>{health.data.cards.toLocaleString()} cards stored</p>
              <Badge tone="outline">Phase {health.data.phase}</Badge>
            </>
          ) : null}
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        {import.meta.env.VITE_DEMO === 'true' ? <DemoBanner /> : null}
        {children}
      </main>
    </div>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-line bg-surface/40 px-6 py-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-1 text-sm text-ink-faint">{description}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  )
}
