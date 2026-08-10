import { useState } from 'react'
import type { Centering, ConditionAssessment, ConditionWrite, FaceDefects, Severity } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Field, Input, Label, Textarea } from '@/components/ui/field'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'

const DEFECT_GROUPS: { title: string; fields: (keyof FaceDefects)[] }[] = [
  { title: 'Corners', fields: ['corner_tl', 'corner_tr', 'corner_bl', 'corner_br'] },
  { title: 'Edges', fields: ['edge_condition', 'whitening', 'silvering'] },
  {
    title: 'Surface',
    fields: [
      'surface_condition',
      'holo_condition',
      'scratches',
      'print_lines',
      'dents',
      'dimpling',
      'creases',
      'staining',
      'misc_defects',
    ],
  },
]

const FIELD_LABELS: Record<string, string> = {
  corner_tl: 'Top left',
  corner_tr: 'Top right',
  corner_bl: 'Bottom left',
  corner_br: 'Bottom right',
  edge_condition: 'Edge wear',
  whitening: 'Whitening',
  silvering: 'Silvering',
  surface_condition: 'Surface',
  holo_condition: 'Holo',
  scratches: 'Scratches',
  print_lines: 'Print lines',
  dents: 'Dents',
  dimpling: 'Dimpling',
  creases: 'Creases',
  staining: 'Staining',
  misc_defects: 'Other',
}

const SEVERITIES: { value: Severity; label: string; tone: string }[] = [
  { value: 'none', label: 'None', tone: 'data-[on=true]:bg-positive/20 data-[on=true]:text-positive' },
  { value: 'minor', label: 'Minor', tone: 'data-[on=true]:bg-caution/20 data-[on=true]:text-caution' },
  {
    value: 'moderate',
    label: 'Moderate',
    tone: 'data-[on=true]:bg-negative/15 data-[on=true]:text-negative',
  },
  {
    value: 'severe',
    label: 'Severe',
    tone: 'data-[on=true]:bg-negative/30 data-[on=true]:text-negative',
  },
]

const BLANK_FACE = Object.fromEntries(
  Object.keys(FIELD_LABELS).map((field) => [field, 'unknown']),
) as unknown as FaceDefects

const BLANK_CENTERING: Centering = { left: null, right: null, top: null, bottom: null }

export function ConditionForm({
  existing,
  onSubmit,
  onCancel,
  submitting,
}: {
  existing?: ConditionAssessment
  onSubmit: (payload: ConditionWrite) => void
  onCancel: () => void
  submitting?: boolean
}) {
  const [front, setFront] = useState<FaceDefects>({ ...BLANK_FACE, ...(existing?.front ?? {}) })
  const [back, setBack] = useState<FaceDefects>({ ...BLANK_FACE, ...(existing?.back ?? {}) })
  const [frontCentering, setFrontCentering] = useState<Centering>(
    existing?.centering.front ?? BLANK_CENTERING,
  )
  const [backCentering, setBackCentering] = useState<Centering>(
    existing?.centering.back ?? BLANK_CENTERING,
  )
  const [notes, setNotes] = useState(existing?.notes ?? '')

  const submit = () => {
    onSubmit({
      front,
      back,
      centering: { front: frontCentering, back: backCentering },
      notes: notes || null,
    })
  }

  const answered =
    [...Object.keys(FIELD_LABELS)].filter(
      (field) => front[field as keyof FaceDefects] !== 'unknown',
    ).length +
    [...Object.keys(FIELD_LABELS)].filter((field) => back[field as keyof FaceDefects] !== 'unknown')
      .length

  return (
    <div className="space-y-4 px-5 py-5">
      <Tabs defaultValue="front">
        <div className="flex items-center justify-between gap-4">
          <TabsList>
            <TabsTrigger value="front">Front</TabsTrigger>
            <TabsTrigger value="back">Back</TabsTrigger>
          </TabsList>
          <p className="text-xs text-ink-faint">
            {answered} of 32 answered — anything left blank counts as unknown, not perfect
          </p>
        </div>

        <TabsContent value="front" className="mt-4 space-y-5">
          <CenteringInputs
            title="Front centering"
            hint="Border widths or percentages — only the ratio matters. A 55/45 front is the usual limit for a 10."
            value={frontCentering}
            onChange={setFrontCentering}
          />
          <DefectGrid face={front} onChange={setFront} />
        </TabsContent>

        <TabsContent value="back" className="mt-4 space-y-5">
          <CenteringInputs
            title="Back centering"
            hint="Backs are judged more leniently — 75/25 is commonly still a 10."
            value={backCentering}
            onChange={setBackCentering}
          />
          <DefectGrid face={back} onChange={setBack} />
        </TabsContent>
      </Tabs>

      <Field label="Notes">
        <Textarea
          rows={2}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Anything the checkboxes do not capture…"
        />
      </Field>

      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" onClick={submit} disabled={submitting}>
          {submitting ? 'Saving…' : 'Save assessment'}
        </Button>
      </div>
    </div>
  )
}

function CenteringInputs({
  title,
  hint,
  value,
  onChange,
}: {
  title: string
  hint: string
  value: Centering
  onChange: (value: Centering) => void
}) {
  const set = (key: keyof Centering) => (event: React.ChangeEvent<HTMLInputElement>) => {
    const raw = event.target.value
    onChange({ ...value, [key]: raw === '' ? null : Number(raw) })
  }

  return (
    <div className="space-y-2">
      <div>
        <Label>{title}</Label>
        <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(['left', 'right', 'top', 'bottom'] as const).map((edge) => (
          <div key={edge}>
            <Label className="capitalize">{edge}</Label>
            <Input
              type="number"
              step="0.1"
              min={0}
              max={100}
              value={value[edge] ?? ''}
              onChange={set(edge)}
              placeholder="50"
              className="mt-1"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

function DefectGrid({
  face,
  onChange,
}: {
  face: FaceDefects
  onChange: (value: FaceDefects) => void
}) {
  return (
    <div className="space-y-4">
      {DEFECT_GROUPS.map((group) => (
        <div key={group.title} className="space-y-2">
          <Label>{group.title}</Label>
          <div className="space-y-1.5">
            {group.fields.map((field) => (
              <div key={String(field)} className="flex items-center justify-between gap-3">
                <span className="text-sm text-ink-muted">{FIELD_LABELS[String(field)]}</span>
                <div className="flex gap-1">
                  {SEVERITIES.map((severity) => {
                    const on = face[field] === severity.value
                    return (
                      <button
                        key={severity.value}
                        type="button"
                        data-on={on}
                        onClick={() => onChange({ ...face, [field]: severity.value })}
                        className={cn(
                          'rounded-md border border-line px-2 py-1 text-xs text-ink-faint transition-colors hover:text-ink',
                          severity.tone,
                          on && 'border-transparent font-medium',
                        )}
                      >
                        {severity.label}
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
