"""CLI 入口：init-db / doctor / create-owner / import-baseline / verify-baseline / backup / restore / score / backtest / learn / compare。"""
from __future__ import annotations

import argparse
import json
import getpass
import sys
from pathlib import Path

from app.db import connect, init_db
from app.settings import Settings


def _settings_from_args(args) -> Settings:
    return Settings(data_dir=args.data_dir)


def cmd_init_db(args) -> int:
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    print(f"数据库已初始化：{s.db_path}")
    return 0


def cmd_doctor(args) -> int:
    s = _settings_from_args(args)
    s.ensure_dirs()
    print(f"[doctor] 数据目录：{s.data_dir}")
    print(f"[doctor] 数据库路径：{s.db_path}")
    ok = s.db_path.exists()
    print(f"[doctor] 数据库文件存在：{'是' if ok else '否（先运行 init-db）'}")
    if ok:
        conn = connect(s.db_path)
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            print(f"[doctor] 表数量：{len(tables)}")
            print(f"[doctor] 表列表：{', '.join(tables)}")
        finally:
            conn.close()
    return 0


def cmd_create_owner(args) -> int:
    from app.auth import create_owner
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    username = input("用户名 [owner]: ").strip() or "owner"
    password = getpass.getpass("密码：")
    if not password:
        print("错误：密码不能为空", file=sys.stderr)
        return 1
    confirm = getpass.getpass("确认密码：")
    if password != confirm:
        print("错误：两次密码不一致", file=sys.stderr)
        return 1
    create_owner(s.db_path, username, password)
    print(f"owner 已创建：{username}")
    return 0


def cmd_import_baseline(args) -> int:
    from app.services.imports import import_baseline
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    report = import_baseline(s.db_path, Path(args.source))
    print(f"导入完成：机会 {report['total_opportunities']}，重复跳过 {report['duplicates_skipped']}，"
          f"错误 {len(report['errors'])}")
    for e in report["errors"][:20]:
        print(f"  [错误] {e}", file=sys.stderr)
    return 0 if not report["errors"] else 1


def cmd_verify_baseline(args) -> int:
    from app.services.imports import verify_baseline
    s = _settings_from_args(args)
    init_db(s.db_path)
    report = verify_baseline(s.db_path,source_hash=args.source_hash)
    print(f"基线核验：机会 {report['total']}，评分词 {report['scored']}，"
          f"排除/观察 {report['excluded_or_observed']}，品类组 {report['groups']}，"
          f"零供应样本 {report['zero_supply_samples']}（未知 {report['unknown_supply_samples']}）")
    if report["pass"]:
        print("PASS：基线不变量全部满足")
        return 0
    print("FAIL：以下不变量未满足 →", "；".join(report["failed"]), file=sys.stderr)
    return 1


def cmd_backup(args) -> int:
    from app.services.backup import backup_data
    s = _settings_from_args(args)
    s.ensure_dirs()
    out = backup_data(s, Path(args.output))
    print(f"备份完成：{out}")
    return 0


def cmd_restore(args) -> int:
    from app.services.backup import restore_data
    target = Path(args.data_dir) if args.data_dir else (Path.cwd() / "work" / "restore-check")
    result = restore_data(Path(args.backup), target)
    print(f"恢复完成：{result}")
    conn=connect(result/'app.db')
    try:
        state=conn.execute("SELECT value FROM runtime_state WHERE key='restore'").fetchone()[0]
        print(f"运行状态：{state}；待核对记录请登录后查看 /api/v1/recovery，未知请求禁止自动重发")
    finally: conn.close()
    return 0


# ============ G03：PM 决策与学习链路 ============

