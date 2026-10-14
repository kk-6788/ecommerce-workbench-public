"""数据库层测试：迁移、FK、事务回滚、幂等。"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import connect, init_db


def test_init_db_creates_schema(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = connect(db)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {
            "owners", "sessions", "model_versions", "batches", "opportunities",
            "products", "evidence", "offers", "predictions", "assets",
            "listings", "operation_plans", "approvals", "jobs",
            "external_attempts", "cost_ledger", "metric_snapshots", "reviews",
            "calibration_proposals", "audit_events", "schema_migrations",
        }
        assert expected <= tables
    finally:
        conn.close()


def test_init_db_idempotent(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = connect(db)
    conn.execute("INSERT INTO owners(id, username, password_hash, salt, created_at)"
                 " VALUES('o1','kk','h','s',datetime('now'))")
    conn.commit()
    conn.close()
    # 再次初始化不应清数据
    init_db(db)
    conn = connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) c FROM owners").fetchone()["c"] == 1
    finally:
        conn.close()


def test_foreign_keys_enforced(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sessions(id, owner_id, token_hash, csrf_hash, expires_at, created_at)"
                     " VALUES('s1','nonexistent','t','c','2026-01-01','2026-01-01')")
    conn.close()


def test_transaction_rollback(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    conn = connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO batches(id, name, week_start, created_at)"
                     " VALUES('b1','测试批次','2026-09-14',datetime('now'))")
        conn.rollback()
        assert conn.execute("SELECT COUNT(*) c FROM batches").fetchone()["c"] == 0
    finally:
        conn.close()
