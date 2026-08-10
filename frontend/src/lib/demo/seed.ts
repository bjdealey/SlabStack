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
