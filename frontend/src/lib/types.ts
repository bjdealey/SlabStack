/**
 * Mirrors the API contract in docs/API_CONTRACT.md.
 *
 * Hand-written rather than generated so the shapes stay readable, but they are
 * checked against the live OpenAPI schema by the backend's contract test.
 * Money fields are always major units (18.8 = £18.80).
 */

export type BlockStatus = 'ok' | 'partial' | 'not_assessed' | 'insufficient_data' | 'not_implemented'
export type Confidence = 'none' | 'low' | 'medium' | 'high'
export type Severity = 'none' | 'minor' | 'moderate' | 'severe' | 'unknown'
export type Decision =
  | 'grade'
  | 'grade_if_batch_filled'
  | 'sell_raw'
  | 'keep_raw'
  | 'hold'
  | 'do_not_grade'
  | 'insufficient_data'

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> | null }
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface CardImage {
  id: string
  card_id: string
  side: 'front' | 'back' | 'detail' | 'slab'
  url: string
  thumbnail_url: string | null
  original_filename: string | null
  mime_type: string
  width: number | null
  height: number | null
  size_bytes: number | null
  is_primary: boolean
  sort_order: number
  caption: string | null
  created_at: string
}

export interface Card {
  id: string
  name: string
  set_id: string | null
  set_name: string | null
  set_code: string | null
  card_number: string | null
  variant_id: string | null
  variant: string | null
  language: string
  printing: string | null
  rarity: string | null
  pokemon: string | null
  card_type: string | null
  is_promo: boolean
  release_date: string | null
  catalog_key: string | null
  raw_condition: string | null
  quantity: number
  purchase_price: number | null
  purchase_currency: string | null
  purchase_date: string | null
  status: string
  user_raw_value: number | null
  decision_override: Decision | null
  decision_override_reason: string | null
  review_after: string | null
  notes: string | null
  external_ids: Record<string, unknown> | null
  created_at: string
  updated_at: string
  images: CardImage[]
  primary_image_url: string | null
  has_condition_assessment: boolean
}

export interface CardWrite {
  name: string
  set_id?: string | null
  set_name?: string | null
  set_code?: string | null
  card_number?: string | null
  variant?: string | null
  language?: string
  printing?: string | null
  rarity?: string | null
  pokemon?: string | null
  card_type?: string | null
  is_promo?: boolean
  release_date?: string | null
  raw_condition?: string | null
  quantity?: number
  purchase_price?: number | null
  purchase_currency?: string | null
  purchase_date?: string | null
  status?: string
  user_raw_value?: number | null
  decision_override?: Decision | null
  decision_override_reason?: string | null
  review_after?: string | null
  notes?: string | null
}

export interface CardSet {
  id: string
  code: string
  name: string
  series: string | null
  language: string
  release_date: string | null
  total_cards: number | null
  secret_cards: number | null
  notes: string | null
}

export interface CardVariant {
  id: string
  code: string
  name: string
  description: string | null
  sort_order: number
  is_builtin: boolean
  active: boolean
}

export interface Group {
  id: string
  name: string
  description: string | null
  color: string | null
  kind: string
  filter_json: Record<string, unknown> | null
  sort_order: number
  card_count: number
  created_at: string
  updated_at: string
}

/* --- Condition ---------------------------------------------------------- */

export interface FaceDefects {
  corner_tl: Severity
  corner_tr: Severity
  corner_bl: Severity
  corner_br: Severity
  edge_condition: Severity
  surface_condition: Severity
  holo_condition: Severity
  scratches: Severity
  print_lines: Severity
  silvering: Severity
  whitening: Severity
  dents: Severity
  dimpling: Severity
  creases: Severity
  staining: Severity
  misc_defects: Severity
  notes?: string | null
  defect_notes?: Record<string, string> | null
}

export interface Centering {
  left: number | null
  right: number | null
  top: number | null
  bottom: number | null
}

export interface ConditionScores {
  centering: number | null
  centering_front: number | null
  centering_back: number | null
  corners: number | null
  edges: number | null
  surface: number | null
  overall: number | null
  completeness: number | null
}

export interface ConditionAssessment {
  id: string
  card_id: string
  assessed_at: string
  assessor: string
  is_current: boolean
  centering: { front: Centering; back: Centering }
  front: FaceDefects
  back: FaceDefects
  notes: string | null
  scores: ConditionScores
  created_at: string
  updated_at: string
}

