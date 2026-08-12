import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError } from '@/lib/api'
import type { CollectionImport } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Textarea } from '@/components/ui/field'
import { formatNumber } from '@/lib/utils'

const SAMPLE = `Name,Set,Number,Quantity,Condition,Price
Umbreon VMAX,Evolving Skies,215/203,1,NM,310.00
Charizard,Base Set,4/102,1,Lightly Played,1200`

/**
 * Import a collection from somebody else's export.
 *
 * Until this existed the only way in was the Add Card form, one card at a time
 * — fine for the card in your hand, hopeless for the four hundred in a box, and
 * the last thing standing between the engine and a real collection.
 *
 * It previews first and writes second. A bad import is not a wrong answer on
 * screen, it is four hundred rows you now have to find and delete, so the first
 * pass reads the file, says exactly what it found, and changes nothing.
 */
export function ImportCollectionDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [csv, setCsv] = useState('')
  const [skipDuplicates, setSkipDuplicates] = useState(true)
  const [preview, setPreview] = useState<CollectionImport | null>(null)
  const queryClient = useQueryClient()

  const run = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.importCollection({ csv, dry_run: dryRun, skip_duplicates: skipDuplicates }),
    onSuccess: (report) => {
      setPreview(report)
      if (report.status === 'error') {
        toast.error(report.reason ?? 'The file could not be read')
        return
      }
      if (report.dry_run) {
        toast.success(
          report.imported
            ? `${report.imported} card(s) ready — nothing added yet`
            : 'Nothing new to add',
        )
      } else {
        toast.success(report.imported ? `Added ${report.imported} card(s)` : 'Nothing was added')
        queryClient.invalidateQueries()
      }
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'The import failed'),
  })

  const readFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      setCsv(String(reader.result ?? ''))
      setPreview(null)
    }
    reader.readAsText(file)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) setPreview(null)
      }}
    >
      <DialogContent
        title="Import a collection"
        description="Paste a CSV or choose a file. Column names are matched loosely, so most exports work unchanged — a card name is the only thing a row cannot do without."
      >
        <div className="space-y-4 px-5 py-4">
          <Field label="CSV file">
            <input
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) readFile(file)
              }}
              className="w-full text-xs text-ink-muted file:mr-3 file:rounded-md file:border file:border-line file:bg-surface-raised file:px-3 file:py-1.5 file:text-xs file:text-ink"
            />
          </Field>

          <Field
            label="Or paste it"
            hint={`Understood columns include name, set, number, quantity, condition, language, foil, rarity and price. Example:\n${SAMPLE}`}
          >
            <Textarea
              value={csv}
              onChange={(event) => {
                setCsv(event.target.value)
                setPreview(null)
              }}
              className="min-h-40 font-mono text-xs"
              placeholder={SAMPLE}
            />
          </Field>

          <label className="flex items-start gap-2 text-xs text-ink-muted">
            <input
              type="checkbox"
              checked={skipDuplicates}
              onChange={(event) => {
                setSkipDuplicates(event.target.checked)
                setPreview(null)
              }}
              className="mt-0.5"
            />
            <span>
              Skip cards I already have.
              <span className="block text-ink-faint">
                Turn this off only if you genuinely bought a second copy — the engine decides per
                physical card, so two copies are two rows.
              </span>
            </span>
          </label>

          {preview ? <Report report={preview} /> : null}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {preview && !preview.dry_run ? 'Done' : 'Cancel'}
          </Button>
          <Button
            variant="secondary"
            disabled={!csv.trim() || run.isPending}
            onClick={() => run.mutate(true)}
          >
            {run.isPending ? 'Reading…' : 'Preview'}
          </Button>
          {preview?.dry_run && preview.imported ? (
            <Button variant="primary" disabled={run.isPending} onClick={() => run.mutate(false)}>
              Add {formatNumber(preview.imported)} card(s)
            </Button>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Report({ report }: { report: CollectionImport }) {
  const unreadable = report.errors.length
  return (
    <div className="space-y-2 rounded-lg border border-line px-3 py-2">
      {report.status !== 'error' ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
          <span>
            <strong className="text-ink">{formatNumber(report.imported)}</strong>{' '}
            {report.dry_run ? 'ready to add' : 'added'}
          </span>
          {report.duplicates ? <span>· {formatNumber(report.duplicates)} already held</span> : null}
          {report.failed ? <span>· {formatNumber(report.failed)} unreadable</span> : null}
        </div>
      ) : null}

      {report.reason ? (
        <p className="text-[0.7rem] leading-relaxed text-ink-faint">{report.reason}</p>
      ) : null}

      {report.notes.map((note) => (
        <p
          key={note}
          className="flex items-start gap-1.5 text-[0.7rem] leading-relaxed text-ink-faint"
        >
          <TriangleAlert className="mt-px size-3 shrink-0" />
          {note}
        </p>
      ))}

      {unreadable ? (
        <ul className="max-h-24 space-y-0.5 overflow-y-auto text-[0.7rem] text-caution">
          {report.errors.slice(0, 20).map((error, index) => (
            <li key={`${error.line_number}-${index}`}>
              {error.line_number ? `Line ${error.line_number}: ` : ''}
              {error.message}
            </li>
          ))}
        </ul>
      ) : null}

      {/* The first few rows as we read them. A header matched to the wrong
          column is invisible in a count and obvious in a row. */}
      {report.cards.length ? (
        <div className="max-h-48 overflow-y-auto rounded-md border border-line">
          <table className="w-full text-[0.7rem]">
            <tbody className="divide-y divide-line">
              {report.cards.slice(0, 40).map((card) => (
                <tr key={card.line_number} className={card.duplicate_of ? 'opacity-50' : ''}>
                  <td className="px-2 py-1 text-ink">{card.name}</td>
                  <td className="px-2 py-1 text-ink-faint">{card.set_name ?? card.set_code ?? ''}</td>
                  <td className="tabular px-2 py-1 text-ink-faint">{card.card_number ?? ''}</td>
                  <td className="px-2 py-1 text-ink-faint">
                    {card.quantity > 1 ? `×${card.quantity}` : ''}
                  </td>
                  <td className="px-2 py-1 text-ink-faint">{card.raw_condition}</td>
                  {/* Variant and language are the two fields a mis-matched
                      header corrupts invisibly, and both feed catalog_key —
                      a card filed under the wrong one never finds its prices.
                      Language is shown only when it is not English, so the
                      column stays quiet until it matters. */}
                  <td className="px-2 py-1 text-ink-faint">
                    {[card.variant, card.language !== 'English' ? card.language : null]
                      .filter(Boolean)
                      .join(' · ')}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {card.duplicate_of ? <Badge tone="neutral">held</Badge> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}
