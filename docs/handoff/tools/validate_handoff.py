"""Validate the planning package. Never imports/runs the future application.
Python standard library only; no network, no business DB, no credentials.
Run from any directory: python <package>/tools/validate_handoff.py
"""
from pathlib import Path
from decimal import Decimal
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'manifest.json', 'validation-report.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_integrity():
    manifest = read_json(ROOT / 'manifest.json')
    actual = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
              and p.relative_to(ROOT).as_posix() not in EXCLUDED}
    declared = {r['path'] for r in manifest['files']}
    require(actual == declared, f'File set mismatch: missing={declared-actual}, extra={actual-declared}')
    require(len(declared) == len(manifest['files']), 'Duplicate manifest path')
    for row in manifest['files']:
        path = (ROOT / row['path']).resolve()
        require(path.is_relative_to(ROOT), 'Manifest path escapes root')
        require(path.stat().st_size == row['bytes'], f'Size mismatch: {row["path"]}')
        require(sha(path) == row['sha256'], f'Hash mismatch: {row["path"]}')
    return {'hashed_files': len(actual)}


def sources_integrity():
    origins = read_json(ROOT / 'sources/origins.json')['sources']
    for row in origins:
        path = (ROOT / row['path']).resolve()
        require(path.is_relative_to(ROOT / 'sources'), 'Source path outside sources')
        require(sha(path) == row['sha256'], f'Source hash changed: {row["path"]}')
    pm = list((ROOT / 'sources/pm-decision-os-v1.2').rglob('*.md'))
    require(len(pm) == 22, 'PM v1.2 must retain all 22 Markdown files')
    return {'source_files': len(origins), 'pm_markdown_files': len(pm)}


def baseline_shape():
    folder = ROOT / 'sources/strategy-20260914'
    def rows(name):
        with (folder / name).open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    scored = rows('v4.2.2勘误3-25词评分总表.csv')
    excluded = rows('v4.2.2勘误3-5词规则排除表.csv')
    a = {r['关键词'] for r in scored}
    b = {r['关键词'] for r in excluded}
    require(len(scored) == len(a) == 25, 'Scored rows must be 25 unique keywords')
    require(len(excluded) == len(b) == 5 and not a.intersection(b), 'Excluded pool mismatch')
    groups = {r['品类组'] for r in scored}
    require(len(groups) == 9, 'Expected 9 category groups')
    zeros = sum(r['1688样本'].strip() == '0' for r in scored)
    require(zeros == 8, 'Expected 8 zero-supply-sample records')
    observe = next((r for r in excluded if r['关键词'] == '狗狗四脚衣'), None)
    require(observe is not None and '观察' in json.dumps(observe, ensure_ascii=False), 'Observation keyword lost')
    return {'scored':25,'excluded_or_observed':5,'groups':9,'zero_supply_samples':zeros,
            'scope':'row preservation only, not a complete historical rule recalculation'}


def json_and_states():
    json_files = list((ROOT / 'contracts').glob('*.json'))
    for p in json_files:
        read_json(p)
    states = read_json(ROOT / 'contracts/states.json')
    for name in ('job','plan','attempt'):
        model = states[name]
        transitions = model['transitions']
        require(model['initial'] in transitions, f'{name}: missing initial state')
        for source, targets in transitions.items():
            require(all(t in transitions for t in targets), f'{name}/{source}: undefined transition')
    require('READY' not in states['plan']['transitions']['UNKNOWN'], 'Unknown must not resubmit')
    example = read_json(ROOT / 'contracts/operation-plan.example.json')
    require(example['is_synthetic'] and example['not_a_real_quote'], 'Example must not look like real quote')
    require(example['approvals'] == [] and example['plan_hash'] is None, 'Example is deliberately unapproved')
    return {'contract_json_files': len(json_files), 'state_models':3}