export interface ConditionWrite {
  assessor?: string
  centering?: { front?: Partial<Centering>; back?: Partial<Centering> }
  front?: Partial<FaceDefects>
  back?: Partial<FaceDefects>
  notes?: string | null
}

/* --- Grading configuration ---------------------------------------------- */

export interface GradingTier {
  id: string
  company_id: string
  tier_code: string
  tier_name: string
  price: number
  currency: string
  minimum_cards: number
  maximum_cards: number | null
  min_declared_value: number | null
  max_declared_value: number | null
  turnaround_days: number | null
  membership_required: boolean
  membership_discount_pct: number
  additional_fees: number
  per_card_fees: number
  declared_value_fee_pct: number
  effective_from: string | null
  effective_to: string | null
  active: boolean
  sort_order: number
  source_url: string | null
  source_checked_at: string | null
  notes: string | null
}

export interface GradingMembership {
  id: string
  company_id: string
  code: string
  name: string
  annual_fee: number
  currency: string
  included_credits: number
  discount_pct: number
  user_holds: boolean
  expires_on: string | null
  active: boolean
  source_url: string | null
  notes: string | null
}

export interface GradingCompany {
  id: string
  code: string
  name: string
  country: string | null
  currency: string
  website: string | null
  market_recognition_score: number
  grade_scale_max: number
  supports_half_grades: boolean
  supports_subgrades: boolean
  active: boolean
  sort_order: number
  notes: string | null
  tiers: GradingTier[]
  memberships: GradingMembership[]
}

export interface SellingProfile {
  id: string
  code: string
  name: string
  platform: string | null
  currency: string
  platform_fee_pct: number
  payment_fee_pct: number
  payment_fixed_fee: number
  listing_fee: number
  other_fee_pct: number
  fees_apply_to_shipping: boolean
  shipping_charged_to_buyer: number
  shipping_cost: number
  packaging_cost: number
  graded_shipping_cost: number | null
  graded_packaging_cost: number | null
  is_default: boolean
  active: boolean
  sort_order: number
  notes: string | null
}

export interface DataSource {
  id: string
  code: string
  name: string
  kind: string
  base_url: string | null
  api_key_env_var: string | null
  enabled: boolean
  priority: number
  has_adapter: boolean
  api_key_present: boolean
  last_sync_at: string | null
  last_sync_status: string | null
  terms_url: string | null
  notes: string | null
}

/* --- Settings ------------------------------------------------------------ */

export interface SettingDefinition {
  key: string
  label: string
  type: 'string' | 'number' | 'integer' | 'boolean' | 'money' | 'percent' | 'enum' | 'json'
  default: unknown
  category: string
  description: string
  minimum: number | null
  maximum: number | null
  options: string[]
  advanced: boolean
}

export interface SettingsResponse {
  values: Record<string, unknown>
  definitions: SettingDefinition[]
}

/* --- Evaluation (spec section 45) ---------------------------------------- */

export interface EvaluationBlock {
  status: BlockStatus
  reason: string | null
  phase: number | null
}

export interface ExplanationItem {
  kind: 'pass' | 'warn' | 'fail' | 'info'
  text: string
  detail: string | null
}

export interface RawBlock extends EvaluationBlock {
  card_id: string
  display_name: string
  set_label: string | null
  number: string | null
  variant: string | null
  language: string | null
  quantity: number
  currency: string
  purchase_price: number | null
  user_raw_value: number | null
  market_raw_value: number | null
  best_raw_value: number | null
  raw_value_source: string | null
  net_raw_sale_value: number | null
}

export interface ConditionBlock extends EvaluationBlock {
  assessment_id: string | null
  assessed_at: string | null
  assessor: string | null
  completeness: number | null
  scores: Omit<ConditionScores, 'completeness'>
  notable_defects: string[]
}

export interface GradePredictionBlock extends EvaluationBlock {
  company_code: string | null
  kind: string | null
  source: string | null
  probabilities: { grade: number; label: string; probability: number }[]
  likely_grade: number | null
  grade_min: number | null
  grade_max: number | null
  max_grade_cap: number | null
  confidence: Confidence
  caps_applied: string[]
}

export interface MarketValueRow {
  grade_label: string
  company_code: string | null
  grade: number | null
  median: number | null
  weighted_median: number | null
  low_quartile: number | null
  high_quartile: number | null
  last_sale: number | null
  realistic_sale: number | null
  quick_sale: number | null
  sample_size: number
  window_days: number | null
  last_sale_at: string | null
  confidence: Confidence
  premium_vs_raw_pct: number | null
  is_user_override: boolean
}

