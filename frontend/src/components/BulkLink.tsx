import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link2, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '@/lib/api'
import type { DataSource, LinkOutcome, LinkReport } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn, formatNumber } from '@/lib/utils'

const STATUS_TONES: Record<string, 'positive' | 'caution' | 'negative' | 'neutral'> = {
  linked: 'positive',
  ambiguous: 'caution',
  skipped: 'neutral',
  failed: 'negative',
}

/**
 * Link a whole collection to one source, in two steps.
 *
 * Per-card linking is the most tedious thing in this application — a hundred
 * cards and two sources is two hundred searches, each ending in a click on the
 * obvious answer. But the tedium is not the risk. A wrong link is silent and
 * lasting: every future refresh prices a different printing, the figures stay
 * plausible, and nothing ever says so.
 *
 * So this previews first and writes second. The preview is a real run against
 * the source that happens to change nothing, which means what you approve is
 * what was actually found rather than an estimate of it.
 */
export function BulkLink({ sources }: { sources: DataSource[] }) {
  const queryClient = useQueryClient()
  const [source, setSource] = useState('pokemontcg_io')
  const [preview, setPreview] = useState<LinkReport | null>(null)

  const searchable = sources.filter(
    (row) => row.enabled && row.has_adapter && !['manual', 'csv'].includes(row.code),
  )

  const run = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.linkAll({ source_code: source, dry_run: dryRun, limit: 200 }),
    onSuccess: (report) => {
      setPreview(report)
      // A source that could not be reached returns a report too, and it must not
      // be read as a verdict on the collection: "nothing was certain enough to
      // link" blames the cards for what was actually a configuration failure.
      if (report.status === 'error') {
        toast.error(report.reason ?? `${report.source_name} could not be searched`)
        return
      }
      if (report.dry_run) {
        toast.success(
          report.linked
            ? `${report.linked} card(s) can be linked — nothing written yet`
            : 'Nothing was certain enough to link',
        )
      } else {
        toast.success(report.linked ? `Linked ${report.linked} card(s)` : 'Nothing was linked')
        queryClient.invalidateQueries()
      }
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'The link pass failed'),
  })

  if (!searchable.length) return null

  return (
    <div className="rounded-lg border border-line px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-sm font-medium text-ink">
            <Link2 className="size-3.5" /> Link the whole collection
          </span>
          <span className="block text-xs leading-relaxed text-ink-faint">
            Searches once per unlinked card and stores the provider&rsquo;s id where the match is
            beyond doubt. Anything less is handed back with its candidates rather than guessed at.
          </span>
        </span>

        {searchable.length > 1 ? (
          <select
            value={source}
            onChange={(event) => {
              setSource(event.target.value)
              setPreview(null)
            }}
            className="shrink-0 rounded-md border border-line bg-canvas px-2 py-1 text-xs text-ink"
          >
            {searchable.map((row) => (
              <option key={row.code} value={row.code}>
                {row.name}
              </option>
            ))}
          </select>
        ) : null}

        <Button
          size="sm"
          variant="secondary"
          disabled={run.isPending}
          onClick={() => run.mutate(true)}
        >
          {run.isPending ? 'Checking…' : 'Preview'}
        </Button>
      </div>

      {preview ? (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          {/* A run that never happened has no tallies. Four zeros above an error
              read as "we looked and found nothing", which is a claim about the
              collection rather than about the source. */}
          {preview.status !== 'error' ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
              <span>
                <strong className="text-ink">{formatNumber(preview.linked)}</strong> ready to link
              </span>
              <span>· {formatNumber(preview.ambiguous)} need a decision</span>
              <span>· {formatNumber(preview.skipped)} skipped</span>
              {preview.failed ? <span>· {formatNumber(preview.failed)} failed</span> : null}
              {preview.dry_run && preview.linked ? (
                <Button
                  size="sm"
                  variant="primary"
                  className="ml-auto"
                  disabled={run.isPending}
                  onClick={() => run.mutate(false)}
                >
                  Link {formatNumber(preview.linked)} card(s)
                </Button>
              ) : null}
            </div>
          ) : null}

          {preview.reason ? (
            <p className="text-[0.7rem] leading-relaxed text-ink-faint">{preview.reason}</p>
          ) : null}
          {preview.notes.map((note) => (
            <p
              key={note}
              className="flex items-start gap-1.5 text-[0.7rem] leading-relaxed text-caution"
            >
              <TriangleAlert className="mt-px size-3 shrink-0" />
              {note}
            </p>
          ))}

          {/* Only the ones that need a person. A list of successes is noise; a
              list of near-misses is the actual work left to do. */}
          {preview.cards.filter((card) => card.status !== 'linked').length ? (
            <div className="max-h-64 divide-y divide-line overflow-y-auto rounded-lg border border-line">
              {preview.cards
                .filter((card) => card.status !== 'linked')
                .map((card) => (
                  <Outcome key={card.card_id} card={card} />
                ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function Outcome({ card }: { card: LinkOutcome }) {
  return (
    <div className="px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{card.name}</span>
        <Badge tone={STATUS_TONES[card.status] ?? 'neutral'}>{card.status}</Badge>
      </div>
      {card.reason ? (
        <p className="pt-0.5 text-[0.7rem] leading-relaxed text-ink-faint">{card.reason}</p>
      ) : null}
      {card.candidates.length ? (
        <ul className="pt-1 text-[0.7rem] text-ink-faint">
          {card.candidates.map((candidate) => (
            <li key={candidate.external_id} className="flex items-center gap-2">
              <span
                className={cn(
                  'tabular w-9 shrink-0 text-right',
                  candidate.confidence >= 0.7 ? 'text-ink-muted' : '',
                )}
              >
                {Math.round(candidate.confidence * 100)}%
              </span>
              <span className="truncate">
                {candidate.name}
                {candidate.set_name ? ` · ${candidate.set_name}` : ''}
                {candidate.card_number ? ` · ${candidate.card_number}` : ''}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
