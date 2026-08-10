import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'
import { api, ApiError, keys } from '@/lib/api'
import type { GradingTier, SettingDefinition } from '@/lib/types'
import { PageHeader } from '@/components/AppShell'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Field, Input, Select } from '@/components/ui/field'
import { Panel, PanelBody, PanelDescription, PanelHeader, PanelTitle } from '@/components/ui/panel'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ErrorState, LoadingPanel } from '@/components/ui/states'
import { formatMoney, humanise } from '@/lib/utils'

const CATEGORY_LABELS: Record<string, string> = {
  general: 'General',
  thresholds: 'Profit thresholds',
  risk: 'Risk',
  submission: 'Submission',
  market: 'Market analysis',
}

export function Settings() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Everything the decision engine assumes about your costs and risk appetite lives here."
      />
      <div className="p-6">
        <Tabs defaultValue="economics">
          <TabsList>
            <TabsTrigger value="economics">Economics</TabsTrigger>
            <TabsTrigger value="grading">Grading companies</TabsTrigger>
            <TabsTrigger value="selling">Selling costs</TabsTrigger>
            <TabsTrigger value="sources">Data sources</TabsTrigger>
          </TabsList>

          <TabsContent value="economics" className="mt-5">
            <EconomicsSettings />
          </TabsContent>
          <TabsContent value="grading" className="mt-5">
            <GradingSettings />
          </TabsContent>
          <TabsContent value="selling" className="mt-5">
            <SellingSettings />
          </TabsContent>
          <TabsContent value="sources" className="mt-5">
            <DataSourceSettings />
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}

