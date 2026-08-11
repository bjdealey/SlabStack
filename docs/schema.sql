-- SlabStack database schema (SQLite)
--
-- GENERATED FILE — do not edit.
-- Source of truth: backend/app/models/*.py
-- Regenerate with: cd backend && python -m scripts.dump_schema
--
-- Conventions:
--   *_minor      integer count of minor currency units (pence), never a float
--   catalog_key  normalised card identity shared by duplicate copies
--   is_current   marks the live row where history is kept (condition, predictions)


-- ====================================================================
-- app_settings
-- ====================================================================
CREATE TABLE app_settings (
	"key" VARCHAR(80) NOT NULL, 
	value JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_app_settings PRIMARY KEY ("key")
);

-- ====================================================================
-- card_variants
-- ====================================================================
CREATE TABLE card_variants (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(48) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	description TEXT, 
	sort_order INTEGER NOT NULL, 
	is_builtin BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_card_variants PRIMARY KEY (id), 
	CONSTRAINT uq_card_variants_code UNIQUE (code)
);

-- ====================================================================
-- collection_groups
-- ====================================================================
CREATE TABLE collection_groups (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	color VARCHAR(24), 
	kind VARCHAR(16) NOT NULL, 
	filter_json JSON, 
	sort_order INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_collection_groups PRIMARY KEY (id), 
	CONSTRAINT ck_collection_groups_kind_valid CHECK (kind IN ('folder', 'watchlist', 'smart')), 
	CONSTRAINT uq_collection_groups_name UNIQUE (name)
);

-- ====================================================================
-- data_sources
-- ====================================================================
CREATE TABLE data_sources (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(48) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	provider_class VARCHAR(120), 
	base_url VARCHAR(255), 
	api_key_env_var VARCHAR(80), 
	config JSON, 
	enabled BOOLEAN NOT NULL, 
	priority INTEGER NOT NULL, 
	rate_limit_per_minute INTEGER, 
	last_sync_at DATETIME, 
	last_sync_status VARCHAR(32), 
	last_sync_error TEXT, 
	terms_url VARCHAR(255), 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_data_sources PRIMARY KEY (id), 
	CONSTRAINT ck_data_sources_kind_valid CHECK (kind IN ('market_data', 'card_catalog', 'manual', 'csv_import')), 
	CONSTRAINT uq_data_sources_code UNIQUE (code)
);

-- ====================================================================
-- grading_companies
-- ====================================================================
CREATE TABLE grading_companies (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(16) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	country VARCHAR(64), 
	currency VARCHAR(3) NOT NULL, 
	website VARCHAR(255), 
	market_recognition_score FLOAT NOT NULL, 
	strictness FLOAT NOT NULL, 
	grade_scale_max FLOAT NOT NULL, 
	supports_half_grades BOOLEAN NOT NULL, 
	supports_subgrades BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_grading_companies PRIMARY KEY (id), 
	CONSTRAINT uq_grading_companies_code UNIQUE (code)
);

-- ====================================================================
-- selling_cost_profiles
-- ====================================================================
CREATE TABLE selling_cost_profiles (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(48) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	platform VARCHAR(48), 
	currency VARCHAR(3) NOT NULL, 
	platform_fee_pct FLOAT NOT NULL, 
	payment_fee_pct FLOAT NOT NULL, 
	payment_fixed_fee_minor INTEGER NOT NULL, 
	listing_fee_minor INTEGER NOT NULL, 
	other_fee_pct FLOAT NOT NULL, 
	fees_apply_to_shipping BOOLEAN NOT NULL, 
	shipping_charged_to_buyer_minor INTEGER NOT NULL, 
	shipping_cost_minor INTEGER NOT NULL, 
	packaging_cost_minor INTEGER NOT NULL, 
	graded_shipping_cost_minor INTEGER, 
	graded_packaging_cost_minor INTEGER, 
	is_default BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_selling_cost_profiles PRIMARY KEY (id), 
	CONSTRAINT uq_selling_cost_profiles_code UNIQUE (code)
);

-- ====================================================================
-- sets
-- ====================================================================
CREATE TABLE sets (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	series VARCHAR(120), 
	language VARCHAR(32) NOT NULL, 
	release_date DATE, 
	total_cards INTEGER, 
	secret_cards INTEGER, 
	external_ids JSON, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_sets PRIMARY KEY (id), 
	CONSTRAINT uq_sets_code_language UNIQUE (code, language), 
	CONSTRAINT ck_sets_language_valid CHECK (language IN ('English', 'Japanese', 'German', 'French', 'Italian', 'Spanish', 'Portuguese', 'Korean', 'Chinese', 'Dutch', 'Polish', 'Russian', 'Thai', 'Indonesian', 'Other'))
);
CREATE INDEX ix_sets_code ON sets (code);
CREATE INDEX ix_sets_name ON sets (name);

-- ====================================================================
-- cards
-- ====================================================================
CREATE TABLE cards (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	set_id VARCHAR(32), 
	set_name VARCHAR(160), 
	set_code VARCHAR(32), 
	card_number VARCHAR(32), 
	variant_id VARCHAR(32), 
	variant VARCHAR(80), 
	language VARCHAR(32) NOT NULL, 
	printing VARCHAR(48), 
	rarity VARCHAR(64), 
	pokemon VARCHAR(120), 
	card_type VARCHAR(64), 
	is_promo BOOLEAN NOT NULL, 
	release_date DATE, 
	catalog_key VARCHAR(200), 
	raw_condition VARCHAR(32), 
	quantity INTEGER NOT NULL, 
	purchase_price_minor INTEGER, 
	purchase_currency VARCHAR(3), 
	purchase_date DATE, 
	status VARCHAR(32) NOT NULL, 
	user_raw_value_minor INTEGER, 
	decision_override VARCHAR(32), 
	decision_override_reason TEXT, 
	review_after DATE, 
	notes TEXT, 
	external_ids JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_cards PRIMARY KEY (id), 
	CONSTRAINT ck_cards_quantity_positive CHECK (quantity >= 1), 
	CONSTRAINT ck_cards_language_valid CHECK (language IN ('English', 'Japanese', 'German', 'French', 'Italian', 'Spanish', 'Portuguese', 'Korean', 'Chinese', 'Dutch', 'Polish', 'Russian', 'Thai', 'Indonesian', 'Other')), 
	CONSTRAINT ck_cards_status_valid CHECK (status IN ('in_collection', 'submitted_for_grading', 'graded', 'listed_for_sale', 'sold', 'archived')), 
	CONSTRAINT ck_cards_decision_override_valid CHECK (decision_override IS NULL OR decision_override IN ('grade', 'grade_if_batch_filled', 'sell_raw', 'keep_raw', 'hold', 'do_not_grade', 'insufficient_data')), 
	CONSTRAINT fk_cards_set_id_sets FOREIGN KEY(set_id) REFERENCES sets (id) ON DELETE SET NULL, 
	CONSTRAINT fk_cards_variant_id_card_variants FOREIGN KEY(variant_id) REFERENCES card_variants (id) ON DELETE SET NULL
);
CREATE INDEX ix_cards_card_number ON cards (card_number);
CREATE INDEX ix_cards_catalog_key ON cards (catalog_key);
CREATE INDEX ix_cards_name ON cards (name);
CREATE INDEX ix_cards_pokemon ON cards (pokemon);
CREATE INDEX ix_cards_set_code ON cards (set_code);
CREATE INDEX ix_cards_set_name ON cards (set_name);
CREATE INDEX ix_cards_set_number ON cards (set_code, card_number);
CREATE INDEX ix_cards_status ON cards (status);
CREATE INDEX ix_cards_variant ON cards (variant);

-- ====================================================================
-- grade_rules
-- ====================================================================
CREATE TABLE grade_rules (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(64) NOT NULL, 
	label VARCHAR(160) NOT NULL, 
	company_id VARCHAR(32), 
	field VARCHAR(64) NOT NULL, 
	face VARCHAR(8), 
	min_severity VARCHAR(16) NOT NULL, 
	max_grade FLOAT, 
	probability_multiplier FLOAT, 
	penalty_from_grade FLOAT, 
	notes TEXT, 
	is_builtin BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_grade_rules PRIMARY KEY (id), 
	CONSTRAINT uq_grade_rules_code UNIQUE (code), 
	CONSTRAINT fk_grade_rules_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE CASCADE
);

-- ====================================================================
-- grading_memberships
-- ====================================================================
CREATE TABLE grading_memberships (
	id VARCHAR(32) NOT NULL, 
	company_id VARCHAR(32) NOT NULL, 
	code VARCHAR(48) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	annual_fee_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	included_credits INTEGER NOT NULL, 
	discount_pct FLOAT NOT NULL, 
	user_holds BOOLEAN NOT NULL, 
	expires_on DATE, 
	active BOOLEAN NOT NULL, 
	source_url VARCHAR(255), 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_grading_memberships PRIMARY KEY (id), 
	CONSTRAINT uq_membership_code UNIQUE (company_id, code), 
	CONSTRAINT fk_grading_memberships_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE CASCADE
);
CREATE INDEX ix_grading_memberships_company_id ON grading_memberships (company_id);

-- ====================================================================
-- grading_tiers
-- ====================================================================
CREATE TABLE grading_tiers (
	id VARCHAR(32) NOT NULL, 
	company_id VARCHAR(32) NOT NULL, 
	tier_code VARCHAR(48) NOT NULL, 
	tier_name VARCHAR(80) NOT NULL, 
	price_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	minimum_cards INTEGER NOT NULL, 
	maximum_cards INTEGER, 
	min_declared_value_minor INTEGER, 
	max_declared_value_minor INTEGER, 
	turnaround_days INTEGER, 
	membership_required BOOLEAN NOT NULL, 
	membership_discount_pct FLOAT NOT NULL, 
	additional_fees_minor INTEGER NOT NULL, 
	per_card_fees_minor INTEGER NOT NULL, 
	declared_value_fee_pct FLOAT NOT NULL, 
	effective_from DATE, 
	effective_to DATE, 
	active BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	source_url VARCHAR(255), 
	source_checked_at DATE, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_grading_tiers PRIMARY KEY (id), 
	CONSTRAINT uq_tier_effective UNIQUE (company_id, tier_code, effective_from), 
	CONSTRAINT ck_grading_tiers_minimum_cards_positive CHECK (minimum_cards >= 1), 
	CONSTRAINT fk_grading_tiers_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE CASCADE
);
CREATE INDEX ix_grading_tiers_company_id ON grading_tiers (company_id);

-- ====================================================================
-- card_images
-- ====================================================================
CREATE TABLE card_images (
	id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	side VARCHAR(16) NOT NULL, 
	file_path VARCHAR(512) NOT NULL, 
	thumbnail_path VARCHAR(512), 
	original_filename VARCHAR(255), 
	mime_type VARCHAR(64) NOT NULL, 
	width INTEGER, 
	height INTEGER, 
	size_bytes INTEGER, 
	sha256 VARCHAR(64), 
	is_primary BOOLEAN NOT NULL, 
	sort_order INTEGER NOT NULL, 
	caption VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_card_images PRIMARY KEY (id), 
	CONSTRAINT ck_card_images_side_valid CHECK (side IN ('front', 'back', 'detail', 'slab')), 
	CONSTRAINT fk_card_images_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE
);
CREATE INDEX ix_card_images_card_id ON card_images (card_id);
CREATE INDEX ix_card_images_sha256 ON card_images (sha256);

-- ====================================================================
-- collection_group_cards
-- ====================================================================
CREATE TABLE collection_group_cards (
	id VARCHAR(32) NOT NULL, 
	group_id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	added_at DATETIME NOT NULL, 
	CONSTRAINT pk_collection_group_cards PRIMARY KEY (id), 
	CONSTRAINT uq_group_card UNIQUE (group_id, card_id), 
	CONSTRAINT fk_collection_group_cards_group_id_collection_groups FOREIGN KEY(group_id) REFERENCES collection_groups (id) ON DELETE CASCADE, 
	CONSTRAINT fk_collection_group_cards_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE
);
CREATE INDEX ix_collection_group_cards_card_id ON collection_group_cards (card_id);
CREATE INDEX ix_collection_group_cards_group_id ON collection_group_cards (group_id);

-- ====================================================================
-- condition_assessments
-- ====================================================================
CREATE TABLE condition_assessments (
	id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	assessed_at DATETIME NOT NULL, 
	assessor VARCHAR(24) NOT NULL, 
	is_current BOOLEAN NOT NULL, 
	front_centering_left FLOAT, 
	front_centering_right FLOAT, 
	front_centering_top FLOAT, 
	front_centering_bottom FLOAT, 
	back_centering_left FLOAT, 
	back_centering_right FLOAT, 
	back_centering_top FLOAT, 
	back_centering_bottom FLOAT, 
	front_corner_tl VARCHAR(16) NOT NULL, 
	front_corner_tr VARCHAR(16) NOT NULL, 
	front_corner_bl VARCHAR(16) NOT NULL, 
	front_corner_br VARCHAR(16) NOT NULL, 
	front_edge_condition VARCHAR(16) NOT NULL, 
	front_surface_condition VARCHAR(16) NOT NULL, 
	front_holo_condition VARCHAR(16) NOT NULL, 
	front_scratches VARCHAR(16) NOT NULL, 
	front_print_lines VARCHAR(16) NOT NULL, 
	front_silvering VARCHAR(16) NOT NULL, 
	front_whitening VARCHAR(16) NOT NULL, 
	front_dents VARCHAR(16) NOT NULL, 
	front_dimpling VARCHAR(16) NOT NULL, 
	front_creases VARCHAR(16) NOT NULL, 
	front_staining VARCHAR(16) NOT NULL, 
	front_misc_defects VARCHAR(16) NOT NULL, 
	back_corner_tl VARCHAR(16) NOT NULL, 
	back_corner_tr VARCHAR(16) NOT NULL, 
	back_corner_bl VARCHAR(16) NOT NULL, 
	back_corner_br VARCHAR(16) NOT NULL, 
	back_edge_condition VARCHAR(16) NOT NULL, 
	back_surface_condition VARCHAR(16) NOT NULL, 
	back_holo_condition VARCHAR(16) NOT NULL, 
	back_scratches VARCHAR(16) NOT NULL, 
	back_print_lines VARCHAR(16) NOT NULL, 
	back_silvering VARCHAR(16) NOT NULL, 
	back_whitening VARCHAR(16) NOT NULL, 
	back_dents VARCHAR(16) NOT NULL, 
	back_dimpling VARCHAR(16) NOT NULL, 
	back_creases VARCHAR(16) NOT NULL, 
	back_staining VARCHAR(16) NOT NULL, 
	back_misc_defects VARCHAR(16) NOT NULL, 
	front_defect_notes JSON, 
	back_defect_notes JSON, 
	front_notes TEXT, 
	back_notes TEXT, 
	notes TEXT, 
	centering_score_front FLOAT, 
	centering_score_back FLOAT, 
	centering_score FLOAT, 
	corners_score FLOAT, 
	edges_score FLOAT, 
	surface_score FLOAT, 
	completeness FLOAT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_condition_assessments PRIMARY KEY (id), 
	CONSTRAINT ck_condition_assessments_assessor_valid CHECK (assessor IN ('user', 'image_model', 'imported')), 
	CONSTRAINT fk_condition_assessments_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE
);
CREATE INDEX ix_condition_assessments_card_id ON condition_assessments (card_id);
CREATE INDEX ix_condition_card_current ON condition_assessments (card_id, is_current);

-- ====================================================================
-- grading_submissions
-- ====================================================================
CREATE TABLE grading_submissions (
	id VARCHAR(32) NOT NULL, 
	reference VARCHAR(48) NOT NULL, 
	name VARCHAR(120), 
	company_id VARCHAR(32) NOT NULL, 
	tier_id VARCHAR(32), 
	membership_id VARCHAR(32), 
	status VARCHAR(24) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	shipping_out_minor INTEGER NOT NULL, 
	shipping_return_minor INTEGER NOT NULL, 
	insurance_minor INTEGER NOT NULL, 
	membership_allocation_minor INTEGER NOT NULL, 
	handling_minor INTEGER NOT NULL, 
	other_fees_minor INTEGER NOT NULL, 
	cost_allocation_method VARCHAR(24) NOT NULL, 
	submitted_at DATE, 
	received_at DATE, 
	returned_at DATE, 
	tracking_outbound VARCHAR(120), 
	tracking_return VARCHAR(120), 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_grading_submissions PRIMARY KEY (id), 
	CONSTRAINT ck_grading_submissions_status_valid CHECK (status IN ('draft', 'planned', 'shipped', 'received', 'grading', 'returned', 'cancelled')), 
	CONSTRAINT ck_grading_submissions_cost_allocation_method_valid CHECK (cost_allocation_method IN ('equal', 'value_weighted', 'custom')), 
	CONSTRAINT uq_grading_submissions_reference UNIQUE (reference), 
	CONSTRAINT fk_grading_submissions_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE RESTRICT, 
	CONSTRAINT fk_grading_submissions_tier_id_grading_tiers FOREIGN KEY(tier_id) REFERENCES grading_tiers (id) ON DELETE SET NULL, 
	CONSTRAINT fk_grading_submissions_membership_id_grading_memberships FOREIGN KEY(membership_id) REFERENCES grading_memberships (id) ON DELETE SET NULL
);
CREATE INDEX ix_grading_submissions_company_id ON grading_submissions (company_id);
CREATE INDEX ix_grading_submissions_status ON grading_submissions (status);

-- ====================================================================
-- market_listings
-- ====================================================================
CREATE TABLE market_listings (
	id VARCHAR(32) NOT NULL, 
	catalog_key VARCHAR(200) NOT NULL, 
	card_id VARCHAR(32), 
	company_id VARCHAR(32), 
	grade FLOAT, 
	grade_label VARCHAR(24) NOT NULL, 
	platform VARCHAR(48), 
	listed_at DATE, 
	ends_at DATETIME, 
	price_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	shipping_minor INTEGER, 
	listing_title VARCHAR(512), 
	source_url VARCHAR(512), 
	seller VARCHAR(120), 
	is_auction BOOLEAN NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	source_id VARCHAR(32), 
	external_id VARCHAR(160), 
	raw_payload JSON, 
	seen_at DATETIME NOT NULL, 
	CONSTRAINT pk_market_listings PRIMARY KEY (id), 
	CONSTRAINT uq_listing_source_external UNIQUE (source_id, external_id), 
	CONSTRAINT fk_market_listings_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_listings_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_listings_source_id_data_sources FOREIGN KEY(source_id) REFERENCES data_sources (id) ON DELETE SET NULL
);
CREATE INDEX ix_market_listings_catalog_key ON market_listings (catalog_key);
CREATE INDEX ix_market_listings_key_grade ON market_listings (catalog_key, grade_label);

-- ====================================================================
-- market_prices
-- ====================================================================
CREATE TABLE market_prices (
	id VARCHAR(32) NOT NULL, 
	catalog_key VARCHAR(200) NOT NULL, 
	card_id VARCHAR(32), 
	company_id VARCHAR(32), 
	grade FLOAT, 
	grade_label VARCHAR(24) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	median_minor INTEGER, 
	weighted_median_minor INTEGER, 
	mean_minor INTEGER, 
	low_quartile_minor INTEGER, 
	high_quartile_minor INTEGER, 
	last_sale_minor INTEGER, 
	realistic_sale_minor INTEGER, 
	quick_sale_minor INTEGER, 
	sample_size INTEGER NOT NULL, 
	window_days INTEGER, 
	last_sale_at DATE, 
	confidence VARCHAR(16) NOT NULL, 
	computed_at DATETIME, 
	user_value_minor INTEGER, 
	user_value_note TEXT, 
	source_id VARCHAR(32), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	CONSTRAINT pk_market_prices PRIMARY KEY (id), 
	CONSTRAINT uq_price_key_grade_source UNIQUE (catalog_key, grade_label, source_id), 
	CONSTRAINT ck_market_prices_confidence_valid CHECK (confidence IN ('none', 'low', 'medium', 'high')), 
	CONSTRAINT fk_market_prices_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_prices_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_prices_source_id_data_sources FOREIGN KEY(source_id) REFERENCES data_sources (id) ON DELETE SET NULL
);
CREATE INDEX ix_market_prices_catalog_key ON market_prices (catalog_key);

-- ====================================================================
-- market_sales
-- ====================================================================
CREATE TABLE market_sales (
	id VARCHAR(32) NOT NULL, 
	catalog_key VARCHAR(200) NOT NULL, 
	card_id VARCHAR(32), 
	company_id VARCHAR(32), 
	grade FLOAT, 
	grade_label VARCHAR(24) NOT NULL, 
	platform VARCHAR(48), 
	sale_date DATE NOT NULL, 
	sale_price_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	shipping_minor INTEGER, 
	condition_note VARCHAR(120), 
	listing_title VARCHAR(512), 
	source_url VARCHAR(512), 
	seller VARCHAR(120), 
	bid_count INTEGER, 
	lot_size INTEGER NOT NULL, 
	is_auction BOOLEAN, 
	is_excluded BOOLEAN NOT NULL, 
	exclusion_reason VARCHAR(32), 
	excluded_by VARCHAR(16), 
	is_outlier BOOLEAN NOT NULL, 
	source_id VARCHAR(32), 
	external_id VARCHAR(160), 
	raw_payload JSON, 
	imported_at DATETIME NOT NULL, 
	CONSTRAINT pk_market_sales PRIMARY KEY (id), 
	CONSTRAINT uq_sale_source_external UNIQUE (source_id, external_id), 
	CONSTRAINT ck_market_sales_exclusion_reason_valid CHECK (exclusion_reason IS NULL OR exclusion_reason IN ('lot_or_bundle', 'damaged', 'wrong_card', 'wrong_language', 'wrong_variant', 'wrong_grade', 'price_outlier', 'suspected_fake', 'best_offer_unknown', 'user_excluded')), 
	CONSTRAINT fk_market_sales_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_sales_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE SET NULL, 
	CONSTRAINT fk_market_sales_source_id_data_sources FOREIGN KEY(source_id) REFERENCES data_sources (id) ON DELETE SET NULL
);
CREATE INDEX ix_market_sales_catalog_key ON market_sales (catalog_key);
CREATE INDEX ix_market_sales_grade_label ON market_sales (grade_label);
CREATE INDEX ix_market_sales_key_grade_date ON market_sales (catalog_key, grade_label, sale_date);
CREATE INDEX ix_market_sales_sale_date ON market_sales (sale_date);

-- ====================================================================
-- price_snapshots
-- ====================================================================
CREATE TABLE price_snapshots (
	id VARCHAR(32) NOT NULL, 
	catalog_key VARCHAR(200) NOT NULL, 
	card_id VARCHAR(32), 
	company_id VARCHAR(32), 
	grade FLOAT, 
	grade_label VARCHAR(24) NOT NULL, 
	snapshot_date DATE NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	value_minor INTEGER NOT NULL, 
	sample_size INTEGER NOT NULL, 
	active_listings INTEGER, 
	source_id VARCHAR(32), 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_price_snapshots PRIMARY KEY (id), 
	CONSTRAINT uq_snapshot_unique UNIQUE (catalog_key, grade_label, snapshot_date, source_id), 
	CONSTRAINT fk_price_snapshots_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL, 
	CONSTRAINT fk_price_snapshots_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE SET NULL, 
	CONSTRAINT fk_price_snapshots_source_id_data_sources FOREIGN KEY(source_id) REFERENCES data_sources (id) ON DELETE SET NULL
);
CREATE INDEX ix_price_snapshots_key_date ON price_snapshots (catalog_key, snapshot_date);

-- ====================================================================
-- grade_predictions
-- ====================================================================
CREATE TABLE grade_predictions (
	id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	condition_assessment_id VARCHAR(32), 
	company_id VARCHAR(32), 
	kind VARCHAR(16) NOT NULL, 
	source VARCHAR(24) NOT NULL, 
	model_version VARCHAR(32), 
	probabilities JSON NOT NULL, 
	likely_grade FLOAT, 
	grade_min FLOAT, 
	grade_max FLOAT, 
	max_grade_cap FLOAT, 
	confidence VARCHAR(16) NOT NULL, 
	caps_applied JSON, 
	explanation JSON, 
	is_current BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_grade_predictions PRIMARY KEY (id), 
	CONSTRAINT ck_grade_predictions_kind_valid CHECK (kind IN ('physical', 'market')), 
	CONSTRAINT ck_grade_predictions_source_valid CHECK (source IN ('rules_engine', 'user_override', 'calibrated', 'image_model')), 
	CONSTRAINT ck_grade_predictions_confidence_valid CHECK (confidence IN ('none', 'low', 'medium', 'high')), 
	CONSTRAINT fk_grade_predictions_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE, 
	CONSTRAINT fk_grade_predictions_condition_assessment_id_condition_assessments FOREIGN KEY(condition_assessment_id) REFERENCES condition_assessments (id) ON DELETE SET NULL, 
	CONSTRAINT fk_grade_predictions_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE CASCADE
);
CREATE INDEX ix_grade_predictions_card_current ON grade_predictions (card_id, is_current);
CREATE INDEX ix_grade_predictions_card_id ON grade_predictions (card_id);

-- ====================================================================
-- submission_cards
-- ====================================================================
CREATE TABLE submission_cards (
	id VARCHAR(32) NOT NULL, 
	submission_id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	tier_id VARCHAR(32), 
	declared_value_minor INTEGER, 
	system_declared_value_minor INTEGER, 
	declared_value_source VARCHAR(16) NOT NULL, 
	declared_value_confidence VARCHAR(16), 
	grading_fee_minor INTEGER, 
	allocated_overhead_minor INTEGER, 
	predicted_grade FLOAT, 
	actual_grade FLOAT, 
	cert_number VARCHAR(64), 
	status VARCHAR(24) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	notes TEXT, 
	metadata_json JSON, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_submission_cards PRIMARY KEY (id), 
	CONSTRAINT uq_submission_card UNIQUE (submission_id, card_id), 
	CONSTRAINT ck_submission_cards_status_valid CHECK (status IN ('planned', 'submitted', 'graded', 'returned', 'rejected', 'removed')), 
	CONSTRAINT ck_submission_cards_declared_value_source_valid CHECK (declared_value_source IN ('system', 'user')), 
	CONSTRAINT fk_submission_cards_submission_id_grading_submissions FOREIGN KEY(submission_id) REFERENCES grading_submissions (id) ON DELETE CASCADE, 
	CONSTRAINT fk_submission_cards_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE, 
	CONSTRAINT fk_submission_cards_tier_id_grading_tiers FOREIGN KEY(tier_id) REFERENCES grading_tiers (id) ON DELETE SET NULL
);
CREATE INDEX ix_submission_cards_card_id ON submission_cards (card_id);
CREATE INDEX ix_submission_cards_submission_id ON submission_cards (submission_id);

-- ====================================================================
-- prediction_results
-- ====================================================================
CREATE TABLE prediction_results (
	id VARCHAR(32) NOT NULL, 
	card_id VARCHAR(32) NOT NULL, 
	grade_prediction_id VARCHAR(32), 
	company_id VARCHAR(32) NOT NULL, 
	submission_id VARCHAR(32), 
	actual_grade FLOAT NOT NULL, 
	actual_subgrades JSON, 
	graded_at DATE, 
	cert_number VARCHAR(64), 
	predicted_probabilities JSON, 
	predicted_likely_grade FLOAT, 
	brier_score FLOAT, 
	notes TEXT, 
	created_at DATETIME NOT NULL, 
	CONSTRAINT pk_prediction_results PRIMARY KEY (id), 
	CONSTRAINT fk_prediction_results_card_id_cards FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE, 
	CONSTRAINT fk_prediction_results_grade_prediction_id_grade_predictions FOREIGN KEY(grade_prediction_id) REFERENCES grade_predictions (id) ON DELETE SET NULL, 
	CONSTRAINT fk_prediction_results_company_id_grading_companies FOREIGN KEY(company_id) REFERENCES grading_companies (id) ON DELETE CASCADE, 
	CONSTRAINT fk_prediction_results_submission_id_grading_submissions FOREIGN KEY(submission_id) REFERENCES grading_submissions (id) ON DELETE SET NULL
);
CREATE INDEX ix_prediction_results_card_id ON prediction_results (card_id);
