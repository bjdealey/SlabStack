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
  user_declared_value: number | null
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
  user_declared_value?: number | null
  decision_override?: Decision | null
  decision_override_reason?: string | null
  review_after?: string | null
  notes?: string | null
  /** A provider's own id for this card, so price syncs can find it directly. */
  external_ids?: Record<string, string> | null
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
  strictness: number
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
  /**
   * Every environment variable the source reads, and whether it is set. Some
   * need more than one — eBay wants a client id and a secret — and reporting
   * only the first shows a source as ready when it cannot authenticate.
   * Names only. Never values.
   */
  credentials: Credential[]
  last_sync_at: string | null
  last_sync_status: string | null
  terms_url: string | null
  notes: string | null
}

export interface Credential {
  env_var: string
  present: boolean
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

export interface GradeProbability {
  grade: number
  label: string
  probability: number
}

export interface CompanyGradePrediction {
  company_id: string | null
  company_code: string
  company_name: string | null
  probabilities: GradeProbability[]
  likely_grade: number | null
  grade_min: number | null
  grade_max: number | null
  max_grade_cap: number | null
  confidence: Confidence
  caps_applied: string[]
  is_user_override: boolean
  /** `calibrated` once your own returned grades have moved this prediction. */
  source: string
  /** The raw model, kept beside the corrected one rather than replaced. */
  uncalibrated_likely_grade: number | null
  uncalibrated_probabilities: GradeProbability[]
  /** Grades the centre moved. Null when nothing was applied. */
  calibration_offset: number | null
  calibration_sample_size: number | null
  calibration_note: string | null
}

export interface GradePredictionBlock extends EvaluationBlock {
  company_code: string | null
  kind: string | null
  source: string | null
  probabilities: GradeProbability[]
  likely_grade: number | null
  grade_min: number | null
  grade_max: number | null
  max_grade_cap: number | null
  confidence: Confidence
  caps_applied: string[]
  physical: CompanyGradePrediction | null
  by_company: CompanyGradePrediction[]
  model_version: string | null
  base_grade: number | null
}

export interface GradeRule {
  id: string
  code: string
  label: string
  company_id: string | null
  company_code: string | null
  field: string
  face: string | null
  min_severity: Severity
  max_grade: number | null
  probability_multiplier: number | null
  penalty_from_grade: number | null
  notes: string | null
  is_builtin: boolean
  active: boolean
  sort_order: number
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
  /**
   * Which evidence the score rests on. `sales` are your own records, which
   * carry dates and answer both how often this trades and whether it traded
   * recently. `reported_volume` is a source's yearly unit count — better on the
   * first question, silent on the second.
   */
  basis: string | null
  annual_volume: number | null
}

export interface TrendBlock extends EvaluationBlock {
  direction: string
  confidence: Confidence
  /** Which grade the direction describes — a pooled trend measures sales mix, not price. */
  grade_label: string | null
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
  base_fee: number | null
  membership_discount: number | null
  grading_fee: number | null
  per_card_fees: number | null
  declared_value_fee: number | null
  allocated_overhead: number | null
  total_cost: number | null
  shared_total: number | null
  assumed_batch_size: number
  membership_code: string | null
  turnaround_days: number | null
  minimum_cards: number
  requires_batch: boolean
  membership_required: boolean
  available: boolean
  blockers: string[]
}

export interface NetValueRow {
  grade_label: string
  grade: number | null
  gross: number | null
  shipping_income: number | null
  platform_fee: number | null
  payment_fee: number | null
  listing_fee: number | null
  postage_cost: number | null
  packaging_cost: number | null
  total_costs: number | null
  net: number | null
  is_graded: boolean
}

/** The best a company could do, priced in its own slabs — never mixed across graders. */
export interface CompanyBestCase {
  company_id: string
  company_code: string
  tier_name: string | null
  grading_cost: number | null
  best_grade_label: string | null
  best_grade: number | null
  best_net: number | null
  upside_vs_raw: number | null
  reason: string | null
}

export interface GradingOptionsBlock extends EvaluationBlock {
  options: GradingOption[]
  currency: string
  best_case: CompanyBestCase[]
  declared_value: number | null
  declared_value_source: string
  declared_value_confidence: Confidence
  declared_value_basis: string | null
  declared_value_coverage: number | null
  assumed_batch_size: number
  allocation_method: string
  allocation_note: string | null
  selling_profile_code: string | null
  selling_profile_name: string | null
  net_values: NetValueRow[]
  cheapest_available_cost: number | null
}

export interface OutcomeRow {
  grade: number
  label: string
  probability: number
  gross_value: number | null
  net_value: number | null
  profit: number | null
}

/** The five components the opportunity score is built from, each 0-10. */
export type ScoreParts = Record<string, number>

export interface ExpectedOutcome {
  company_code: string
  tier_name: string | null
  grading_cost: number | null
  expected_gross: number | null
  expected_net: number | null
  expected_profit: number | null
  roi_pct: number | null
  probability_of_profit: number | null
  probability_of_target_profit: Record<string, number>
  minimum_profitable_grade: number | null
  probability_at_or_above_minimum: number | null
  downside: number | null
  upside: number | null
  liquidity_score: number | null
  opportunity_score: number | null
  score_parts: ScoreParts
  /** Share of the grade distribution with sales behind it. The rest is unknown. */
  coverage: number
  confidence: Confidence
  notes: string[]
  rows: OutcomeRow[]
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
  score_parts: ScoreParts
  expected_net: number | null
  /** What selling it raw would net — the bar grading has to clear. */
  net_raw_alternative: number | null
  downside: number | null
  upside: number | null
  probability_of_target_profit: Record<string, number>
  grading_cost: number | null
  /** The submission the quoted cost assumes, which is not always the one asked for. */
  assumed_batch_size: number
  /**
   * Share of the likely grades with sales behind them. Below 1.0 every expected
   * figure above is conditional on landing on a priced grade — the UI must say so.
   */
  coverage: number
  review_in_days: number | null
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
  grading_options: GradingOptionsBlock
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

/** One card's verdict, flattened for a ranked list. */
export interface Opportunity {
  card_id: string
  name: string
  set_label: string | null
  decision: Decision
  headline: string
  confidence: Confidence
  company_code: string | null
  tier_name: string | null
  expected_profit: number | null
  roi_pct: number | null
  probability_of_profit: number | null
  opportunity_score: number | null
  grading_cost: number | null
  net_raw_alternative: number | null
  /** Below 1.0 the profit and ROI are conditional on landing a priced grade. */
  coverage: number
  is_user_override: boolean
  /** Carried so a list can be cut by how the market behaves, not just the verdict. */
  liquidity_score: number | null
  liquidity_band: string | null
  trend_direction: string | null
}

export interface CollectionDecisions {
  status: BlockStatus
  reason: string | null
  currency: string
  /** How many cards the engine actually ran over — always read next to the totals. */
  analysed: number
  total_cards: number
  skipped_not_ready: number
  truncated: boolean
  batch_size: number
  expected_profit: number | null
  potential_graded_value: number | null
  potential_uplift: number | null
  total_grading_cost: number | null
  counts: Partial<Record<Decision, number>>
  opportunities: Opportunity[]
}

/* --- Submissions --------------------------------------------------------- */

export interface SubmissionCardLine {
  submission_card_id: string
  card_id: string
  name: string
  set_label: string | null
  tier_id: string | null
  tier_name: string | null
  declared_value: number | null
  declared_value_source: string
  declared_value_confidence: Confidence | null
  base_fee: number | null
  membership_discount: number | null
  grading_fee: number | null
  per_card_fees: number | null
  declared_value_fee: number | null
  allocated_overhead: number | null
  total_cost: number | null
  /** What drove this card's share of the pot: 1 when equal, the declared value when weighted. */
  allocation_weight: number
  predicted_grade: number | null
  actual_grade: number | null
  status: string
  sort_order: number
  blockers: string[]
}

/** The cards on one tier, and whether that tier's own rules are satisfied. */
export interface SubmissionTierGroup {
  tier_id: string | null
  tier_name: string | null
  company_code: string
  card_count: number
  minimum_cards: number
  maximum_cards: number | null
  short_by: number
  over_by: number
  blockers: string[]
}

export interface Submission {
  id: string
  reference: string
  name: string | null
  status: string
  currency: string
  company_id: string | null
  company_code: string | null
  company_name: string | null
  tier_id: string | null
  card_count: number
  declared_value_total: number | null
  shipping_out: number | null
  shipping_return: number | null
  insurance: number | null
  handling: number | null
  other_fees: number | null
  tier_additional_fees: number | null
  shared_pot: number | null
  grading_fees: number | null
  per_card_fees: number | null
  declared_value_fees: number | null
  membership_discount: number | null
  total_cost: number | null
  /** An average, and null with no cards — not zero. */
  cost_per_card: number | null
  allocation_method: string
  allocation_note: string | null
  membership_code: string | null
  submitted_at: string | null
  received_at: string | null
  returned_at: string | null
  tracking_outbound: string | null
  tracking_return: string | null
  notes: string | null
  tiers: SubmissionTierGroup[]
  cards: SubmissionCardLine[]
  blockers: string[]
  warnings: string[]
}

export interface SubmissionWrite {
  name?: string | null
  company_id?: string | null
  tier_id?: string | null
  status?: string | null
  cost_allocation_method?: string | null
  shipping_out?: number | null
  shipping_return?: number | null
  handling?: number | null
  other_fees?: number | null
  submitted_at?: string | null
  received_at?: string | null
  returned_at?: string | null
  tracking_outbound?: string | null
  tracking_return?: string | null
  notes?: string | null
}

export interface PlacedCard {
  card_id: string
  name: string
  set_label: string | null
  company_code: string | null
  tier_id: string | null
  tier_name: string | null
  declared_value: number | null
  decision_when_routed: Decision
  decision_in_batch: Decision
  expected_profit: number | null
  grading_cost: number | null
  opportunity_score: number | null
  /** False when the card stopped paying once the real batch size was known. */
  still_pays: boolean
  reason: string | null
  cheaper_tier_name: string | null
  cheaper_tier_saving: number | null
}

export interface ProposedBatch {
  company_id: string
  company_code: string
  tier_id: string | null
  tier_name: string | null
  /** Where the cards land at the current count — differs from tier_name while short. */
  effective_tier_name: string | null
  card_count: number
  minimum_cards: number
  maximum_cards: number | null
  short_by: number
  expected_profit: number | null
  grading_cost: number | null
  expected_profit_if_filled: number | null
  viable: boolean
  reason: string | null
  cards: PlacedCard[]
}

export interface OptimiserPlan {
  status: BlockStatus
  reason: string | null
  currency: string
  analysable: number
  worth_grading: number
  placed: number
  total_cards: number
  truncated: boolean
  /** The batch size cards were routed at, so bulk tiers were on the table. */
  routed_at_batch_size: number
  expected_profit: number | null
  total_grading_cost: number | null
  batches: ProposedBatch[]
  unplaced: {
    card_id: string
    name: string
    set_label: string | null
    company_code: string | null
    tier_name: string | null
    expected_profit: number | null
    reason: string
  }[]
  stopped_paying: PlacedCard[]
  notes: string[]
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

/* --- Market -------------------------------------------------------------- */

export type ExclusionReason =
  | 'lot_or_bundle'
  | 'damaged'
  | 'wrong_card'
  | 'wrong_language'
  | 'wrong_variant'
  | 'wrong_grade'
  | 'price_outlier'
  | 'suspected_fake'
  | 'best_offer_unknown'
  | 'user_excluded'

export interface MarketSale {
  id: string
  catalog_key: string
  card_id: string | null
  company_id: string | null
  grade: number | null
  grade_label: string
  platform: string | null
  sale_date: string
  sale_price: number
  currency: string
  shipping: number | null
  total_paid: number | null
  condition_note: string | null
  listing_title: string | null
  source_url: string | null
  seller: string | null
  bid_count: number | null
  lot_size: number
  is_auction: boolean | null
  is_excluded: boolean
  exclusion_reason: ExclusionReason | null
  excluded_by: 'system' | 'user' | null
  is_outlier: boolean
  source_id: string | null
  external_id: string | null
  imported_at: string | null
}

/**
 * One thing somebody is currently asking for — never something they got.
 *
 * Deliberately a different type from `MarketSale`. A sale is evidence; an
 * asking price is a hope, and an unsold listing is evidence that nobody paid
 * it. Nothing here feeds a valuation.
 */
export interface Listing {
  id: string
  catalog_key: string
  grade_label: string
  grade: number | null
  platform: string | null
  price: number
  currency: string
  /** Null means the source did not state postage, not that it is free. */
  shipping: number | null
  /** Only where postage is known, so this is never a price with a guess in it. */
  total_ask: number | null
  listing_title: string | null
  source_url: string | null
  is_auction: boolean
  is_active: boolean
  listed_at: string | null
  /** When an auction closes. Absent for fixed-price listings. */
  ends_at: string | null
  /** When this was last confirmed present. Listings end. */
  seen_at: string
}

export interface MarketPrice {
  id: string
  catalog_key: string
  company_id: string | null
  grade: number | null
  grade_label: string
  currency: string
  median: number | null
  weighted_median: number | null
  mean: number | null
  low_quartile: number | null
  high_quartile: number | null
  last_sale: number | null
  realistic_sale: number | null
  quick_sale: number | null
  sample_size: number
  window_days: number | null
  last_sale_at: string | null
  confidence: Confidence
  computed_at: string | null
  user_value: number | null
  user_value_note: string | null
  premium_vs_raw_pct: number | null
}

export interface MarketLiquidity {
  score: number | null
  band: string
  sales_7d: number
  sales_30d: number
  sales_90d: number
  sales_365d: number
  days_since_last_sale: number | null
  active_listings: number | null
  sold_to_active_ratio: number | null
  median_days_between_sales: number | null
  sales_per_month: number | null
  /**
   * Which evidence the score rests on. `sales` are your own records, which
   * carry dates and answer both how often this trades and whether it traded
   * recently. `reported_volume` is a source's yearly unit count — better on the
   * first question, silent on the second.
   */
  basis: string | null
  annual_volume: number | null
}

export interface MarketTrend {
  direction: string
  confidence: Confidence
  grade_label: string | null
  change_7d_pct: number | null
  change_30d_pct: number | null
  change_90d_pct: number | null
  change_180d_pct: number | null
  change_365d_pct: number | null
  sample_size: number
}

export interface MarketSummary {
  catalog_key: string
  currency: string
  prices: MarketPrice[]
  liquidity: MarketLiquidity
  trend: MarketTrend
  sale_count: number
  excluded_count: number
  grade_labels: string[]
  computed_at: string | null
}

export interface SaleWrite {
  sale_date: string
  sale_price: number
  currency?: string | null
  shipping?: number | null
  grade_label?: string | null
  company_id?: string | null
  grade?: number | null
  platform?: string | null
  listing_title?: string | null
  source_url?: string | null
  seller?: string | null
  lot_size?: number
  condition_note?: string | null
  apply_filters?: boolean
}

export interface ImportRowError {
  line_number: number | null
  message: string
  values: Record<string, string>
}

export interface ImportResult {
  imported: number
  updated: number
  skipped: number
  excluded: number
  outliers_flagged: number
  exclusions: Record<string, number>
  errors: ImportRowError[]
  prices: MarketPrice[]
}

export interface SnapshotPoint {
  snapshot_date: string
  value: number
  sample_size: number
  active_listings: number | null
}

export interface SnapshotSeries {
  grade_label: string
  currency: string
  points: SnapshotPoint[]
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

/* --- Analytics ------------------------------------------------------------ */

export interface RankedOpportunities {
  status: BlockStatus
  reason: string | null
  currency: string
  analysed: number
  total_cards: number
  actionable: number
  expected_profit: number | null
  total_grading_cost: number | null
  items: Opportunity[]
}

export interface SellingCandidate {
  card_id: string
  name: string
  set_label: string | null
  decision: Decision
  /** What the market says it fetches. */
  realistic_sale: number | null
  /** What you keep after fees and postage — the same figure the card page shows. */
  net_proceeds: number | null
  /** What to ask, which is not what it fetches. Null when nothing prices it. */
  suggested_listing: number | null
  listing_basis: string | null
  liquidity_score: number | null
  liquidity_band: string | null
  days_since_last_sale: number | null
  trend_direction: string | null
  confidence: Confidence
  purchase_price: number | null
  /** Null when you never recorded what you paid. Unknown is not break-even. */
  gain_vs_purchase: number | null
  blockers: string[]
}

export interface SellingQueue {
  status: BlockStatus
  reason: string | null
  currency: string
  analysed: number
  total_cards: number
  total_net_proceeds: number | null
  items: SellingCandidate[]
  notes: string[]
}

export interface GradedCardResult {
  card_id: string
  name: string
  predicted_grade: number | null
  actual_grade: number | null
  /** Positive when it graded better than predicted. */
  surprise: number | null
  cost: number | null
  graded_value: number | null
  net_if_sold: number | null
  profit: number | null
  blockers: string[]
}

export interface SubmissionReturn {
  submission_id: string
  reference: string
  company_code: string | null
  status: string
  returned_at: string | null
  card_count: number
  graded_count: number
  total_cost: number | null
  total_value: number | null
  total_profit: number | null
  roi_pct: number | null
  /** Positive means the grader was kinder than the model expected. */
  mean_surprise: number | null
  cards: GradedCardResult[]
  status_note: string | null
}

export interface SubmissionReturns {
  status: BlockStatus
  reason: string | null
  currency: string
  scored: number
  awaiting: number
  total_cost: number | null
  total_profit: number | null
  roi_pct: number | null
  submissions: SubmissionReturn[]
}

export interface CollectionFilter {
  key: string
  label: string
  description: string
}

export interface FilterResult {
  key: string
  label: string
  description: string
  status: BlockStatus
  reason: string | null
  currency: string
  matched: number
  analysed: number
  total_cards: number
  /** Cards the engine could not decide. Unanswered is not "no". */
  unclassified: number
  card_ids: string[]
  items: Opportunity[]
}

/* --- Learning (Phase 8) --------------------------------------------------- */

export interface GradeBand {
  grade: number
  predicted_count: number
  actual_count: number
  predicted_rate: number | null
  actual_rate: number | null
  /** Actual minus predicted, in percentage points. Negative = over-predicted. */
  gap_pct: number | null
}

export interface ScoredResult {
  card_id: string
  name: string
  company_code: string | null
  predicted_grade: number | null
  actual_grade: number
  /** Positive means it graded better than predicted. */
  surprise: number | null
  /** How wrong the whole distribution was. Lower is better. */
  brier: number | null
  graded_at: string | null
}

export interface CompanyAccuracy {
  company_id: string
  company_code: string
  company_name: string
  scored: number
  exact_pct: number | null
  within_half_pct: number | null
  within_one_pct: number | null
  /** Mean signed error in grades — the bias. Positive: cards beat the prediction. */
  mean_error: number | null
  mean_absolute_error: number | null
  error_stdev: number | null
  mean_brier: number | null
  bands: GradeBand[]
  headline: string | null
  status: BlockStatus
  reason: string | null
}

export interface AccuracyReport {
  status: BlockStatus
  reason: string | null
  scored: number
  /** Graded cards with no prediction behind them. Counted, not dropped. */
  awaiting: number
  minimum_sample: number
  companies: CompanyAccuracy[]
  results: ScoredResult[]
}

export interface CalibrationEntry {
  company_id: string
  company_code: string
  sample_size: number
  minimum_sample: number
  /** Reported whether or not it is applied. */
  grade_offset: number
  spread_multiplier: number
  applied: boolean
  confidence: Confidence
  reason: string | null
}

export interface CalibrationState {
  enabled: boolean
  minimum_sample: number
  max_offset: number
  companies: CalibrationEntry[]
}

/* --- Live market data ----------------------------------------------------- */

export interface SourceState {
  code: string
  name: string
  enabled: boolean
  has_adapter: boolean
  api_key_present: boolean
  api_key_env_var: string | null
  last_sync_at: string | null
  last_sync_status: string | null
  last_sync_error: string | null
  terms_url: string | null
  notes: string | null
}

export interface CardMatch {
  external_id: string
  name: string
  set_name: string | null
  set_code: string | null
  card_number: string | null
  rarity: string | null
  language: string | null
  image_url: string | null
  /** Ordering only. A match is never applied without you confirming it. */
  confidence: number
}

export interface CatalogLookup {
  source_code: string
  source_name: string
  query: string | null
  matches: CardMatch[]
  status: string
  reason: string | null
}

export interface CardSyncOutcome {
  card_id: string
  name: string
  status: string
  value: number | null
  currency: string | null
  /** What the provider quoted, before conversion. */
  source_value: number | null
  source_currency: string | null
  /** Your rate from Settings, not a live one. */
  fx_rate: number | null
  reason: string | null

