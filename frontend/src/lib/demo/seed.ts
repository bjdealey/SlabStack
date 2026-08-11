/**
 * The demo's sample collection.
 *
 * Curated rather than random, so the demo shows the states that matter: a card
 * fully assessed and photographed, one assessed but incomplete, one with a
 * user-pinned Hold decision, a multi-copy stack waiting to be split, and
 * several with nothing yet — which is what a real collection looks like on
 * day one.
 *
 * The images are generated placeholders, not card scans: the artwork is
 * Nintendo/Creatures/GAME FREAK's, and shipping it in a public demo is not ours
 * to do. The real app shows your own photographs.
 */

import type { Card, ConditionWrite } from '@/lib/types'
import { BLANK_FACE } from './engine'

export interface SeedCard {
  card: Partial<Card> & { name: string }
  image?: string
  condition?: ConditionWrite
}

const none = (overrides: Record<string, string> = {}) => ({
  ...Object.fromEntries(Object.keys(BLANK_FACE).map((key) => [key, 'none'])),
  ...overrides,
})

export const SEED_CARDS: SeedCard[] = [
  {
    card: {
      name: 'Umbreon VMAX',
      set_code: 'EVS',
      set_name: 'Evolving Skies',
      card_number: '215/203',
      variant: 'Alternate Art',
      pokemon: 'Umbreon',
      rarity: 'Secret Rare',
      purchase_price: 185,
      user_raw_value: 210,
      purchase_date: '2025-11-02',
      notes: 'Pulled from a booster box, straight into a sleeve.',
    },
    image: 'card-1.jpg',
    condition: {
      centering: {
        front: { left: 48, right: 52, top: 51, bottom: 49 },
        back: { left: 47, right: 53, top: 50, bottom: 50 },
      },
      front: none({ scratches: 'minor', corner_bl: 'minor' }) as never,
      back: none({ whitening: 'minor' }) as never,
      notes: 'Pack fresh. One hairline on the holo under direct light.',
    },
  },
  {
    card: {
      name: 'Charizard ex',
      set_code: 'MEW',
      set_name: '151',
      card_number: '199/165',
      variant: 'Special Illustration Rare',
      pokemon: 'Charizard',
      rarity: 'Special Illustration Rare',
      purchase_price: 240,
      user_raw_value: 265,
      purchase_date: '2026-01-18',
    },
    image: 'card-2.jpg',
    condition: {
      centering: { front: { left: 55, right: 45, top: 50, bottom: 50 } },
      front: none({ print_lines: 'minor' }) as never,
      notes: 'Only had a chance to look at the front so far.',
    },
  },
  {
    card: {
      name: 'Giratina VSTAR',
      set_code: 'LOR',
      set_name: 'Lost Origin',
      card_number: '212/196',
      variant: 'Alternate Art',
      pokemon: 'Giratina',
      purchase_price: 175,
      user_raw_value: 168,
      decision_override: 'hold',
      decision_override_reason: 'Price soft since the reprint rumours — revisit next month.',
      review_after: '2026-09-10',
    },
    image: 'card-3.jpg',
  },
  {
    // Assessed and priced, but the slabs barely beat the raw card — the honest
    // "no" the engine has to be able to give as clearly as it gives a yes.
    card: {
      name: 'Gengar VMAX',
      set_code: 'FST',
      set_name: 'Fusion Strike',
      card_number: '271/264',
      variant: 'Alternate Art',
      pokemon: 'Gengar',
      purchase_price: 95,
      user_raw_value: 110,
    },
    condition: {
      centering: {
        front: { left: 54, right: 46, top: 52, bottom: 48 },
        back: { left: 55, right: 45, top: 51, bottom: 49 },
      },
      front: none({ corner_tr: 'minor' }) as never,
      back: none({ whitening: 'minor' }) as never,
      notes: 'Clean enough to grade well. The question is whether the slab is worth anything.',
    },
  },
  {
    card: {
      name: 'Pikachu',
      set_code: 'CRZ',
      set_name: 'Crown Zenith',
      card_number: 'GG44/GG70',
      variant: 'Trainer Gallery',
      pokemon: 'Pikachu',
      quantity: 3,
      purchase_price: 12,
      notes: 'Three copies — split before any of them goes in a submission.',
    },
  },
  {
    card: {
      name: 'Rayquaza VMAX',
      set_code: 'EVS',
      set_name: 'Evolving Skies',
      card_number: '218/203',
      variant: 'Alternate Art',
      pokemon: 'Rayquaza',
      purchase_price: 210,
      user_raw_value: 245,
    },
  },
  {
    card: {
      name: 'Mew ex',
      set_code: 'MEW',
      set_name: '151',
      card_number: '205/165',
      variant: 'Special Illustration Rare',
      pokemon: 'Mew',
      purchase_price: 88,
      user_raw_value: 96,
    },
  },
  {
    card: {
      name: 'Sylveon VMAX',
      set_code: 'EVS',
      set_name: 'Evolving Skies',
      card_number: '212/203',
      variant: 'Alternate Art',
      pokemon: 'Sylveon',
      purchase_price: 64,
    },
  },
  {
    card: {
      name: 'Lugia V',
      set_code: 'SIT',
      set_name: 'Silver Tempest',
      card_number: '186/195',
      variant: 'Alternate Art',
      pokemon: 'Lugia',
      purchase_price: 42,
      user_raw_value: 38,
    },
  },
  {
    card: {
      name: 'Charizard',
      set_code: 'BS',
      set_name: 'Base Set',
      card_number: '4/102',
      variant: 'Holo',
      printing: 'Unlimited',
      pokemon: 'Charizard',
      rarity: 'Holo Rare',
      raw_condition: 'Moderately Played',
      purchase_price: 260,
      user_raw_value: 240,
      purchase_date: '2024-06-14',
      notes: 'Childhood copy. Plays rough but the eye appeal is there.',
    },
    image: 'card-4.jpg',
    condition: {
      centering: {
        front: { left: 62, right: 38, top: 48, bottom: 52 },
        back: { left: 50, right: 50, top: 55, bottom: 45 },
      },
      front: none({
        corner_tl: 'moderate',
        corner_tr: 'minor',
        corner_bl: 'moderate',
        corner_br: 'minor',
        edge_condition: 'moderate',
        whitening: 'moderate',
        scratches: 'moderate',
        surface_condition: 'minor',
      }) as never,
      back: none({
        whitening: 'moderate',
        edge_condition: 'minor',
        corner_tl: 'minor',
        corner_tr: 'minor',
      }) as never,
      notes: 'Honest wear. Worth checking whether a 6 still clears the fee.',
    },
  },
  {
    card: {
      name: 'Iono',
      set_code: 'PAF',
      set_name: 'Paldean Fates',
      card_number: '237/091',
      variant: 'Special Illustration Rare',
      pokemon: null,
      card_type: 'Trainer',
      purchase_price: 118,
      user_raw_value: 132,
    },
  },
  {
    card: {
      name: 'Pikachu with Grey Felt Hat',
      set_code: 'PR',
      set_name: 'Van Gogh Museum Promo',
      card_number: '085',
      variant: 'Promo',
      pokemon: 'Pikachu',
      is_promo: true,
      purchase_price: 320,
      user_raw_value: 380,
    },
  },
]

