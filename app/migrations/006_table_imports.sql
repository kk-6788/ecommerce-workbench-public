
CREATE TABLE table_imports (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES owners(id),
 filename TEXT NOT NULL, kind TEXT NOT NULL, source_hash TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 state TEXT NOT NULL DEFAULT 'PREVIEW', created_at TEXT NOT NULL,
 report_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(report_json))
);
CREATE TABLE imported_rows (
 id TEXT PRIMARY KEY, import_id TEXT NOT NULL REFERENCES table_imports(id),
 owner_id TEXT NOT NULL REFERENCES owners(id), kind TEXT NOT NULL,
 row_hash TEXT NOT NULL, row_number INTEGER NOT NULL,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
 normalized_json TEXT NOT NULL CHECK(json_valid(normalized_json)),
 target_id TEXT, created_at TEXT NOT NULL, UNIQUE(owner_id,kind,row_hash)
);