def sql_contract():
    with sqlite3.connect(':memory:') as db:
        ddl = (ROOT / 'contracts/schema.sql').read_text(encoding='utf-8')
        db.executescript(ddl)
        db.executescript(ddl)  # initialization contract is repeatable
        require(db.execute('PRAGMA foreign_keys').fetchone()[0] == 1, 'FK not enabled')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'owners','sessions','model_versions','batches','products','opportunities','offers',
                    'evidence','predictions','assets','listings','operation_plans','approvals','jobs',
                    'external_attempts','cost_ledger','metric_snapshots','reviews','calibration_proposals','audit_events'}
        require(tables == required, f'Schema table mismatch {tables ^ required}')
        def rejected(sql, args=()):
            try:
                db.execute(sql,args)
            except sqlite3.IntegrityError:
                return
            raise ValueError('SQL invariant unexpectedly accepted write')
        rejected("INSERT INTO sessions VALUES ('s','missing','hash','csrf','later',NULL,'now')")
        db.execute("INSERT INTO owners VALUES ('o','test','synthetic-hash','synthetic-salt','now')")
        for kind in ('STRATEGY','ENGINE','OUTCOME'):
            db.execute('INSERT INTO model_versions VALUES (?,?,?,?,?,?,?)',(kind,kind,'test','{}','testhash',None,'now'))
        rejected("UPDATE model_versions SET version='changed'")
        db.execute("INSERT INTO products (id,title,category,strategy_mode,stage,facts_json,is_synthetic,created_at,updated_at) VALUES ('p','test','pet','PUHUO','EVIDENCE','{}',1,'now','now')")
        db.execute("INSERT INTO operation_plans (id,product_id,action,plan_hash,payload_json,requires_content,requires_cost,cost_cap_minor,state,expires_at,created_at) VALUES ('pl','p','TEST','hash','{}',1,1,100,'DRAFT','later','now')")
        rejected("UPDATE operation_plans SET cost_cap_minor=200 WHERE id='pl'")
        rejected("INSERT INTO approvals VALUES ('bad','pl','different','o','COST','APPROVE','now','later')")
        db.execute("INSERT INTO jobs (id,product_id,plan_id,kind,state,input_json,input_hash,created_at,updated_at) VALUES ('j','p','pl','TEST','QUEUED','{}','hash','now','now')")
        db.execute("INSERT INTO external_attempts (id,plan_id,job_id,state,idempotency_key,request_hash,provider,prepared_at) VALUES ('a','pl','j','PREPARED','key','hash','fake','now')")
        rejected("INSERT INTO external_attempts (id,plan_id,job_id,state,idempotency_key,request_hash,provider,prepared_at) VALUES ('a2','pl','j','PREPARED','key2','hash','fake','now')")
        require(db.execute('PRAGMA foreign_key_check').fetchall() == [], 'Foreign key violation')
    return {'tables':len(tables),'in_memory_only':True,'ddl_initialization_and_selected_constraints':'PASS',
            'scope':'not application authentication, billing, concurrency or execution tests'}


def fixture_arithmetic():
    fixtures = read_json(ROOT / 'contracts/golden-cases.json')
    require(fixtures['is_synthetic'], 'Fixtures must be labelled synthetic')
    for case in fixtures['margin_cases']:
        if case['procurement'] is None:
            require(case['expected'] is None, 'Missing cost should stay unknown')
            continue
        calculated = Decimal(case['price']) * Decimal('.865') - Decimal(case['procurement']) - Decimal('4.5')
        require(calculated == Decimal(case['expected']), 'Inconsistent margin fixture')
    return {'fixture_arithmetic_cases':len(fixtures['margin_cases']),'scope':'fixture consistency, not implemented algorithm testing'}


def document_contracts():
    docs = sorted(ROOT.glob('[0-9][0-9]-*.md'))
    require(len(docs) == 8, 'Expected documents 00 through 07')
    entry = (ROOT / '00-START-HERE.md').read_text(encoding='utf-8')
    for doc in docs[1:]:
        require(doc.name in entry, f'Entry missing {doc.name}')
    tasks = (ROOT / '04-实施任务.md').read_text(encoding='utf-8')
    actual_tasks = set(re.findall(r'^\| (T\d\d)\s',tasks,re.M))
    expected_tasks = {f'T{i:02}' for i in range(1,29)}
    require(actual_tasks == expected_tasks, 'Implementation task definitions mismatch')
    acceptance = (ROOT / '05-验收与运行手册.md').read_text(encoding='utf-8')
    ids = set(re.findall(r'^\| (AC-\d\d)\s',acceptance,re.M))
    require(ids == {f'AC-{i:02}' for i in range(1,23)}, 'Acceptance definitions mismatch')
    for doc in docs:
        content = doc.read_text(encoding='utf-8')
        for task in re.findall(r'\bT\d\d\b',content):
            require(task in expected_tasks, f'Undefined task {task}')
        for ac in re.findall(r'AC-\d\d',content):
            require(ac in ids, f'Undefined acceptance {ac}')
        for path in re.findall(r'contracts/[A-Za-z0-9_.-]+',content):
            require((ROOT / path).is_file(), f'Missing referenced contract {path}')
    status = read_json(ROOT / 'contracts/implementation-status.json')['tasks']
    require({r['task_id'] for r in status} == expected_tasks, 'Status template task mismatch')
    require(all(r['status'] == 'NOT_STARTED' for r in status), 'Planning template must not claim implementation done')
    return {'entry_documents':len(docs),'implementation_tasks':len(actual_tasks),'acceptance_criteria':len(ids),
            'scope':'entry/contract/task references; historical Obsidian wikilinks not validated'}


def main():
    report = {'checked_at_utc':datetime.now(timezone.utc).isoformat(),
              'scope':'Planning-package integrity only; no application or live-platform test executed.',
              'checks':[]}
    checks = [package_integrity,sources_integrity,baseline_shape,json_and_states,sql_contract,fixture_arithmetic,document_contracts]
    for check in checks:
        try:
            detail = check()
            report['checks'].append({'name':check.__name__,'status':'PASS','detail':detail})
        except Exception as exc:
            report['checks'].append({'name':check.__name__,'status':'FAIL','error':str(exc)})
    report['status'] = 'PASS' if all(c['status']=='PASS' for c in report['checks']) else 'FAIL'
    (ROOT / 'validation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