/* --- Comparable sales ----------------------------------------------------- */

/**
 * Sales histories for the demo, described rather than listed.
 *
 * Dates are generated relative to the day the demo is opened, so the sample
 * never goes stale and the recency weighting, liquidity score and trend all
 * have something real to work on. The junk entries are deliberate: the demo
 * should show the exclusion filters catching a job lot and a Japanese print,
 * because that is the part of the import that is easy to get wrong.
 */
export interface SaleSeries {
  /** `raw`, or a slab label such as `PSA 10`. */
  label: string
  count: number
  /** Price of the most recent sale, in major units. */
  price: number
  /** Days between sales. */
  spacing: number
  /**
   * Days back to the most recent sale in this series. Without it every series
   * would start today, and a card meant to look illiquid would show a sale in
   * the last week.
   */
  offset?: number
  /** Added per step back in time — negative means prices have been rising. */
  drift?: number
  /** Random-looking but deterministic wobble, ± this many major units. */
  jitter?: number
  title?: string
}

export interface SeedJunk {
  daysAgo: number
  price: number
  title: string
}

export interface SeedMarket {
  series: SaleSeries[]
  junk?: SeedJunk[]
  /** Active unsold listings, which drag the liquidity score down. */
  activeListings?: number
}

/** Keyed by card name. Cards missing from this map have no sales — also a state worth showing. */
export const SEED_MARKET: Record<string, SeedMarket> = {
  'Umbreon VMAX': {
    // The one card with a grader's ladder priced end to end, so the decision
    // engine has something it can actually confirm. Everything else in the seed
    // is thinner on purpose — a demo where every card is a winner would be
    // teaching the wrong lesson.
    //
    // PSA slabs sell for more, but the demo fixture carries no PSA pricing, so
    // the engine costs the route it can actually cost. That is the point: it
    // recommends what you could really do, not the best number on the page.
    series: [
      { label: 'raw', count: 24, price: 212, spacing: 4, drift: -1.4, jitter: 7 },
      { label: 'PSA 10', count: 14, price: 905, spacing: 7, offset: 2, drift: -8, jitter: 30 },
      { label: 'PSA 9', count: 9, price: 372, spacing: 11, offset: 3, drift: -2, jitter: 14 },
      { label: 'CGC 10', count: 8, price: 604, spacing: 10, offset: 3, drift: -6, jitter: 24 },
      { label: 'CGC 9.5', count: 5, price: 430, spacing: 19, offset: 6, drift: -3, jitter: 18 },
      { label: 'CGC 9', count: 6, price: 318, spacing: 15, offset: 4, drift: -2, jitter: 14 },
      { label: 'CGC 8.5', count: 4, price: 272, spacing: 23, offset: 9, drift: -1, jitter: 11 },
      { label: 'CGC 8', count: 4, price: 249, spacing: 26, offset: 12, drift: -1, jitter: 9 },
    ],
    junk: [
      { daysAgo: 9, price: 640, title: 'Pokemon Job Lot 60 Cards Bundle Evolving Skies' },
      { daysAgo: 15, price: 96, title: 'Japanese Umbreon VMAX Alt Art 095/069' },
      { daysAgo: 22, price: 78, title: 'Umbreon VMAX Alt Art heavily played creased' },
      { daysAgo: 5, price: 1450, title: 'Umbreon VMAX Alt Art 215/203 Evolving Skies' },
    ],
    activeListings: 11,
  },
  'Charizard ex': {
    series: [
      { label: 'raw', count: 17, price: 96, spacing: 5, drift: 0.5, jitter: 5 },
      { label: 'PSA 10', count: 8, price: 305, spacing: 12, offset: 5, drift: 4, jitter: 16 },
    ],
    junk: [{ daysAgo: 11, price: 240, title: 'Charizard ex 199/165 x10 bulk lot' }],
    activeListings: 24,
  },
  'Giratina VSTAR': {
    // Thin and slow: the state where a confident number would be a lie.
    series: [{ label: 'raw', count: 4, price: 118, spacing: 47, offset: 38, drift: 3, jitter: 6 }],
    activeListings: 9,
  },
  'Gengar VMAX': {
    // A full CGC ladder that simply does not pay: the slabs sell for barely
    // more than the raw card, and the fee eats the difference. The honest "no"
    // matters as much as the yes — a demo where grading always wins would be
    // selling something the engine does not believe.
    series: [
      { label: 'raw', count: 12, price: 148, spacing: 8, drift: 2.4, jitter: 6 },
      { label: 'CGC 10', count: 4, price: 172, spacing: 22, offset: 7, drift: 4, jitter: 6 },
      { label: 'CGC 9.5', count: 6, price: 154, spacing: 16, offset: 4, drift: 6, jitter: 5 },
      { label: 'CGC 9', count: 7, price: 143, spacing: 14, offset: 3, drift: 3, jitter: 5 },
      { label: 'CGC 8.5', count: 5, price: 137, spacing: 18, offset: 9, drift: 2, jitter: 4 },
      { label: 'CGC 8', count: 5, price: 133, spacing: 20, offset: 6, drift: 1, jitter: 4 },
    ],
    activeListings: 6,
  },
  'Lugia V': {
    series: [{ label: 'raw', count: 9, price: 74, spacing: 9, drift: -0.8, jitter: 4 }],
    activeListings: 3,
  },
  Charizard: {
    // Base Set: rare, expensive, and it barely trades.
    series: [
      { label: 'raw', count: 3, price: 1180, spacing: 96, offset: 74, drift: -60, jitter: 40 },
      { label: 'PSA 9', count: 2, price: 5400, spacing: 150, offset: 121, drift: -300, jitter: 0 },
    ],
    junk: [{ daysAgo: 30, price: 42, title: 'Charizard Base Set custom metal card replica' }],
    activeListings: 2,
  },
  Iono: {
    series: [{ label: 'raw', count: 15, price: 124, spacing: 6, drift: 1.1, jitter: 5 }],
    activeListings: 14,
  },
  'Pikachu with Grey Felt Hat': {
    series: [
      { label: 'raw', count: 11, price: 402, spacing: 9, drift: -5, jitter: 18 },
      { label: 'PSA 10', count: 4, price: 1290, spacing: 26, offset: 12, drift: -40, jitter: 60 },
    ],
    activeListings: 7,
  },
}