def _connect_products(db_path) -> list[dict]:
    """读取全部商品（含 facts/offers/evidence/predictions）。"""
    from app.db import connect
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM products ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["facts"] = json.loads(d.pop("facts_json") or "{}")
            d["offers"] = [dict(x) for x in conn.execute(
                "SELECT * FROM offers WHERE product_id=?", (r["id"],)).fetchall()]
            d["predictions"] = [dict(x) for x in conn.execute(
                "SELECT * FROM predictions WHERE product_id=? ORDER BY created_at DESC",
                (r["id"],)).fetchall()]
            for p in d["predictions"]:
                p["result"] = json.loads(p.pop("result_json") or "{}")
                p["input"] = json.loads(p.pop("input_json") or "{}")
            out.append(d)
        return out
    finally:
        conn.close()


def cmd_select_model(args):
    from app.services.model_config import select_strategy
    s=_settings_from_args(args)
    select_strategy(s.db_path,args.version_id,args.owner_id)
    print("已选择模型版本："+args.version_id)
    return 0

def cmd_score(args) -> int:
    """对全部商品运行分层评分，输出排名与预测建议。"""
    from app.domain.legacy_rules import TierInput, grade_tier
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    products = _connect_products(s.db_path)
    results = []
    for p in products:
        f = p["facts"]
        from decimal import Decimal as D
        try:
            from app.domain.inputs import effective_input
            inp, frozen = effective_input(p)
            tier = grade_tier(inp)
            from app.api.business_routes import _ensure_model_versions
            from app.services.model_config import apply_selected
            versions=_ensure_model_versions(s.db_path)
            tier, model=apply_selected(s.db_path,tier,versions["STRATEGY"])
            results.append({"product_id": p["id"], "title": p["title"],
                            "layer": tier.layer, "cm": str(tier.cm) if tier.cm is not None else None,
                            "block_execution": tier.block_execution,
                            "reason": tier.reason})
        except Exception as exc:
            results.append({"product_id": p["id"], "title": p["title"],
                            "layer": "ERROR", "cm": None,
                            "block_execution": True, "reason": str(exc)})
    ranked = sorted(results, key=lambda r: (0 if r["layer"] in
                    ("T1", "T1候选", "T2", "T2观察型", "T3", "T3候选") else 1,
                    -(float(r["cm"]) if r["cm"] else 0)))
    for r in ranked:
        flag = "可执行" if not r["block_execution"] else "阻塞"
        print(f"{r['layer']:<8} {flag:<4} CM={str(r['cm']):<8} {r['title']} — {r['reason']}")
    print(f"共 {len(ranked)} 个商品")
    return 0


def _dec_or_none(v):
    from decimal import Decimal as D, InvalidOperation
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None



def _observed_cases(db_path):
    from app.domain.backtesting import ObservedCase
    conn=connect(db_path)
    try:
        rows=conn.execute("SELECT r.*,p.result_json,p.created_at AS predicted_at FROM reviews r JOIN predictions p ON p.id=r.prediction_id JOIN products x ON x.id=r.product_id WHERE x.is_synthetic=0 AND NOT EXISTS (SELECT 1 FROM reviews newer WHERE newer.supersedes_id=r.id)").fetchall()
        cases=[]
        from collections import Counter
        counts=Counter(row["prediction_id"] for row in rows)
        for row in rows:
            if counts[row["prediction_id"]]!=1: continue
            result=json.loads(row["result_json"])
            evidence=json.loads(row["findings_json"])
            if evidence.get("evidence_status")!="SUFFICIENT": continue
            cases.append(ObservedCase(candidate_id=row["prediction_id"],predicted_decision=result["recommended_action"],predicted_score=float(result["final_score"]) if result.get("final_score") is not None else None,outcome_status=row["outcome"]))
        return cases
    finally: conn.close()

