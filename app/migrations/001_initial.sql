-- Planning contract: validate with an in-memory SQLite database only.
-- The implementing agent copies/adapts this as migration 001 after T04 tests.
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS owners (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
 token_hash TEXT NOT NULL UNIQUE, csrf_hash TEXT NOT NULL,
 expires_at TEXT NOT NULL, revoked_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_versions (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('STRATEGY','ENGINE','OUTCOME')),
 version TEXT NOT NULL, content_json TEXT NOT NULL CHECK(json_valid(content_json)),
 content_hash TEXT NOT NULL, supersedes_id TEXT REFERENCES model_versions(id),
 created_at TEXT NOT NULL, UNIQUE(kind, version)
);
CREATE TABLE IF NOT EXISTS batches (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, week_start TEXT NOT NULL,
 target_min INTEGER NOT NULL DEFAULT 3, target_max INTEGER NOT NULL DEFAULT 5,
 created_at TEXT NOT NULL, CHECK(target_min>=0 AND target_max>=target_min)
);
CREATE TABLE IF NOT EXISTS opportunities (
 id TEXT PRIMARY KEY, keyword TEXT NOT NULL, category_group TEXT NOT NULL,
 source_hash TEXT NOT NULL, source_row INTEGER NOT NULL,
 recorded_layer TEXT NOT NULL, disposition TEXT NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
 missing_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(missing_json)),
 created_at TEXT NOT NULL, UNIQUE(source_hash,source_row)
);
CREATE TABLE IF NOT EXISTS products (
 id TEXT PRIMARY KEY, batch_id TEXT REFERENCES batches(id),
 opportunity_id TEXT REFERENCES opportunities(id), title TEXT NOT NULL,
 category TEXT NOT NULL, strategy_mode TEXT NOT NULL CHECK(strategy_mode IN ('PUHUO','RND')),
 stage TEXT NOT NULL, facts_json TEXT NOT NULL CHECK(json_valid(facts_json)),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK(is_synthetic IN (0,1)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, product_id TEXT REFERENCES products(id),
 opportunity_id TEXT REFERENCES opportunities(id), source_url TEXT, source_path TEXT,
 source_key TEXT NOT NULL, source_hash TEXT NOT NULL,
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('FACT','PROXY','ESTIMATE','UNKNOWN')),
 collection_mode TEXT NOT NULL, observed_at TEXT, collected_at TEXT NOT NULL,
 content_json TEXT NOT NULL CHECK(json_valid(content_json)),
 is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK(is_synthetic IN (0,1)),
 UNIQUE(source_key,source_hash)
);
CREATE TABLE IF NOT EXISTS offers (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 supplier TEXT NOT NULL, sku_key TEXT NOT NULL,
 specification_json TEXT NOT NULL CHECK(json_valid(specification_json)),
 procurement_minor INTEGER CHECK(procurement_minor>=0),
 freight_minor INTEGER CHECK(freight_minor>=0), currency TEXT NOT NULL DEFAULT 'CNY',
 sample_count INTEGER CHECK(sample_count>=0), verification_status TEXT NOT NULL,
 evidence_id TEXT REFERENCES evidence(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS predictions (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 strategy_version_id TEXT NOT NULL REFERENCES model_versions(id),
 engine_version_id TEXT NOT NULL REFERENCES model_versions(id),
 outcome_version_id TEXT NOT NULL REFERENCES model_versions(id),
 input_json TEXT NOT NULL CHECK(json_valid(input_json)), input_hash TEXT NOT NULL,
 result_json TEXT NOT NULL CHECK(json_valid(result_json)), decision TEXT NOT NULL,
 supersedes_id TEXT REFERENCES predictions(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 kind TEXT NOT NULL CHECK(kind IN ('IMAGE','VIDEO','BRIEF','SCRIPT','OTHER')),
 role TEXT NOT NULL, sku_key TEXT, relative_path TEXT NOT NULL, file_hash TEXT NOT NULL,
 mime_type TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 source_evidence_id TEXT REFERENCES evidence(id), parent_id TEXT REFERENCES assets(id),
 review_status TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(product_id,relative_path,file_hash)
);
CREATE TABLE IF NOT EXISTS listings (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 platform TEXT NOT NULL, account_id TEXT NOT NULL, external_id TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,
 content_json TEXT NOT NULL CHECK(json_valid(content_json)), created_at TEXT NOT NULL,
 UNIQUE(platform,account_id,external_id,revision)
);
CREATE TABLE IF NOT EXISTS operation_plans (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 action TEXT NOT NULL, plan_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 requires_content INTEGER NOT NULL CHECK(requires_content IN (0,1)),
 requires_cost INTEGER NOT NULL CHECK(requires_cost IN (0,1)),
 cost_cap_minor INTEGER CHECK(cost_cap_minor>=0), currency TEXT NOT NULL DEFAULT 'CNY',
 credit_cap TEXT, state TEXT NOT NULL, expires_at TEXT NOT NULL,
 supersedes_id TEXT REFERENCES operation_plans(id), created_at TEXT NOT NULL,
 UNIQUE(id,plan_hash),
 CHECK(requires_cost=0 OR cost_cap_minor IS NOT NULL OR credit_cap IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
 owner_id TEXT NOT NULL REFERENCES owners(id),
 kind TEXT NOT NULL CHECK(kind IN ('CONTENT','COST')),
 decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT','REVOKE')),
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 FOREIGN KEY(plan_id,plan_hash) REFERENCES operation_plans(id,plan_hash)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, product_id TEXT REFERENCES products(id),
 plan_id TEXT REFERENCES operation_plans(id), kind TEXT NOT NULL,
 state TEXT NOT NULL, input_json TEXT NOT NULL CHECK(json_valid(input_json)),
 input_hash TEXT NOT NULL, progress_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_json)),
 lease_owner TEXT, lease_until TEXT, heartbeat_at TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, error_code TEXT,
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_attempts (
 id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE REFERENCES operation_plans(id),
 job_id TEXT NOT NULL REFERENCES jobs(id), state TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
 provider TEXT NOT NULL, provider_request_id TEXT,
 receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),
 prepared_at TEXT NOT NULL, submitted_at TEXT, reconciled_at TEXT
);
CREATE TABLE IF NOT EXISTS cost_ledger (
 id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES external_attempts(id),
 approval_id TEXT NOT NULL REFERENCES approvals(id), entry_kind TEXT NOT NULL,
 amount_minor INTEGER CHECK(amount_minor>=0), currency TEXT NOT NULL DEFAULT 'CNY',
 credit_amount TEXT, receipt_key TEXT NOT NULL UNIQUE,
 evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)), created_at TEXT NOT NULL,
 CHECK(amount_minor IS NOT NULL OR credit_amount IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 listing_id TEXT REFERENCES listings(id), platform TEXT NOT NULL, account_id TEXT NOT NULL,
 external_content_id TEXT, channel TEXT NOT NULL,
 period_start TEXT NOT NULL, period_end TEXT NOT NULL, source_timezone TEXT NOT NULL,
 granularity TEXT NOT NULL, measure_mode TEXT NOT NULL CHECK(measure_mode IN ('INTERVAL','CUMULATIVE')),
 source_key TEXT NOT NULL, content_hash TEXT NOT NULL, revision INTEGER NOT NULL,
 metrics_json TEXT NOT NULL CHECK(json_valid(metrics_json)), evidence_id TEXT REFERENCES evidence(id),
 is_synthetic INTEGER NOT NULL DEFAULT 0 CHECK(is_synthetic IN (0,1)),
 collected_at TEXT NOT NULL, UNIQUE(source_key,content_hash), UNIQUE(source_key,revision)
);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
 prediction_id TEXT NOT NULL REFERENCES predictions(id),
 metric_ids_json TEXT NOT NULL CHECK(json_valid(metric_ids_json)), outcome TEXT NOT NULL,
 hypotheses_json TEXT NOT NULL CHECK(json_valid(hypotheses_json)),
 findings_json TEXT NOT NULL CHECK(json_valid(findings_json)),
 supersedes_id TEXT REFERENCES reviews(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibration_proposals (
 id TEXT PRIMARY KEY, model_version_id TEXT NOT NULL REFERENCES model_versions(id),
 proposed_json TEXT NOT NULL CHECK(json_valid(proposed_json)),
 comparison_json TEXT NOT NULL CHECK(json_valid(comparison_json)), state TEXT NOT NULL,
 accepted_by TEXT REFERENCES owners(id), new_version_id TEXT REFERENCES model_versions(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY, actor_type TEXT NOT NULL, actor_id TEXT,
 event_type TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 detail_json TEXT NOT NULL CHECK(json_valid(detail_json)), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,created_at);
CREATE INDEX IF NOT EXISTS evidence_product ON evidence(product_id,collected_at);
CREATE INDEX IF NOT EXISTS metrics_latest ON metric_snapshots(source_key,revision DESC);
CREATE INDEX IF NOT EXISTS approval_latest ON approvals(plan_id,kind,created_at DESC);
CREATE TRIGGER IF NOT EXISTS prediction_no_update BEFORE UPDATE ON predictions
 BEGIN SELECT RAISE(ABORT,'prediction is immutable'); END;
CREATE TRIGGER IF NOT EXISTS prediction_no_delete BEFORE DELETE ON predictions
 BEGIN SELECT RAISE(ABORT,'prediction is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_no_update BEFORE UPDATE ON model_versions
 BEGIN SELECT RAISE(ABORT,'model version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_no_delete BEFORE DELETE ON model_versions
 BEGIN SELECT RAISE(ABORT,'model version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS plan_content_immutable BEFORE UPDATE OF
 product_id,action,plan_hash,payload_json,requires_content,requires_cost,cost_cap_minor,currency,credit_cap,expires_at
 ON operation_plans BEGIN SELECT RAISE(ABORT,'create a new plan version'); END;
CREATE TRIGGER IF NOT EXISTS approval_no_update BEFORE UPDATE ON approvals
 BEGIN SELECT RAISE(ABORT,'append approval decision'); END;
CREATE TRIGGER IF NOT EXISTS approval_no_delete BEFORE DELETE ON approvals
 BEGIN SELECT RAISE(ABORT,'append approval decision'); END;
-- Cross-product linkage, active approval selection, caps, state transitions and
-- completed-attempt immutability also require service-layer checks and T09-T13 tests.
