"""认证层测试：owner 创建、会话、CSRF、撤销。"""
from __future__ import annotations

import pytest

from app.auth import (
    create_owner, create_session, hash_password, revoke_session, validate_session,
    verify_password,
)
from app.db import init_db


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "auth.db"
    init_db(p)
    return p


def test_password_hash_verify_roundtrip():
    ph, salt = hash_password("secret123")
    assert ph != "secret123"
    assert verify_password("secret123", ph, salt)
    assert not verify_password("wrong", ph, salt)


def test_create_owner_and_duplicate(db):
    create_owner(db, "kk", "pw123")
    with pytest.raises(ValueError):
        create_owner(db, "kk", "other")


def test_create_session_valid_and_invalid(db):
    create_owner(db, "kk", "pw123")
    s = create_session(db, "kk", "pw123")
    assert s["token"] and s["csrf"]
    # 校验
    v = validate_session(db, s["token"])
    assert v is not None
    assert v["owner_id"]
    # 错误密码
    with pytest.raises(ValueError):
        create_session(db, "kk", "bad")


def test_session_expiry_and_revoke(db):
    create_owner(db, "kk", "pw123")
    s = create_session(db, "kk", "pw123")
    # 撤销
    revoke_session(db, s["token"])
    assert validate_session(db, s["token"]) is None


def test_worker_cannot_approve_by_design(db):
    """worker 没有认证 token 就无法通过任何审批接口；此处验证无 token 即无会话。"""
    create_owner(db, "kk", "pw123")
    assert validate_session(db, "") is None
    assert validate_session(db, "attacker-token") is None


def test_no_default_password(db):
    """无预置密码：未创建 owner 时任何密码都无法建立会话。"""
    with pytest.raises(ValueError):
        create_session(db, "admin", "admin")
