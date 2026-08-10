import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { useQuery } from '@tanstack/react-query'
import { api, keys } from '@/lib/api'
import type { Card, CardWrite } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Field, Input, Select, Textarea } from '@/components/ui/field'

// Only the name is required. A collection gets entered in a hurry, and a card
// with a name is more useful than a card that was never added because the form
// demanded a rarity.
const schema = z.object({
  name: z.string().min(1, 'Give the card a name'),
  set_code: z.string().optional(),
  card_number: z.string().optional(),
  variant: z.string().optional(),
  language: z.string(),
  printing: z.string().optional(),
  rarity: z.string().optional(),
  pokemon: z.string().optional(),
  is_promo: z.boolean(),
  raw_condition: z.string().optional(),
  quantity: z.coerce.number().int().min(1).max(10000),
  purchase_price: z.union([z.coerce.number().min(0), z.literal('')]).optional(),
  user_raw_value: z.union([z.coerce.number().min(0), z.literal('')]).optional(),
  purchase_date: z.string().optional(),
  status: z.string(),
  notes: z.string().optional(),
})

type FormValues = z.input<typeof schema>

function toPayload(values: FormValues): CardWrite {
  const number = (value: unknown) =>
    value === '' || value === undefined || value === null ? null : Number(value)
  const text = (value: unknown) => (value === '' || value === undefined ? null : String(value))

  return {
    name: values.name.trim(),
    set_code: text(values.set_code),
    card_number: text(values.card_number),
    variant: text(values.variant),
    language: values.language,
    printing: text(values.printing),
    rarity: text(values.rarity),
    pokemon: text(values.pokemon),
    is_promo: values.is_promo,
    raw_condition: text(values.raw_condition) ?? 'Unknown',
    quantity: Number(values.quantity),
    purchase_price: number(values.purchase_price),
    user_raw_value: number(values.user_raw_value),
    purchase_date: text(values.purchase_date),
    status: values.status,
    notes: text(values.notes),
  }
}

export function CardForm({
  card,
  onSubmit,
  onCancel,
  submitting,
}: {
  card?: Card
  onSubmit: (payload: CardWrite) => void
  onCancel: () => void
  submitting?: boolean
}) {
  const sets = useQuery({ queryKey: keys.sets(), queryFn: () => api.listSets() })
  const variants = useQuery({ queryKey: keys.variants, queryFn: api.listVariants })
  const enums = useQuery({ queryKey: keys.enums, queryFn: api.enums })

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: card?.name ?? '',
      set_code: card?.set_code ?? '',
      card_number: card?.card_number ?? '',
      variant: card?.variant ?? '',
      language: card?.language ?? 'English',
      printing: card?.printing ?? 'Unlimited',
      rarity: card?.rarity ?? '',
      pokemon: card?.pokemon ?? '',
      is_promo: card?.is_promo ?? false,
      raw_condition: card?.raw_condition ?? 'Unknown',
      quantity: card?.quantity ?? 1,
      purchase_price: card?.purchase_price ?? '',
      user_raw_value: card?.user_raw_value ?? '',
      purchase_date: card?.purchase_date ?? '',
      status: card?.status ?? 'in_collection',
      notes: card?.notes ?? '',
    },
  })

  const languages = enums.data?.enums.language ?? ['English']
  const printings = enums.data?.enums.printing ?? []
  const conditions = enums.data?.enums.raw_condition ?? []
  const statuses = enums.data?.enums.card_status ?? []

  return (
    <form onSubmit={handleSubmit((values) => onSubmit(toPayload(values)))} className="space-y-5 px-5 py-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Card name" error={errors.name?.message} className="sm:col-span-2">
          <Input placeholder="Umbreon VMAX" autoFocus {...register('name')} />
        </Field>

        <Field label="Set" hint="Type a code — known sets fill in their name">
          <Input list="set-codes" placeholder="EVS" {...register('set_code')} />
          <datalist id="set-codes">
            {(sets.data ?? []).map((item) => (
              <option key={item.id} value={item.code}>
                {item.name}
              </option>
            ))}
          </datalist>
        </Field>

        <Field label="Card number">
          <Input placeholder="215/203" {...register('card_number')} />
        </Field>

        <Field label="Variant" hint="Alt art and reverse holo are separate markets">
          <Input list="variant-names" placeholder="Alternate Art" {...register('variant')} />
          <datalist id="variant-names">
            {(variants.data ?? []).map((item) => (
              <option key={item.id} value={item.name} />
            ))}
          </datalist>
        </Field>

        <Field label="Language">
          <Select {...register('language')}>
            {languages.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Printing">
          <Select {...register('printing')}>
            <option value="">—</option>
            {printings.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Rarity">
          <Input placeholder="Secret Rare" {...register('rarity')} />
        </Field>

        <Field label="Pokémon">
          <Input placeholder="Umbreon" {...register('pokemon')} />
        </Field>

        <Field label="Raw condition" hint="A quick label — the real detail is the assessment">
          <Select {...register('raw_condition')}>
            {conditions.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>

        <Field
          label="Quantity"
          hint="Split to one row per copy before grading"
        >
          <Input type="number" min={1} {...register('quantity')} />
        </Field>

        <Field label="Status">
          <Select {...register('status')}>
            {statuses.map((value) => (
              <option key={value} value={value}>
                {value.replace(/_/g, ' ')}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Purchase price" hint="What you paid, per copy">
          <Input type="number" step="0.01" min={0} placeholder="185.00" {...register('purchase_price')} />
        </Field>

        <Field label="Purchase date">
          <Input type="date" {...register('purchase_date')} />
        </Field>

        <Field
          label="Your raw value estimate"
          hint="Kept separate from any market figure"
          className="sm:col-span-2"
        >
          <Input type="number" step="0.01" min={0} placeholder="210.00" {...register('user_raw_value')} />
        </Field>

        <Field label="Notes" className="sm:col-span-2">
          <Textarea rows={3} placeholder="Where it came from, anything unusual…" {...register('notes')} />
        </Field>

        <label className="flex items-center gap-2 text-sm text-ink-muted sm:col-span-2">
          <input type="checkbox" className="size-4 accent-[var(--color-brand)]" {...register('is_promo')} />
          This is a promo card
        </label>
      </div>

      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" disabled={submitting}>
          {submitting ? 'Saving…' : card ? 'Save changes' : 'Add card'}
        </Button>
      </div>
    </form>
  )
}