  /* A sales-level source answers with evidence rather than one number. */
  sales_imported: number
  sales_updated: number
  /** Fetched and deliberately not counted. Kept, so any exclusion is reversible. */
  sales_excluded: number
  /** Grade labels this card now has sales for, raw first. */
  grades: string[]
  listings_seen: number
  /** What the source says exists. Larger than `listings_seen` when paged. */
  listings_reported: number | null
}

export interface SyncReport {
  source_code: string
  source_name: string
  started_at: string
  finished_at: string | null
  requested: number
  updated: number
  skipped: number
  failed: number
  status: BlockStatus | string
  reason: string | null
  cards: CardSyncOutcome[]
  notes: string[]
  sales_imported: number
  sales_excluded: number
  listings_seen: number
}

/* --- What a card actually sold for ---------------------------------------- */

/** The one figure in this application that is not a projection. */
export interface Disposal {
  id: string
  card_id: string | null
  card_name: string | null
  sold_on: string
  platform: string | null
  currency: string
  sold_graded: boolean
  grade_label: string
  grade: number | null
  gross: number
  shipping_income: number | null
  fees: number | null
  postage_cost: number | null
  packaging_cost: number | null
  net_proceeds: number
  /** True when you typed the payout rather than letting it be estimated. */
  net_is_user_entered: boolean
  /** Null means unrecorded, never free. */
  grading_cost: number | null
  notes: string | null
}

export interface DisposalWrite {
  sold_on: string
  gross: number
  sold_graded?: boolean
  grade_label?: string
  grade?: number | null
  platform?: string | null
  net_proceeds?: number | null
  grading_cost?: number | null
  notes?: string | null
}

export interface DisposalOutcome {
  disposal_id: string
  card_id: string | null
  name: string
  sold_on: string
  grade_label: string
  sold_graded: boolean
  currency: string
  net_proceeds: number | null
  purchase_price: number | null
  grading_cost: number | null
  realised_profit: number | null
  /** False when a cost is missing, in which case there is no profit to show. */
  profit_is_complete: boolean
  /** What it was worth the day it sold — not today, which has moved since. */
  market_value_on_the_day: number | null
  vs_market_pct: number | null
  raw_value_on_the_day: number | null
  /** What the slab netted over the raw card that day, less what grading cost. */
  grading_gain: number | null
  reason: string | null
}

export interface RealisedReport {
  status: BlockStatus
  reason: string | null
  currency: string
  sold: number
  scored: number
  graded_sales: number
  raw_sales: number
  total_net_proceeds: number | null
  total_realised_profit: number | null
  total_grading_gain: number | null
  items: DisposalOutcome[]
  notes: string[]
}

/* --- What to assess first ------------------------------------------------- */

/** One unassessed card, and the most grading could possibly gain. */
export interface AssessmentCandidate {
  card_id: string
  name: string
  set_label: string | null
  /** `assess`, `skip` or `unknown`. */
  verdict: string
  reason: string | null
  /**
   * The most grading could add, if the card came back at the best-priced
   * grade. An upper bound, not a forecast — an assessment can only lower it.
   */
  ceiling: number | null
  /**
   * False when the best *priced* grade sits below the top of that company's
   * ladder, which makes the ceiling a bound over the priced grades only. A
   * negative ceiling proves nothing when this is false.
   */
  ceiling_is_complete: boolean
  company_code: string | null
  tier_name: string | null
  grading_cost: number | null
  best_grade_label: string | null
  best_net: number | null
  net_raw_value: number | null
  liquidity_score: number | null
  liquidity_band: string | null
  confidence: Confidence
}

export interface AssessmentQueue {
  status: BlockStatus
  reason: string | null
  currency: string
  analysed: number
  total_cards: number
  unpriced: number
  worth_assessing: number
  ruled_out: number
  unknown: number
  truncated: boolean
  total_ceiling: number | null
  items: AssessmentCandidate[]
  notes: string[]
}

/* --- Collection import ---------------------------------------------------- */

/** One row of a collection export, as we read it, before anything is written. */
export interface ImportedCard {
  line_number: number
  name: string
  set_name: string | null
  set_code: string | null
  card_number: string | null
  variant: string | null
  printing: string | null
  language: string
  rarity: string | null
  quantity: number
  /**
   * A coarse label only. It is deliberately *not* a condition assessment —
   * spec §6 rejects NM/LP/MP as the condition model — so no engine reads it and
   * an imported card stays undecided until somebody looks at it.
   */
  raw_condition: string
  purchase_price: number | null
  purchase_currency: string | null
  purchase_date: string | null
  catalog_key: string | null
  /** Set when this row matches a card already held. */
  duplicate_of: string | null
  /** What the file said, when we could not make sense of it. */
  condition_as_written: string | null
}

export interface ImportRowError {
  line_number: number | null
  message: string
}

export interface CollectionImport {
  dry_run: boolean
  status: string
  reason: string | null
  imported: number
  duplicates: number
  failed: number
  cards: ImportedCard[]
  errors: ImportRowError[]
  notes: string[]
}

/* --- Bulk catalogue linking ---------------------------------------------- */

export interface LinkCandidate {
  external_id: string
  name: string
  set_name: string | null
  card_number: string | null
  confidence: number
}

export interface LinkOutcome {
  card_id: string
  name: string
  /** `linked`, `ambiguous`, `skipped` or `failed`. */
  status: string
  reason: string | null
  external_id: string | null
  matched_name: string | null
  confidence: number | null
  /** What it was choosing between when it declined to choose. */
  candidates: LinkCandidate[]
}

export interface LinkReport {
  source_code: string
  source_name: string
  linked: number
  skipped: number
  ambiguous: number
  failed: number
  dry_run: boolean
  status: string
  reason: string | null
  cards: LinkOutcome[]
  notes: string[]
}
