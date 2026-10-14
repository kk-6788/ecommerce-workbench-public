"""SQLite 连接、迁移与事务辅助。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path) -> sqlite3.Connection:
    """打开带外键、busy_timeout 的 SQLite 连接。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM schema_migrations ORDER BY name"
        ).fetchall()
        return {r["name"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def _split_sql(sql: str) -> list[str]:
    """按分号拆分 SQL 语句（忽略空语句与注释行）。"""
    out = []
    buf = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        # 简单拆分：以分号结尾视为一条语句完成
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                out.append(stmt)
            buf = []
    if buf:
        stmt = "\n".join(buf).strip().rstrip(";").strip()
        if stmt:
            out.append(stmt)
    return out


def init_db(db_path) -> None:
    """执行未应用的迁移。幂等：重复调用不清数据。

    R26：每个迁移的 DDL 与版本登记置于同一显式事务；失败整体回滚
    （不留下"DDL 已执行但版本未记录"的半状态）。并发初始化通过
    BEGIN IMMEDIATE 串行化，并在锁内重读已应用版本。
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        # 串行化迁移窗口：两个初始化同时进入时只有一个能拿到写锁
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = _applied_migrations(conn)
        for mig in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if mig.name in applied:
                continue
            sql = mig.read_text(encoding="utf-8")
            try:
                # DDL + 版本登记同事务：失败则整体回滚。
                # 不用 executescript（它会隐式提交挂起事务），逐条执行保持原子。
                statements = _split_sql(sql)
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_migrations(name, applied_at) VALUES(?, datetime('now'))",
                    (mig.name,),
                )
            except Exception:
                conn.execute("ROLLBACK")
                conn.execute("BEGIN IMMEDIATE")  # 回滚后重新进入一致事务
                raise
        conn.commit()
    finally:
        conn.close()


def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE 事务上下文。"""
    conn.execute("BEGIN IMMEDIATE")
    return conn
