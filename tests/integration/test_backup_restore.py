"""备份/恢复测试：一致副本、素材/证据校验、拒绝覆盖、未决 attempt 安全核对模式。"""
from __future__ import annotations

import pytest

from app.db import connect, init_db
from app.services import media
from app.services.backup import backup_data, restore_data
from app.services.products import create_product
from app.settings import Settings
from tests.conftest import make_png_bytes


@pytest.fixture
def settings(tmp_path):
    s = Settings(data_dir=tmp_path / "data")
    s.ensure_dirs()
    init_db(s.db_path)
    return s


def _add_asset(settings, prod, filename="a.png"):
    return media.store_asset(settings.db_path, settings.assets_dir,
                             product_id=prod["id"], kind="IMAGE", role="MAIN",
                             data=make_png_bytes(), filename=filename)


def test_backup_and_restore_roundtrip(settings, tmp_path):
    prod = create_product(settings.db_path, title="备份商品", category="X",
                          strategy_mode="PUHUO")
    _add_asset(settings, prod)

    backup_dir = backup_data(settings, tmp_path / "backup")
    assert (backup_dir / "app.db").exists()
    assert (backup_dir / "backup-manifest.json").exists()
    asset_files = list((backup_dir / "assets").rglob("*"))
    assert len([f for f in asset_files if f.is_file()]) >= 1

    restored = restore_data(backup_dir, tmp_path / "restore")
    assert (restored / "app.db").exists()
    assert (restored / "assets").exists()
    from app.services.products import get_product
    got = get_product(restored / "app.db", prod["id"])
    assert got["title"] == "备份商品"


def test_restore_refuses_nonempty_target(settings, tmp_path):
    backup_dir = backup_data(settings, tmp_path / "backup")
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "x.txt").write_text("occupied")
    with pytest.raises(ValueError):
        restore_data(backup_dir, target)


def test_backup_fails_when_asset_missing(settings, tmp_path):
    """R24：源图缺失 → 备份失败（不再静默 continue）。"""
    prod = create_product(settings.db_path, title="P", category="X", strategy_mode="PUHUO")
    asset = _add_asset(settings, prod)
    # 删除源文件
    p = media.resolve_asset_path(settings.assets_dir, asset)
    p.unlink()
    with pytest.raises(ValueError, match="引用文件缺失|hash 校验失败"):
        backup_data(settings, tmp_path / "backup")


def test_restore_fails_when_backup_tampered(settings, tmp_path):
    """R24：篡改备份素材 → 恢复失败。"""
    prod = create_product(settings.db_path, title="P", category="X", strategy_mode="PUHUO")
    _add_asset(settings, prod)
    backup_dir = backup_data(settings, tmp_path / "backup")
    # 篡改备份中的素材
    asset_files = [p for p in (backup_dir / "assets").rglob("*") if p.is_file()]
    assert asset_files
    asset_files[0].write_bytes(b"tampered!")
    with pytest.raises(ValueError, match="hash|引用缺失"):
        restore_data(backup_dir, tmp_path / "restore")


def test_restore_pending_attempt_enters_reconcile_mode(settings, tmp_path):
    """R25：含 UNKNOWN 的备份进入可查看的待核对模式，不污染最终目录。"""
    prod = create_product(settings.db_path, title="P", category="X", strategy_mode="PUHUO")
    from app.services.plans import PlanSpec, create_plan
    plan = create_plan(settings.db_path, spec=PlanSpec(
        product_id=prod["id"], action="fake", requires_cost=False, requires_content=False))
    conn = connect(settings.db_path)
    conn.execute(
        "INSERT INTO jobs(id, product_id, plan_id, kind, state, input_json, input_hash,"
        " created_at, updated_at) VALUES('j1',?,?,'external','QUEUED','{}','h',"
        " datetime('now'), datetime('now'))", (prod["id"], plan["id"]))
    conn.execute(
        "INSERT INTO external_attempts(id, plan_id, job_id, state, idempotency_key,"
        " request_hash, provider, prepared_at)"
        " VALUES('a1',?,'j1','UNKNOWN','k','h','fake', datetime('now'))", (plan["id"],))
    conn.commit()
    conn.close()

    backup_dir = backup_data(settings, tmp_path / "backup")
    assert backup_dir is not None
    restored = restore_data(backup_dir, tmp_path / "restore")
    # 可查看的待核对状态：不抛错、不重发、标记 NEEDS_RECONCILE
    assert (restored / "app.db").exists()
    state_file = restored / "restore-state.json"
    assert state_file.exists()
    state = state_file.read_text(encoding="utf-8")
    assert "NEEDS_RECONCILE" in state