function EconomicsSettings() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: keys.settings, queryFn: api.getSettings })
  const [dirty, setDirty] = useState<Record<string, unknown>>({})

  const save = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.updateSettings(values),
    onSuccess: () => {
      toast.success('Settings saved')
      setDirty({})
      queryClient.invalidateQueries({ queryKey: keys.settings })
      queryClient.invalidateQueries({ queryKey: keys.summary })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not save settings'),
  })

  if (settings.isLoading) return <LoadingPanel />
  if (settings.isError) return <ErrorState error={settings.error} />

  const { values, definitions } = settings.data!
  const categories = [...new Set(definitions.map((d) => d.category))]
  const current = (key: string) => (key in dirty ? dirty[key] : values[key])

  return (
    <div className="space-y-6">
      {categories.map((category) => (
        <Panel key={category}>
          <PanelHeader>
            <div>
              <PanelTitle>{CATEGORY_LABELS[category] ?? humanise(category)}</PanelTitle>
            </div>
          </PanelHeader>
          <PanelBody className="grid gap-4 sm:grid-cols-2">
            {definitions
              .filter((definition) => definition.category === category)
              .map((definition) => (
                <SettingInput
                  key={definition.key}
                  definition={definition}
                  value={current(definition.key)}
                  onChange={(value) => setDirty((prev) => ({ ...prev, [definition.key]: value }))}
                />
              ))}
          </PanelBody>
        </Panel>
      ))}

      {Object.keys(dirty).length ? (
        <div className="sticky bottom-4 flex items-center justify-between gap-4 rounded-[var(--radius-card)] border border-brand/40 bg-surface-raised px-5 py-3 shadow-lg">
          <p className="text-sm text-ink">{Object.keys(dirty).length} unsaved change(s)</p>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => setDirty({})}>
              Discard
            </Button>
            <Button variant="primary" onClick={() => save.mutate(dirty)} disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function SettingInput({
  definition,
  value,
  onChange,
}: {
  definition: SettingDefinition
  value: unknown
  onChange: (value: unknown) => void
}) {
  if (definition.type === 'json') {
    return (
      <Field label={definition.label} hint={definition.description} className="sm:col-span-2">
        <Input
          value={JSON.stringify(value)}
          onChange={(event) => {
            try {
              onChange(JSON.parse(event.target.value))
            } catch {
              /* keep the last valid value until the JSON parses */
            }
          }}
          className="font-mono text-xs"
        />
      </Field>
    )
  }

  if (definition.type === 'enum') {
    return (
      <Field label={definition.label} hint={definition.description}>
        <Select value={String(value ?? '')} onChange={(event) => onChange(event.target.value)}>
          {definition.options.map((option) => (
            <option key={option} value={option}>
              {humanise(option)}
            </option>
          ))}
        </Select>
      </Field>
    )
  }

  const suffix =
    definition.type === 'percent' ? '%' : definition.type === 'money' ? '£' : undefined

  return (
    <Field
      label={suffix ? `${definition.label} (${suffix})` : definition.label}
      hint={definition.description}
    >
      <Input
        type="number"
        step={definition.type === 'integer' ? 1 : 0.1}
        min={definition.minimum ?? undefined}
        max={definition.maximum ?? undefined}
        value={String(value ?? '')}
        onChange={(event) =>
          onChange(definition.type === 'integer' ? Number.parseInt(event.target.value, 10) : Number(event.target.value))
        }
      />
    </Field>
  )
}

function GradingSettings() {
  const queryClient = useQueryClient()
  const companies = useQuery({ queryKey: keys.companies, queryFn: api.listGradingCompanies })

  const saveTier = useMutation({
    mutationFn: ({ tier, changes }: { tier: GradingTier; changes: Record<string, unknown> }) =>
      api.updateTier(tier.id, {
        tier_code: tier.tier_code,
        tier_name: tier.tier_name,
        price: tier.price,
        minimum_cards: tier.minimum_cards,
        active: tier.active,
        ...changes,
      }),
    onSuccess: () => {
      toast.success('Tier updated')
      queryClient.invalidateQueries({ queryKey: keys.companies })
      queryClient.invalidateQueries({ queryKey: ['evaluation'] })
      queryClient.invalidateQueries({ queryKey: keys.summary })
    },
    onError: (error: unknown) =>
      toast.error(error instanceof ApiError ? error.message : 'Could not update the tier'),
  })

  if (companies.isLoading) return <LoadingPanel />
  if (companies.isError) return <ErrorState error={companies.error} />

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3 rounded-lg border border-caution/30 bg-caution/10 px-4 py-3 text-xs text-caution">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" />
        <p>
          Grader pricing changes several times a year. These are starting values — check them against
          each company's current price list. A tier with no price is left inactive so the engine skips
          it rather than costing a submission at zero.
        </p>
      </div>

      {companies.data!.map((company) => (
        <Panel key={company.id}>
          <PanelHeader>
            <div>
              <PanelTitle className="flex items-center gap-2">
                {company.name}
                <Badge tone="outline">{company.code}</Badge>
                {!company.active ? <Badge tone="neutral">Inactive</Badge> : null}
              </PanelTitle>
              <PanelDescription>
                Market recognition {company.market_recognition_score.toFixed(1)}/10
                {company.website ? (
                  <>
                    {' · '}
                    <a
                      href={company.website}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-brand hover:underline"
                    >
                      price list <ExternalLink className="size-3" />
                    </a>
                  </>
                ) : null}
              </PanelDescription>
            </div>
          </PanelHeader>
          <PanelBody className="overflow-x-auto">
            {company.tiers.length === 0 ? (
              <p className="text-xs text-ink-faint">No tiers configured.</p>
            ) : (
              <table className="w-full min-w-2xl text-sm">
                <thead className="text-left text-xs uppercase tracking-wider text-ink-faint">
                  <tr>
                    <th className="pb-2 font-medium">Tier</th>
                    <th className="pb-2 text-right font-medium">Price</th>
                    <th className="pb-2 text-right font-medium">Min cards</th>
                    <th className="pb-2 text-right font-medium">Max value</th>
                    <th className="pb-2 text-right font-medium">Turnaround</th>
                    <th className="pb-2 text-right font-medium">Active</th>
                  </tr>
                </thead>
                <tbody>
                  {company.tiers.map((tier) => (
                    <tr key={tier.id} className="border-t border-line/60">
                      <td className="py-2 text-ink">{tier.tier_name}</td>
                      <td className="py-2 text-right">
                        <Input
                          type="number"
                          step="0.01"
                          min={0}
                          defaultValue={tier.price}
                          onBlur={(event) => {
                            const price = Number(event.target.value)
                            if (price !== tier.price) saveTier.mutate({ tier, changes: { price } })
                          }}
                          className="tabular ml-auto h-8 w-24 text-right"
                        />
                      </td>
                      <td className="tabular py-2 text-right text-ink-muted">{tier.minimum_cards}</td>
                      <td className="tabular py-2 text-right text-ink-muted">
                        {tier.max_declared_value === null
                          ? '—'
                          : formatMoney(tier.max_declared_value, tier.currency, { compact: true })}
                      </td>
                      <td className="tabular py-2 text-right text-ink-muted">
                        {tier.turnaround_days ? `${tier.turnaround_days}d` : '—'}
                      </td>
                      <td className="py-2 text-right">
                        <input
                          type="checkbox"
                          checked={tier.active}
                          onChange={(event) =>
                            saveTier.mutate({ tier, changes: { active: event.target.checked } })
                          }
                          className="size-4 accent-[var(--color-brand)]"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </PanelBody>
        </Panel>
      ))}
    </div>
  )
}

function SellingSettings() {
  const profiles = useQuery({ queryKey: keys.sellingProfiles, queryFn: api.listSellingProfiles })
  if (profiles.isLoading) return <LoadingPanel />
  if (profiles.isError) return <ErrorState error={profiles.error} />

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {profiles.data!.map((profile) => (
        <Panel key={profile.id}>
          <PanelHeader>
            <div>
              <PanelTitle className="flex items-center gap-2">
                {profile.name}
                {profile.is_default ? <Badge tone="brand">Default</Badge> : null}
              </PanelTitle>
            </div>
          </PanelHeader>
          <PanelBody className="space-y-1.5 text-sm">
            <Row label="Platform fee" value={`${profile.platform_fee_pct}%`} />
            <Row label="Payment fee" value={`${profile.payment_fee_pct}%`} />
            <Row label="Fixed fee" value={formatMoney(profile.payment_fixed_fee, profile.currency)} />
            <Row label="Fees on postage" value={profile.fees_apply_to_shipping ? 'Yes' : 'No'} />
            <Row label="Raw postage" value={formatMoney(profile.shipping_cost, profile.currency)} />
            <Row
              label="Graded postage"
              value={formatMoney(profile.graded_shipping_cost, profile.currency)}
            />
            <Row label="Packaging" value={formatMoney(profile.packaging_cost, profile.currency)} />
            {profile.notes ? (
              <p className="border-t border-line pt-2 text-xs text-ink-faint">{profile.notes}</p>
            ) : null}
          </PanelBody>
        </Panel>
      ))}
    </div>
  )
}

function DataSourceSettings() {
  const sources = useQuery({ queryKey: keys.dataSources, queryFn: api.listDataSources })
  if (sources.isLoading) return <LoadingPanel />
  if (sources.isError) return <ErrorState error={sources.error} />

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface-raised px-4 py-3 text-xs text-ink-muted">
        The local database is the source of truth. Providers import into it — if one goes away, you keep
        your collection, your price history and every past analysis. API keys are read from the
        environment and never stored here.
      </div>
      <Panel className="overflow-x-auto">
        <table className="w-full min-w-2xl text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wider text-ink-faint">
            <tr>
              <th className="px-4 py-3 font-medium">Source</th>
              <th className="px-4 py-3 font-medium">Kind</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">API key</th>
              <th className="px-4 py-3 font-medium">Notes</th>
            </tr>
          </thead>
          <tbody>
            {sources.data!.map((source) => (
              <tr key={source.id} className="border-b border-line/60 last:border-0">
                <td className="px-4 py-2.5 text-ink">{source.name}</td>
                <td className="px-4 py-2.5 text-ink-muted">{humanise(source.kind)}</td>
                <td className="px-4 py-2.5">
                  {source.enabled ? (
                    <Badge tone="positive">Enabled</Badge>
                  ) : source.has_adapter ? (
                    <Badge tone="outline">Disabled</Badge>
                  ) : (
                    <Badge tone="neutral">No adapter yet</Badge>
                  )}
                </td>
                <td className="px-4 py-2.5 text-xs text-ink-faint">
                  {source.api_key_env_var
                    ? source.api_key_present
                      ? `${source.api_key_env_var} set`
                      : `${source.api_key_env_var} not set`
                    : 'Not required'}
                </td>
                <td className="max-w-md px-4 py-2.5 text-xs text-ink-faint">{source.notes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-xs text-ink-faint">{label}</span>
      <span className="tabular text-ink">{value}</span>
    </div>
  )
}
