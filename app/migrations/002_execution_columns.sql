-- R02：operation_plans 持久化 provider 与 account_id（参与 hash 但之前未落库）
ALTER TABLE operation_plans ADD COLUMN provider TEXT;
ALTER TABLE operation_plans ADD COLUMN account_id TEXT;
-- R03：attempt 增加 submitted_unknown 恢复标记列
ALTER TABLE external_attempts ADD COLUMN lease_token TEXT;
