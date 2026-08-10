import { Github, Info, RotateCcw } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'

const REPO = 'https://github.com/bjdealey/SlabStack'

/**
 * Shown only in the GitHub Pages build.
 *
 * Anyone landing on a hosted URL will reasonably assume their data is being
 * saved somewhere. It is not — the demo runs entirely in the tab — and saying
 * so plainly matters more than the banner costing a strip of screen.
 */
export function DemoBanner() {
  const queryClient = useQueryClient()

  const reset = async () => {
    const { resetDemo } = await import('@/lib/demo')
    resetDemo()
    await queryClient.invalidateQueries()
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-brand/30 bg-brand/10 px-6 py-2.5 text-xs">
      <p className="flex items-center gap-2 text-ink">
        <Info className="size-4 shrink-0 text-brand" />
        <span>
          <strong className="font-semibold">Demo.</strong> Everything runs in this tab against a
          sample collection — nothing is uploaded, and a refresh starts over. The real app runs
          locally with FastAPI and SQLite, and your collection never leaves your machine.
        </span>
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={reset}>
          <RotateCcw /> Reset data
        </Button>
        <Button size="sm" variant="secondary" asChild>
          <a href={REPO} target="_blank" rel="noreferrer">
            <Github /> Source &amp; docs
          </a>
        </Button>
      </div>
    </div>
  )
}
