"""G03 PM 决策与学习链路测试：字段校验、校准提案、CLI 命令。"""
from __future__ import annotations

import json

import pytest

from app.db import init_db
from app.domain import calibration, pm_validation
from app.services.products import create_product


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "g03.db"
    init_db(p)
    return p


def test_candidate_validation_required_fields():
    """pm-fields：必填缺失拒绝，类型错误拒绝，未知字段拒绝。"""
    errs = pm_validation.validate_section("candidate", {})
    assert any("candidate_id 必填" in e for e in errs)
    assert any("product_name 必填" in e for e in errs)

    rec = {"candidate_id": "c1", "product_name": "牵引绳", "category": "X",
           "market": "CN", "platform": "taobao", "target_user": "养狗人",
           "currency": "CNY", "field_evidence": {}, "is_synthetic": False}
    errs = pm_validation.validate_section("candidate", rec)
    assert not errs

    rec["supplier_count"] = -1
    errs = pm_validation.validate_section("candidate", rec)
    assert any("supplier_count" in e for e in errs)

    rec["supplier_count"] = 3
    rec["typo_field"] = "x"
    errs = pm_validation.validate_section("candidate", rec)
    assert any("typo_field 不在字段字典中" in e for e in errs)


def test_csv_safe_prevents_formula_injection():
    assert pm_validation.csv_safe("=SUM(A1)") == "'=SUM(A1)"
    assert pm_validation.csv_safe("+cmd") == "'+cmd"
    assert pm_validation.csv_safe("-1") == "'-1"
    assert pm_validation.csv_safe("普通文本") == "普通文本"
    assert pm_validation.csv_safe(None) == ""


def test_calibration_proposal_accept_creates_new_version(db):
    """接受提案只新增版本，不覆盖旧版。"""
    from app.auth import create_owner
    from app.domain.evidence import canonical_json, content_hash
    from app.db import connect
    owner_id = create_owner(db, "owner1", "pw123")
    conn = connect(db)
    conn.execute(
        "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
        " created_at) VALUES('mv1','STRATEGY','v1',?,?,datetime('now'))",
        (canonical_json({"v": "v1"}), content_hash({"v": "v1"})))
    conn.commit()
    conn.close()

    prop = calibration.save_proposal(
        db, model_version_id="mv1",
        proposed={"changes": ["阈值调整"], "samples": 3,"config":{"engine":"legacy-v4.2.2","minimum_margin":"8"}},
        comparison={"old_accuracy": 0.5, "new_accuracy": 0.6})
    assert prop["state"] == "PROPOSED"

    nv = calibration.accept_proposal(db, proposal_id=prop["id"], owner_id=owner_id,
                                     new_version="v2")
    assert nv["version"] == "v2"
    assert nv["supersedes_id"] == "mv1"

    # 旧版仍在
    conn = connect(db)
    cnt = conn.execute("SELECT COUNT(*) c FROM model_versions").fetchone()["c"]
    conn.close()
    assert cnt == 2


def test_calibration_reject(db):
    from app.auth import create_owner
    from app.domain.evidence import canonical_json, content_hash
    from app.db import connect
    owner_id = create_owner(db, "owner1", "pw123")
    conn = connect(db)
    conn.execute(
        "INSERT INTO model_versions(id, kind, version, content_json, content_hash,"
        " created_at) VALUES('mv1','STRATEGY','v1',?,?,datetime('now'))",
        (canonical_json({"v": "v1"}), content_hash({"v": "v1"})))
    conn.commit()
    conn.close()
    prop = calibration.save_proposal(db, model_version_id="mv1",
                                     proposed={}, comparison={})
    r = calibration.reject_proposal(db, proposal_id=prop["id"], owner_id=owner_id)
    assert r["state"] == "REJECTED"


def test_cli_commands_run(tmp_path):
    """CLI score/learn/accept-proposal 可执行且退出码正确。"""
    import subprocess, sys
    from pathlib import Path
    venv_py = sys.executable
    base = str(Path(__file__).resolve().parents[2])
    db = tmp_path / "cli.db"
    init_db(db)

    # init + score（空库也可运行）
    r = subprocess.run([venv_py, "-m", "app.cli", "score", "--data-dir", str(tmp_path)],
                       capture_output=True, text=True, cwd=base, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "共 0 个商品" in r.stdout

    # learn 无模型版本 → 非零
    r = subprocess.run([venv_py, "-m", "app.cli", "learn", "--data-dir", str(tmp_path)],
                       capture_output=True, text=True, cwd=base, timeout=60)
    assert r.returncode == 1