def cmd_backtest(args) -> int:
    """用已观察商品回测：预测 TEST 且结果 VALIDATED = 命中。未测商品不算失败。"""
    from app.domain.backtesting import ObservedCase, backtest_model
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    cases = _observed_cases(s.db_path)
    report = backtest_model(cases, top_n=args.top_n or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_learn(args) -> int:
    """回测后生成校准提案（仅提案，不自动应用）。"""
    from app.domain.calibration import save_proposal
    from app.domain.backtesting import ObservedCase, backtest_model
    s = _settings_from_args(args)
    s.ensure_dirs()
    init_db(s.db_path)
    cases = _observed_cases(s.db_path)
    report = backtest_model(cases, top_n=args.top_n or None)
    mv_id = args.model_version_id
    if not mv_id:
        from app.db import connect as c2
        conn = c2(s.db_path)
        try:
            row = conn.execute(
                "SELECT id FROM model_versions WHERE kind='STRATEGY'"
                " ORDER BY created_at DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        if not row:
            print("错误：无 STRATEGY 模型版本，请先 init 或传 --model-version-id", file=sys.stderr)
            return 1
        mv_id = row["id"]
    proposed={"status":"PROVISIONAL","samples":report.get("conclusive_samples",0),"report":report,"changes":[]}
    comparison={"old_accuracy":None,"new_accuracy":None}
    margin=getattr(args,"minimum_margin",None)
    if margin is not None:
        from app.services.model_config import validate_strategy
        from app.domain.backtesting import compare_models
        from dataclasses import replace
        from decimal import Decimal
        config=validate_strategy({"engine":"legacy-v4.2.2","minimum_margin":margin,"status":"PROVISIONAL"})
        conn=connect(s.db_path)
        try:
            old_cases,new_cases=[],[]
            for case in cases:
                row=conn.execute("SELECT result_json,input_json,product_id,strategy_version_id FROM predictions WHERE id=?",(case.candidate_id,)).fetchone()
                if row["strategy_version_id"]!=mv_id: continue
                from app.domain.inputs import effective_input
                from app.domain.legacy_rules import grade_tier
                from app.services.model_config import apply_config
                frozen=json.loads(row['input_json'])
                if not frozen.get('effective_input'): continue
                inp,_=effective_input({'id':row['product_id'],'title':frozen['effective_input']['keyword'],'revision':frozen['revision'],'facts':frozen['effective_input']})
                tier=apply_config(grade_tier(inp),config)
                action='KILL' if tier.layer=='历史排除' else 'WAIT' if tier.block_execution else 'TEST'
                old_cases.append(case)
                new_cases.append(replace(case,predicted_decision=action))
        finally: conn.close()
        comparison=compare_models(old_cases,new_cases)
        if comparison["decidable_samples"]:
            proposed.update(config=config,samples=comparison["decidable_samples"],changes=[{"minimum_margin":margin}],prediction_ids=[c.candidate_id for c in old_cases])
    proposal=save_proposal(s.db_path,model_version_id=mv_id,proposed=proposed,comparison=comparison)
    print(f"校准提案已保存：{proposal['id']}（仅本批描述性对照，未经真实泛化验证）")
    if proposed.get("config"):
        print(f"接受：python -m app.cli accept-proposal --data-dir \"{s.data_dir}\" --proposal-id {proposal['id']} --new-version <新版本名>")
    else:
        print("待观察：缺有效样本或明确候选配置，不能接受；补足后使用 --minimum-margin 指定候选门槛进行同批对照。")
    return 0


def cmd_accept_proposal(args) -> int:
    from app.domain.calibration import accept_proposal
    s = _settings_from_args(args)
    init_db(s.db_path)
    try:
        nv = accept_proposal(s.db_path, proposal_id=args.proposal_id,
                             owner_id=args.owner_id, new_version=args.new_version)
        print(f"已接受并生成新版本：{nv['version']}（{nv['id']}）")
        return 0
    except (KeyError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


def cmd_compare(args) -> int:
    """新旧模型对比（同批已观察样本，不自动切换）。"""
    from app.domain.backtesting import ObservedCase, compare_models
    s = _settings_from_args(args)
    init_db(s.db_path)
    old_cases = [_obs_from_file(p) for p in (args.old_cases or "").split(",") if p]
    new_cases = [_obs_from_file(p) for p in (args.new_cases or "").split(",") if p]
    if not old_cases or not new_cases:
        print(json.dumps({"common_samples":0,"note":"请明确提供同批次新旧模型预测文件；不把事后预测拼接为实验"},ensure_ascii=False))
        return 0
    report = compare_models(old_cases, new_cases)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _obs_from_file(path: str):
    from app.domain.backtesting import ObservedCase
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ObservedCase(**data)


def cmd_export_candidates(args) -> int:
    """导出商品为 pm-fields candidate CSV（防公式执行）。"""
    from app.domain.pm_validation import csv_safe
    s = _settings_from_args(args)
    init_db(s.db_path)
    products = _connect_products(s.db_path)
    import csv as _csv
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    from app.domain.pm_validation import pm_fields, validate_section
    columns=list(pm_fields()["candidate"])
    records=[]
    for product in products:
        record={key:product["facts"].get(key) for key in columns}
        record.update(candidate_id=product["id"],product_name=product["title"],category=product["category"],timestamp=product["updated_at"],is_synthetic=bool(product["is_synthetic"]))
        if not record["supplier"] and product["offers"]: record["supplier"]=product["offers"][0]["supplier"]
        errors=validate_section("candidate",record)
        if errors: raise ValueError(product["title"]+"："+"；".join(errors))
        records.append(record)
    with open(out,"w",encoding="utf-8-sig",newline="") as stream:
        writer=_csv.writer(stream)
        writer.writerow(columns)
        for record in records: writer.writerow([csv_safe(record[key]) for key in columns])
    print(f"已导出 {len(products)} 个候选到 {out}")
    return 0


def cmd_import_outcomes(args) -> int:
    """导入 outcome CSV 并校验；逐行记录错误，空值保留 null。"""
    from app.domain.pm_validation import validate_section
    from app.domain.metrics import save_metric_snapshot
    import csv as _csv
    s = _settings_from_args(args)
    init_db(s.db_path)
    errors = []
    imported = 0
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        for line_no, row in enumerate(_csv.DictReader(f), start=2):
            from app.domain.pm_validation import decode_csv
            try: rec = decode_csv("outcome", row)
            except ValueError as exc:
                errors.append(f"行{line_no}: {exc}")
                continue
            errs = ["生产结果拒绝合成数据"] if rec.get("is_synthetic") else []
            if errs:
                errors.append(f"行{line_no}: {'；'.join(errs)}")
                continue
            conn = connect(s.db_path)
            try:
                prod=conn.execute('SELECT * FROM products WHERE id=?',(rec.get('candidate_id'),)).fetchone()
                if not prod or prod['is_synthetic']: raise ValueError('商品不存在或属于模拟资料')
                version=conn.execute("SELECT id FROM model_versions WHERE kind='STRATEGY' AND (id=? OR version=?)",(rec.get('model_version'),rec.get('model_version'))).fetchall()
                if len(version)!=1: raise ValueError('model_version 不存在或不唯一')
                for eid in rec.get('evidence_ids') or []:
                    ev=conn.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone()
                    if not ev or ev['product_id']!=prod['id'] or ev['is_synthetic'] or ev['evidence_kind']!='FACT':
                        raise ValueError('结果证据不存在、跨商品或非真实事实')
            except ValueError as exc:
                errors.append(f'行{line_no}: {exc}')
                continue
            finally: conn.close()
            metrics = {**rec,
                "platform": rec.get("platform"), "account_id": rec.get("account_id"),
                "channel": rec.get("channel"), "source_timezone": rec.get("source_timezone"),
                "granularity": rec.get("granularity"), "measure_mode": rec.get("measure_mode"),
                "period_start": rec.get("test_start_date"),
                "period_end": rec.get("test_end_date"),
                "impressions": _int_or_none(rec.get("impressions")),
                "clicks": _int_or_none(rec.get("clicks")),
                "orders": _int_or_none(rec.get("orders")),
                "add_to_cart": _int_or_none(rec.get("add_to_cart")),
                "revenue": rec.get("revenue"), "ad_spend": rec.get("ad_spend"),
            }
            try:
                save_metric_snapshot(
                    s.db_path, product_id=prod["id"], listing_id=None,
                    source_key=f"import:{rec.get('candidate_id')}:{rec.get('model_version')}:{rec.get('test_start_date')}:{rec.get('test_end_date')}",
                    metrics=metrics, is_synthetic=rec["is_synthetic"])
                imported += 1
            except ValueError as e:
                errors.append(f"行{line_no}: {e}")
    print(f"导入 {imported} 条；错误 {len(errors)} 条")
    for e in errors[:20]:
        print(f"  [错误] {e}", file=sys.stderr)
    return 1 if errors else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="xuanpin-assistant", description="电商作业平台 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_data_dir(p):
        p.add_argument("--data-dir", default=None, help="数据目录（默认 runtime/）")

    for name, help_text in [
        ("init-db", "初始化数据库"),
        ("doctor", "环境体检"),
        ("create-owner", "创建 owner 账号"),
        ("import-baseline", "导入 30 词基线"),
        ("verify-baseline", "核验基线"),
        ("backup", "备份数据"),
        ("restore", "从备份恢复（到新目录）"),
        ("score", "对全部商品运行分层评分"),
        ("select-model", "明确选用策略版本"),
        ("backtest", "用已观察商品回测模型"),
        ("learn", "生成校准提案（仅提案）"),
        ("accept-proposal", "接受校准提案并生成新版本"),
        ("compare", "新旧模型对比"),
        ("export-candidates", "导出商品为 candidate CSV"),
        ("import-outcomes", "导入 outcome CSV 并校验"),
    ]:
        p = sub.add_parser(name, help=help_text)
        add_data_dir(p)
        if name == "verify-baseline":
            p.add_argument("--source-hash",default=None,help="指定导入批次 hash")
        elif name == "import-baseline":
            p.add_argument("--source", required=True, help="基线源目录（strategy-20260914）")
        elif name == "backup":
            p.add_argument("--output", default="work/backup-check", help="备份输出目录")
        elif name == "restore":
            p.add_argument("--backup", required=True, help="备份目录")
        elif name == "backtest":
            p.add_argument("--top-n", type=int, default=None, help="Top-N 成功率口径")
        elif name == "learn":
            p.add_argument("--minimum-margin",default=None,help="明确拟比较的最低贡献利润（元）；不提供则仅生成待观察记录")
            p.add_argument("--top-n", type=int, default=None)
            p.add_argument("--model-version-id", default=None)
        elif name == "select-model":
            p.add_argument("--version-id",required=True)
            p.add_argument("--owner-id",required=True)
        elif name == "accept-proposal":
            p.add_argument("--proposal-id", required=True)
            p.add_argument("--new-version", required=True)
            p.add_argument("--owner-id", default=None)
        elif name == "compare":
            p.add_argument("--old-cases", default="")
            p.add_argument("--new-cases", default="")
        elif name == "export-candidates":
            p.add_argument("--output", required=True, help="输出 CSV 路径")
        elif name == "import-outcomes":
            p.add_argument("--input", required=True, help="输入 outcome CSV 路径")

    args = parser.parse_args(argv)
    handler = {
        "init-db": cmd_init_db,
        "doctor": cmd_doctor,
        "create-owner": cmd_create_owner,
        "import-baseline": cmd_import_baseline,
        "verify-baseline": cmd_verify_baseline,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "score": cmd_score,
        "select-model": cmd_select_model,
        "backtest": cmd_backtest,
        "learn": cmd_learn,
        "accept-proposal": cmd_accept_proposal,
        "compare": cmd_compare,
        "export-candidates": cmd_export_candidates,
        "import-outcomes": cmd_import_outcomes,
    }.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except Exception as exc:  # 所有命令失败必须非 0 退出
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
