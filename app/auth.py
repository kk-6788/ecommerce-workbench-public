"""owner 认证：scrypt 密码派生、高熵会话 token、CSRF。worker/agent 不能审批。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from app.db import connect

SESSION_TTL_HOURS = 24 * 14
TOKEN_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 (派生结果hex, salt hex)。salt 随机生成。"""
    if not password:
        raise ValueError("密码不能为空")
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32,
    )
    return derived.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=32,
    )
    return hmac.compare_digest(derived.hex(), password_hash)


def create_owner(db_path, username: str, password: str, *, first_only: bool = False) -> str:
    """创建 owner 账号。幂等：同名已存在则报错。"""
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if first_only and conn.execute("SELECT 1 FROM owners LIMIT 1").fetchone():
            raise ValueError("已有账号，请先登录后创建其他账号")
        exists = conn.execute(
            "SELECT 1 FROM owners WHERE username=?", (username,)).fetchone()
        if exists:
            raise ValueError(f"用户名已存在：{username}")
        owner_id = str(uuid.uuid4())
        ph, salt = hash_password(password)
        conn.execute(
            "INSERT INTO owners(id, username, password_hash, salt, created_at)"
            " VALUES(?,?,?,?,?)",
            (owner_id, username, ph, salt, _utcnow()),
        )
        conn.commit()
        return owner_id
    finally:
        conn.close()


def _new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def create_session(db_path, username: str, password: str) -> dict:
    """校验密码，创建会话。返回 {token, csrf, expires_at}。失败抛 ValueError。"""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, password_hash, salt FROM owners WHERE username=?", (username,)
        ).fetchone()
        if row is None:
            raise ValueError("用户名或密码错误")
        if not verify_password(password, row["password_hash"], row["salt"]):
            raise ValueError("用户名或密码错误")
        session_id = str(uuid.uuid4())
        token = _new_token()
        csrf = _new_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)
        conn.execute(
            "INSERT INTO sessions(id, owner_id, token_hash, csrf_hash, expires_at, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                session_id, row["id"],
                hashlib.sha256(token.encode()).hexdigest(),
                hashlib.sha256(csrf.encode()).hexdigest(),
                expires_at.isoformat(), _utcnow(),
            ),
        )
        conn.commit()
        return {"token": token, "csrf": csrf, "expires_at": expires_at.isoformat()}
    finally:
        conn.close()


def validate_session(db_path, token: str) -> dict | None:
    """校验会话 token。返回 {session_id, owner_id, csrf} 或 None。"""
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, owner_id, csrf_hash, expires_at, revoked_at FROM sessions"
            " WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] < _utcnow():
            return None
        return {"session_id": row["id"], "owner_id": row["owner_id"], "csrf": row["csrf_hash"]}
    finally:
        conn.close()


def revoke_session(db_path, token: str) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    conn = connect(db_path)
    try:
        conn.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?",
                     (_utcnow(), token_hash))
        conn.commit()
    finally:
        conn.close()