export interface MarketBlock extends EvaluationBlock {
  currency: string
  raw: MarketValueRow | null
  graded: MarketValueRow[]
  computed_at: string | null
  sources: string[]
}

export interface LiquidityBlock extends EvaluationBlock {
  score: number | null
  band: string
  sales_7d: number | null
  sales_30d: number | null
  sales_90d: number | null
  sales_365d: number | null
  days_since_last_sale: number | null
  active_listings: number | null
  sold_to_active_ratio: number | null
  median_days_between_sales: number | null
  sales_per_month: number | null
}

export interface TrendBlock extends EvaluationBlock {
  direction: string
  confidence: Confidence
  change_7d_pct: number | null
  change_30d_pct: number | null
  change_90d_pct: number | null
  change_180d_pct: number | null
  change_365d_pct: number | null
  sample_size: number
}

export interface GradingOption {
  company_id: string
  company_code: string
  company_name: string
  tier_id: string | null
  tier_name: string | null
  currency: string
  declared_value: number | null
  grading_fee: number | null
  allocated_overhead: number | null
  total_cost: number | null
  turnaround_days: number | null
  minimum_cards: number
  requires_batch: boolean
  membership_required: boolean
  available: boolean
  blockers: string[]
}

export interface ExpectedOutcome {
  company_code: string
  tier_name: string | null
  expected_gross: number | null
  expected_net: number | null
  expected_profit: number | null
  roi_pct: number | null
  probability_of_profit: number | null
  probability_of_target_profit: Record<string, number>
  minimum_profitable_grade: number | null
  downside: number | null
  upside: number | null
  liquidity_score: number | null
  opportunity_score: number | null
  rows: {
    grade: number
    label: string
    probability: number
    gross_value: number | null
    net_value: number | null
    profit: number | null
  }[]
}

export interface RecommendationBlock extends EvaluationBlock {
  decision: Decision
  confidence: Confidence
  headline: string
  company_code: string | null
  tier_name: string | null
  expected_profit: number | null
  roi_pct: number | null
  probability_of_profit: number | null
  minimum_profitable_grade: number | null
  opportunity_score: number | null
  alternative: ExpectedOutcome | null
  alternative_note: string | null
  is_user_override: boolean
  reasons: ExplanationItem[]
}

export interface CardEvaluation {
  card_id: string
  evaluated_at: string
  engine_version: string
  currency: string
  raw: RawBlock
  condition: ConditionBlock
  grade_prediction: GradePredictionBlock
  market: MarketBlock
  liquidity: LiquidityBlock
  trend: TrendBlock
  grading_options: EvaluationBlock & { options: GradingOption[] }
  expected_outcomes: EvaluationBlock & { outcomes: ExpectedOutcome[] }
  recommendation: RecommendationBlock
  explanation: ExplanationItem[]
  blockers: string[]
  data_confidence: Confidence
}

/* --- Collection ---------------------------------------------------------- */

export interface CollectionSummary {
  totals: {
    cards: number
    copies: number
    distinct_sets: number
    with_images: number
    with_condition: number
    ready_to_analyse: number
  }
  values: {
    currency: string
    purchase_total: number | null
    user_valued_total: number | null
    known_raw_value: number | null
    cards_with_value: number
    potential_graded_value: number | null
    potential_uplift: number | null
    expected_profit: number | null
    values_status: BlockStatus
    values_reason: string | null
  }
  decisions: Record<Decision, number> & { status: BlockStatus; reason: string | null }
  by_status: Record<string, number>
  by_set: { set: string; cards: number; value: number | null }[]
  recent_additions: number
  review_due: number
  readiness: { key: string; label: string; count: number; total: number; action: string }[]
  market_sales_stored: number
  priced_tiers_configured: number
}

export interface Facets {
  sets: string[]
  languages: string[]
  variants: string[]
  rarities: string[]
  statuses: string[]
}

export interface EnumsResponse {
  enums: Record<string, string[]>
  defect_fields: string[]
  corner_fields: string[]
}

export interface HealthResponse {
  status: string
  app: string
  version: string
  database: string
  database_ready: boolean
  data_dir: string
  cards: number
  grading_companies: number
  market_sales: number
  phase: string
}
