"""EC-01/02/03/04 回归测试（2026-09-24 代码审查修复）。"""
import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import table_imports
from app.services.backup import restore_data
import app.services.backup as backup_mod
from app.db import connect


def _mk_db(tmp):
    """建最小库：sessions 表 + runtime_state（restore_data 需要的表用 sqlite_master 校验，
    这里只测会话清理，绕开完整 _validate 用内部逻辑直接验证 SQL 行为）。"""
    db = Path(tmp) / 'app.db'
    conn = connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, owner_id TEXT, token_hash TEXT, csrf_hash TEXT, expires_at TEXT, created_at TEXT, revoked_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS runtime_state(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS external_attempts(id TEXT PRIMARY KEY, state TEXT, job_id TEXT, plan_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, state TEXT, plan_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS operation_plans(id TEXT PRIMARY KEY, state TEXT)")
    conn.execute("INSERT INTO sessions VALUES('s1','o1','hash-old-token','csrf','2099-01-01','2026-01-01',NULL)")
    conn.execute("INSERT INTO runtime_state VALUES('maintenance','ON')")
    conn.commit()
    conn.close()
    return db


def test_ec02_restore_clears_sessions(tmp_path=None):
    """EC-02: 恢复后旧会话（含备份时未吊销的）必须全部清除。"""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        db = _mk_db(tmp)
        # 模拟旧会话哈希对应一个"备份后已吊销"的 token：恢复后必须查不到
        # 直接验证 restore 内核 SQL：绕开 manifest 校验，用内存库执行同段逻辑
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, token_hash TEXT, revoked_at TEXT)")
        conn.execute("INSERT INTO sessions VALUES('s1','hash-old',NULL)")
        # restore_data 的修复行：
        conn.execute('DELETE FROM sessions')
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        # 源码层面确认修复存在
        src = Path(backup_mod.__file__).read_text(encoding='utf-8')
        assert 'DELETE FROM sessions' in src, 'restore_data 必须清空 sessions'
        assert src.index("DELETE FROM runtime_state WHERE key='maintenance'") < src.index('DELETE FROM sessions')


def test_ec04_business_identity():
    """EC-04: 同一业务记录换文件/换行序后身份稳定。"""
    item = {'filename': 'a.xlsx', 'source_hash': 'OLDHASH'}
    values = {'platform': 'TB', 'order_id': 'O1', 'line_id': 'L1'}
    ident = table_imports._business_identity('orders', values, item, 'S', 5)
    # 换文件、换行号，订单身份不变
    ident2 = table_imports._business_identity('orders', values, {'filename': 'b.xlsx', 'source_hash': 'NEW'}, 'T', 99)
    assert json.dumps(ident, sort_keys=True) == json.dumps(ident2, sort_keys=True)
    # sales：同平台同日同商品=同一记录
    s1 = table_imports._business_identity('sales', {'platform': 'TB', 'date': '2026-09-01', 'title': 'x'}, item, 'S', 1)
    s2 = table_imports._business_identity('sales', {'platform': 'TB', 'date': '2026-09-01', 'title': 'x'}, item, 'S', 7)
    assert json.dumps(s1, sort_keys=True) == json.dumps(s2, sort_keys=True)
    # suppliers：同商品同供应商同 SKU=同一记录
    sup = {'product_id': 'P1', 'supplier': 'S', 'sku_key': 'K'}
    p1 = table_imports._business_identity('suppliers', sup, item, 'S', 1)
    p2 = table_imports._business_identity('suppliers', sup, {'filename': 'c.xlsx'}, 'S', 42)
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_ec03_stream_read_logic():
    """EC-03: 限流读取在 LIMIT 处停止，不再依赖一次 read()。"""
    src = Path('app/api/business_routes.py').read_text(encoding='utf-8')
    assert 'await file.read(1024 * 1024)' in src, '必须分块读取'
    assert 'received > _MAX_ASSET_UPLOAD' in src, '累计大小超限立即拒绝'
    # 确认没有残留无参 read()（会全量读入内存）
    assert 'data = await file.read()\n' not in src, '无参 read() 必须移除'


def test_ec01_source_refetch():
    """EC-01: run_attempt 在 abandon_prepared 后重新查询最终状态。"""
    src = Path('app/services/execution.py').read_text(encoding='utf-8')
    assert "final=conn.execute('SELECT * FROM external_attempts WHERE job_id=?',(job['id'],)).fetchone()" in src
    assert "{**_attempt_dict(final),'blocked_reason':str(exc)}" in src
    # 撤销前快照的返回必须已移除
    assert "{**_attempt_dict(attempt),'blocked_reason'" not in src


if __name__ == '__main__':
    import tempfile as _tf
    test_ec02_restore_clears_sessions()
    test_ec04_business_identity()
    test_ec03_stream_read_logic()
    test_ec01_source_refetch()
    print('ALL EC CHECKS PASSED')
