-- R21：文件对象按 hash 全局去重；商品/SKU/用途/版本用独立关联表
-- （同字节文件可被多个明确用途/SKU 引用且身份正确；文件去重 ≠ 业务去重）
CREATE TABLE IF NOT EXISTS asset_usages (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  role TEXT NOT NULL,
  sku_key TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE(asset_id, product_id, role, sku_key)
);
-- R23：模拟/合成来源标记贯穿素材（供生产聚合排除合成数据）
ALTER TABLE assets ADD COLUMN is_synthetic INTEGER NOT NULL DEFAULT 0;
