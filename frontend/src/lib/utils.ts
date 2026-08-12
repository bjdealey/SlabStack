import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  GBP: '£',
  USD: '$',
  EUR: '€',
  JPY: '¥',
  CAD: 'CA$',
  AUD: 'A$',
}

/**
 * Money always arrives from the API in major units. An em dash for null is
 * deliberate: "not known" and "zero" mean very different things here.
 */
export function formatMoney(
  value: number | null | undefined,
  currency = 'GBP',
  options: { compact?: boolean; signed?: boolean } = {},
): string {
  if (value === null || value === undefined) return '—'
  const symbol = CURRENCY_SYMBOLS[currency] ?? ''
  const sign = options.signed && value > 0 ? '+' : value < 0 ? '−' : ''
  const magnitude = Math.abs(value)

  if (options.compact && magnitude >= 10_000) {
    return `${sign}${symbol}${(magnitude / 1000).toFixed(1)}k`
  }
  const digits = options.compact && magnitude >= 100 ? 0 : 2
  return `${sign}${symbol}${magnitude.toLocaleString('en-GB', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-GB', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  // Round before choosing the sign, so -0.4% at zero digits reads "0%" rather
  // than the nonsense "-0%".
  const rounded = Number(value.toFixed(digits))
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${Math.abs(rounded) === 0 ? (0).toFixed(digits) : rounded.toFixed(digits)}%`
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(1)}`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/**
 * How long ago something happened. Past only.
 *
 * A future timestamp gives a negative difference and falls into "today", so
 * anything that can be ahead of now — an auction closing, a submission due —
 * wants `timeUntil` instead.
 */
export function relativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000)
  if (days < 1) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days} days ago`
  if (days < 365) return `${Math.floor(days / 30)} months ago`
  return `${Math.floor(days / 365)} years ago`
}

/**
 * How long is left. The mirror of `relativeTime`, and needed because that one
 * reports every future moment as "today" — which made an auction closing in
 * three weeks look as urgent as one closing in an hour.
 *
 * Hours matter here in a way they do not for a sale date: whether you can still
 * bid is the whole question.
 */
export function timeUntil(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const ms = date.getTime() - Date.now()
  if (ms <= 0) return 'ended'
  const minutes = Math.floor(ms / 60_000)
  if (minutes < 60) return `in ${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `in ${hours}h`
  const days = Math.round(hours / 24)
  return days === 1 ? 'in a day' : `in ${days} days`
}

/** "reverse_holo" -> "Reverse holo". Used for enum values coming from the API. */
export function humanise(value: string | null | undefined): string {
  if (!value) return '—'
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function cardTitle(card: {
  name: string
  card_number?: string | null
  set_name?: string | null
  set_code?: string | null
}): string {
  return card.card_number ? `${card.name} ${card.card_number}` : card.name
}

export function cardSubtitle(card: {
  set_name?: string | null
  set_code?: string | null
  variant?: string | null
  language?: string | null
}): string {
  return [card.set_name ?? card.set_code, card.variant, card.language === 'English' ? null : card.language]
    .filter(Boolean)
    .join(' • ')
}
