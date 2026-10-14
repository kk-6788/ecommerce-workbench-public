CREATE TABLE runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
ALTER TABLE asset_usages ADD COLUMN review_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE asset_usages ADD COLUMN review_note TEXT;
ALTER TABLE asset_usages ADD COLUMN source_evidence_id TEXT REFERENCES evidence(id);
ALTER TABLE asset_usages ADD COLUMN is_synthetic INTEGER NOT NULL DEFAULT 0;
INSERT INTO asset_usages(id,asset_id,product_id,role,sku_key,version,created_at,review_status,source_evidence_id,is_synthetic)
 SELECT 'legacy-'||a.id,a.id,a.product_id,a.role,a.sku_key,1,a.created_at,a.review_status,a.source_evidence_id,a.is_synthetic
 FROM assets a WHERE NOT EXISTS (SELECT 1 FROM asset_usages u WHERE u.asset_id=a.id AND u.product_id=a.product_id AND u.role=a.role AND u.sku_key IS a.sku_key);
UPDATE asset_usages SET review_status=(SELECT a.review_status FROM assets a WHERE a.id=asset_id), source_evidence_id=(SELECT a.source_evidence_id FROM assets a WHERE a.id=asset_id), is_synthetic=(SELECT a.is_synthetic FROM assets a WHERE a.id=asset_id)
 WHERE EXISTS (SELECT 1 FROM assets a WHERE a.id=asset_id AND a.product_id=asset_usages.product_id AND a.role=asset_usages.role AND a.sku_key IS asset_usages.sku_key);
DROP TRIGGER plan_content_immutable;
CREATE TRIGGER plan_content_immutable BEFORE UPDATE OF product_id,action,plan_hash,payload_json,requires_content,requires_cost,cost_cap_minor,currency,credit_cap,expires_at,provider,account_id ON operation_plans BEGIN SELECT RAISE(ABORT,'create a new plan version'); END;
CREATE TABLE opportunity_computations (opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id), result_json TEXT NOT NULL, rule_version TEXT NOT NULL, created_at TEXT NOT NULL);
