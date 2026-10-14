"""审计事件：谁在何时以什么依据改变了状态。不写密钥/整段 cookie。"""
from __future__ import annotations

import json
import uuid

from app.db import connect
from app.domain.evidence import utcnow_iso


def log_event(db_or_conn, *, actor_type: str, actor_id: str | None,
              event_type: str, entity_type: str, entity_id: str,
              detail: dict | None = None) -> str:
    """写 audit_events。detail 不得包含密钥/token/密码。

    第一参数可传 db_path（开新连接并提交）或 sqlite3.Connection（conn），
    传 conn 时与调用方同事务提交，主事务回滚时审计一并回滚（R31）。
    """
    return _log_event(db_or_conn, actor_type=actor_type, actor_id=actor_id,
                      event_type=event_type, entity_type=entity_type,
                      entity_id=entity_id, detail=detail)


def _log_event(db_or_conn, *, actor_type: str, actor_id: str | None,
               event_type: str, entity_type: str, entity_id: str,
               detail: dict | None = None) -> str:
    detail = detail or {}
    # 递归脱敏（R31：黑名单不能只覆盖顶层键）
    clean = _redact(detail)
    eid = str(uuid.uuid4())
    close = False
    import sqlite3
    if isinstance(db_or_conn, sqlite3.Connection):
        conn = db_or_conn
    else:
        conn = connect(db_or_conn)
        close = True
    try:
        conn.execute(
            "INSERT INTO audit_events(id, actor_type, actor_id, event_type,"
            " entity_type, entity_id, detail_json, created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (eid, actor_type, actor_id, event_type, entity_type, entity_id,
             json.dumps(clean, ensure_ascii=False), utcnow_iso()),
        )
        if close:
            conn.commit()
        return eid
    finally:
        if close:
            conn.close()


def _redact(obj, *, blacklist=None):
    """递归脱敏：任意深度键名含敏感词的值替换为 [REDACTED]。"""
    blacklist = blacklist or {"password", "token", "cookie", "secret", "key",
                              "csrf", "salt", "authorization", "api_key",
                              "apikey", "private"}
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if any(b in k.lower() for b in blacklist)
                    else _redact(v, blacklist=blacklist))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(i, blacklist=blacklist) for i in obj]
    return obj
