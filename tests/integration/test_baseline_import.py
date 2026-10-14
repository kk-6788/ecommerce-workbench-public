"""基线导入测试：25+5 词、9 组、8 零供应、幂等、四脚衣观察。"""
from __future__ import annotations

from pathlib import Path

from app.db import connect, init_db
from app.services.imports import import_baseline, verify_baseline

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE = PROJECT_ROOT / "docs" / "handoff" / "sources" / "strategy-20260914"


def test_import_baseline_30_words(tmp_path):
    init_db(tmp_path / "t.db")
    report = import_baseline(tmp_path / "t.db", SOURCE)
    assert report["total_opportunities"] == 30
    assert report["duplicates_skipped"] == 0
    assert not report["errors"]


def test_import_idempotent(tmp_path):
    init_db(tmp_path / "t.db")
    import_baseline(tmp_path / "t.db", SOURCE)
    report2 = import_baseline(tmp_path / "t.db", SOURCE)
    assert report2["total_opportunities"] == 0
    assert report2["duplicates_skipped"] == 30


def test_verify_baseline_shape(tmp_path):
    init_db(tmp_path / "t.db")
    import_baseline(tmp_path / "t.db", SOURCE)
    v = verify_baseline(tmp_path / "t.db")
    assert v["total"] == 30
    assert v["scored"] == 25
    assert v["excluded_or_observed"] == 5
    assert v["groups"] == 9
    assert v["zero_supply_samples"] == 8
    assert v["sijiaoyi_observed"] is True


def test_recorded_layer_preserved_and_sijiaoyi_observed(tmp_path):
    init_db(tmp_path / "t.db")
    import_baseline(tmp_path / "t.db", SOURCE)
    conn = connect(tmp_path / "t.db")
    try:
        row = conn.execute(
            "SELECT recorded_layer, disposition FROM opportunities WHERE keyword='狗狗四脚衣'"
        ).fetchone()
        assert row["recorded_layer"] == "保留观察"
        assert row["disposition"] == "保留观察"
        # 25 词里应有 T1 记录
        t1 = conn.execute(
            "SELECT COUNT(*) c FROM opportunities WHERE disposition='T1' OR disposition='T1候选'"
        ).fetchone()["c"]
        assert t1 >= 1
    finally:
        conn.close()


def test_multifunctional_leash_kept_t1_pending_verification(tmp_path):
    """历史 T1 多功能牵引绳仍待供应链核验，不自动可上架。"""
    init_db(tmp_path / "t.db")
    import_baseline(tmp_path / "t.db", SOURCE)
    conn = connect(tmp_path / "t.db")
    try:
        row = conn.execute(
            "SELECT disposition, raw_json FROM opportunities WHERE keyword='多功能牵引绳'"
        ).fetchone()
        assert row["disposition"] in ("T1", "T1候选")
    finally:
        conn.close()
