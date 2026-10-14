CREATE TABLE ai_check_runs (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
 product_id TEXT NOT NULL REFERENCES products(id), use_case TEXT NOT NULL,
 state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), snapshot_hash TEXT NOT NULL,
 request_json TEXT NOT NULL CHECK(json_valid(request_json)),
 policy_version TEXT NOT NULL, model TEXT NOT NULL, returned_model TEXT,
 plan_id TEXT UNIQUE REFERENCES operation_plans(id),
 response_json TEXT CHECK(response_json IS NULL OR json_valid(response_json)),
 reason TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner_id,snapshot_hash)
);
CREATE TABLE ai_check_answers (
 run_id TEXT NOT NULL REFERENCES ai_check_runs(id), question_id TEXT NOT NULL,
 answer_json TEXT NOT NULL CHECK(json_valid(answer_json)), state TEXT NOT NULL,
 PRIMARY KEY(run_id,question_id)
);
CREATE TABLE ai_review_events (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES ai_check_runs(id),
 owner_id TEXT NOT NULL REFERENCES owners(id), action TEXT NOT NULL, reason TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL, expected_revision INTEGER NOT NULL,
 idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(owner_id,idempotency_key)
);
CREATE TABLE ai_notifications (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
 run_id TEXT NOT NULL REFERENCES ai_check_runs(id), kind TEXT NOT NULL,
 read_at TEXT, resolved_at TEXT, created_at TEXT NOT NULL, UNIQUE(owner_id,run_id,kind)
);
CREATE TABLE ai_calibration_records (
 id TEXT PRIMARY KEY, use_case TEXT NOT NULL, question_hash TEXT NOT NULL,
 model TEXT NOT NULL, policy_version TEXT NOT NULL, evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
 approved_by TEXT REFERENCES owners(id), expires_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER ai_review_no_update BEFORE UPDATE ON ai_review_events BEGIN SELECT RAISE(ABORT,'AI review events are append-only'); END;
CREATE TRIGGER ai_review_no_delete BEFORE DELETE ON ai_review_events BEGIN SELECT RAISE(ABORT,'AI review events are append-only'); END;
CREATE TRIGGER ai_snapshot_immutable BEFORE UPDATE OF owner_id,product_id,use_case,snapshot_json,snapshot_hash,request_json,policy_version,model,plan_id,expires_at ON ai_check_runs BEGIN SELECT RAISE(ABORT,'AI snapshot is immutable'); END;
