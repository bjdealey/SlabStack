import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Banknote } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { Card, Disposal } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input, Select } from '@/components/ui/field'
import { formatDate, formatMoney } from '@/lib/utils'

const today = () => new Date().toISOString().slice(0, 10)

/**
 * Record what a card actually sold for.
 *
 * Every other number on the card page is a projection — what it is worth, what
 * grading would cost, what a sale would net. This is the one that is not, and
 * it is what lets the application find out whether its profit predictions were
 * any good rather than only its grade predictions.
 *
 * The form is deliberately short. Costs are estimated from the selling profile,
 * so the common case is a price and a date; the payout box exists for when you
 * have the statement, and what you type there wins over every estimate because
 * a statement is a fact and a fee model is not.
 */
export function SoldDialog({
  card,
  open,
  onOpenChange,
}: {
  card: Card
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const existing = useQuery({
    queryKey: keys.disposal(card.id),
    queryFn: () => api.getDisposal(card.id),
    enabled: open,
  })

  const [soldOn, setSoldOn] = useState(today())
  const [gross, setGross] = useState('')
  const [net, setNet] = useState('')
  const [graded, setGraded] = useState(false)
  const [gradeLabel, setGradeLabel] = useState('raw')
  const [gradingCost, setGradingCost] = useState('')

  const record = useMutation({
    mutationFn: () =>
      api.recordDisposal(card.id, {
        sold_on: soldOn,
        gross: Number(gross),
        sold_graded: graded,
        grade_label: graded ? gradeLabel : 'raw',
        net_proceeds: net ? Number(net) : null,
        grading_cost: gradingCost ? Number(gradingCost) : null,
      }),
    onSuccess: () => {
      toast.success('Sale recorded')
      queryClient.invalidateQueries()
      onOpenChange(false)
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not record the sale'),
  })

  const undo = useMutation({
    mutationFn: () => api.deleteDisposal(card.id),
    onSuccess: () => {
      toast.success('Sale removed — the card is back in your collection')
      queryClient.invalidateQueries()
      onOpenChange(false)
    },
  })

  const sale = existing.data

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title={sale ? 'This card is sold' : 'Record a sale'}
        description={
          sale
            ? 'What it actually fetched. Every other figure in the app is an estimate; this one happened.'
            : 'A price and a date is enough — fees and postage are estimated from your selling profile. Enter the payout if you have the statement.'
        }
      >
        {sale ? (
          <div className="space-y-3 px-5 py-4">
            <div className="rounded-lg border border-line px-4 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="tabular text-2xl font-semibold text-ink">
                  {formatMoney(sale.net_proceeds, sale.currency)}
                </span>
                <span className="text-xs text-ink-muted">
                  net, from {formatMoney(sale.gross, sale.currency)} gross
                </span>
                <Badge tone={sale.net_is_user_entered ? 'positive' : 'neutral'}>
                  {sale.net_is_user_entered ? 'your payout' : 'estimated'}
                </Badge>
              </div>
              <p className="pt-1 text-xs text-ink-faint">
                Sold {formatDate(sale.sold_on)} as {sale.grade_label}
                {sale.grading_cost !== null
                  ? `, grading cost ${formatMoney(sale.grading_cost, sale.currency)}`
                  : sale.sold_graded
                    ? ', grading cost not recorded — the realised profit cannot be completed without it'
                    : ''}
                .
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Close
              </Button>
              <Button variant="danger" onClick={() => undo.mutate()} disabled={undo.isPending}>
                Undo the sale
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-4 px-5 py-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Sold on">
                  <Input type="date" value={soldOn} onChange={(e) => setSoldOn(e.target.value)} />
                </Field>
                <Field label="Sale price" hint="What the buyer paid for the card.">
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={gross}
                    onChange={(e) => setGross(e.target.value)}
                    placeholder="300.00"
                  />
                </Field>
              </div>

              <Field
                label="Payout (optional)"
                hint="What actually reached you. Overrides the estimated fees and postage — a statement is a fact, a fee model is not."
              >
                <Input
                  type="number"
                  step="0.01"
                  min="0"
                  value={net}
                  onChange={(e) => setNet(e.target.value)}
                  placeholder="estimated from your selling profile"
                />
              </Field>

              <Field label="Sold as">
                <Select
                  value={graded ? gradeLabel : 'raw'}
                  onChange={(e) => {
                    const value = e.target.value
                    setGraded(value !== 'raw')
                    setGradeLabel(value)
                  }}
                >
                  <option value="raw">Raw</option>
                  {['PSA 10', 'PSA 9', 'CGC 10', 'CGC 9.5', 'CGC 9', 'ACE 10', 'BGS 10'].map(
                    (label) => (
                      <option key={label} value={label}>
                        {label}
                      </option>
                    ),
                  )}
                </Select>
              </Field>

              {graded ? (
                <Field
                  label="What grading cost"
                  hint="Leave blank only if you genuinely do not know — blank means unrecorded, not free, and the realised profit will say so rather than flatter the decision."
                >
                  <Input
                    type="number"
                    step="0.01"
                    min="0"
                    value={gradingCost}
                    onChange={(e) => setGradingCost(e.target.value)}
                    placeholder="24.60"
                  />
                </Field>
              ) : null}
            </div>

            <div className="flex justify-end gap-2 border-t border-line px-5 py-4">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                disabled={!gross || !soldOn || record.isPending}
                onClick={() => record.mutate()}
              >
                <Banknote /> Record the sale
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export type { Disposal }
