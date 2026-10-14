"""一致快照、完整清单校验、随机暂存区及同盘原子发布。"""
from __future__ import annotations
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path
from app.db import connect, MIGRATIONS_DIR
from app.domain.evidence import utcnow_iso

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''): h.update(chunk)
    return h.hexdigest()

def _safe(base, relative):
    base = Path(base).resolve()
    if ".." in Path(relative).parts or "private" in [part.lower() for part in Path(relative).parts]:
        raise ValueError("备份引用禁止目录穿越及 private")
    path = (base / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(base) or path == base:
        raise ValueError('备份引用路径越界')
    parts = Path(relative).parts
    if parts and parts[0] in ('assets', 'evidence'):
        managed = base / parts[0]
        if managed.resolve() != managed or not path.is_relative_to(managed):
            raise ValueError('备份引用离开受管素材或证据目录')
    return path

def _copy_verify(src, dst, expected_hash):
    if not src.is_file() or _sha256_file(src) != expected_hash: raise ValueError(f'引用文件缺失或 hash 错误：{src.name}')
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if _sha256_file(dst) != expected_hash: raise ValueError('复制后 hash 校验失败')

def _inventory(db_path):
    conn = connect(db_path)
    try:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('数据库损坏')
        if conn.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('数据库外键损坏')
        expected = {p.name for p in MIGRATIONS_DIR.glob('*.sql')}
        actual = {r[0] for r in conn.execute('SELECT name FROM schema_migrations')}
        if actual != expected: raise ValueError('不支持的数据库迁移版本')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'owners','sessions','model_versions','batches','opportunities','products','evidence','offers','predictions','assets','listings','operation_plans','approvals','jobs','external_attempts','cost_ledger','metric_snapshots','reviews','calibration_proposals','audit_events','asset_usages','runtime_state','opportunity_computations'}
        required.update({'ai_check_runs','ai_check_answers','ai_review_events','ai_notifications','ai_calibration_records'})
        if not required <= tables: raise ValueError('数据库缺必要表')
        files = {}
        for row in conn.execute('SELECT relative_path,file_hash FROM assets'):
            files['assets/' + row['relative_path']] = row['file_hash']
        for row in conn.execute("SELECT source_path,content_json FROM evidence WHERE source_path IS NOT NULL AND source_path!=''"):
            meta = json.loads(row['content_json']).get('_file', {})
            if not meta.get('sha256'): raise ValueError('旧本地证据缺文件 hash，请重新登记证据')
            files['evidence/' + row['source_path']] = meta['sha256']
        pending = conn.execute("SELECT COUNT(*) FROM external_attempts WHERE state IN ('PREPARED','SUBMITTED','UNKNOWN','RECONCILING')").fetchone()[0]
        return files, pending
    finally: conn.close()

def _validate(folder):
    try:
        manifest = json.loads((folder/'backup-manifest.json').read_text(encoding='utf-8'))
        if manifest.get('version') != 3: raise ValueError('备份清单版本必须为 3')
        entries = manifest['files']
        if 'app.db' not in entries: raise ValueError('清单缺数据库')
        for rel, meta in entries.items():
            file = _safe(folder, rel)
            if not file.is_file() or file.stat().st_size != meta['size'] or _sha256_file(file) != meta['hash']:
                raise ValueError(f'备份文件缺失或 hash 错误：{rel}')
        refs, pending = _inventory(folder/'app.db')
        if set(entries) != set(refs) | {'app.db'}: raise ValueError('清单与数据库引用不一致')
        for rel, digest in refs.items():
            if entries[rel]['hash'] != digest: raise ValueError('数据库引用与清单 hash 不符')
        return manifest, pending
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise ValueError('备份清单缺失或不完整') from exc

def backup_data(settings, output_dir):
    from app.services.maintenance import backup_lock
    with backup_lock(settings.data_dir) as acquired:
        if not acquired: raise ValueError('另一个备份正在进行')
        return _backup_data(settings, output_dir)

def _backup_data(settings, output_dir):
    output_dir = Path(output_dir).resolve()
    if output_dir == settings.data_dir or settings.data_dir.is_relative_to(output_dir): raise ValueError('备份不能覆盖数据目录')
    if output_dir.exists(): raise ValueError('备份目录已存在，请选择新名称保留上一份备份')
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.backup-', dir=output_dir.parent))
    token = str(uuid.uuid4())
    conn = connect(settings.db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute("DELETE FROM runtime_state WHERE key='maintenance'")
        conn.execute("INSERT INTO runtime_state VALUES('maintenance',?)", (token,))
        conn.commit()
        dest = connect(temp/'app.db')
        try: conn.backup(dest)
        finally: dest.close()
        refs, pending = _inventory(temp/'app.db')
        for rel, digest in refs.items(): _copy_verify(_safe(settings.data_dir, rel), _safe(temp, rel), digest)
        entries = {rel:{'hash': _sha256_file(_safe(temp,rel)), 'size': _safe(temp,rel).stat().st_size} for rel in ['app.db', *refs]}
        (temp/'backup-manifest.json').write_text(json.dumps({'version':3,'created_at':utcnow_iso(),'files':entries,'pending_attempts':pending},ensure_ascii=False,indent=2),encoding='utf-8')
        _validate(temp)
        temp.rename(output_dir)
        return output_dir
    finally:
        conn.rollback()
        conn.execute("DELETE FROM runtime_state WHERE key='maintenance' AND value=?", (token,))
        conn.commit()
        conn.close()
        if temp.exists(): shutil.rmtree(temp)

def restore_data(backup_dir, target_dir):
    backup_dir, target_dir = Path(backup_dir).resolve(), Path(target_dir).resolve()
    if target_dir.exists(): raise ValueError('目标目录存在，拒绝覆盖；请选择新目录')
    manifest, pending = _validate(backup_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.restore-', dir=target_dir.parent))
    try:
        for rel, meta in manifest['files'].items(): _copy_verify(_safe(backup_dir,rel),_safe(temp,rel),meta['hash'])
        shutil.copy2(backup_dir/'backup-manifest.json',temp/'backup-manifest.json')
        _validate(temp)
        conn = connect(temp/'app.db')
        try:
            conn.execute("DELETE FROM runtime_state WHERE key='maintenance'")
            conn.execute("INSERT OR REPLACE INTO runtime_state VALUES('restore',?)", ('NEEDS_RECONCILE' if pending else 'READY',))
            if pending:
                conn.execute("UPDATE external_attempts SET state='UNKNOWN' WHERE state IN ('PREPARED','SUBMITTED','RECONCILING')")
                conn.execute("UPDATE jobs SET state='RECONCILING',lease_owner=NULL,lease_until=NULL WHERE id IN (SELECT job_id FROM external_attempts WHERE state='UNKNOWN')")
                conn.execute("UPDATE operation_plans SET state='UNKNOWN' WHERE id IN (SELECT plan_id FROM external_attempts WHERE state='UNKNOWN')")
            unresolved=conn.execute("SELECT COUNT(*) FROM external_attempts WHERE state IN ('SUBMITTED','UNKNOWN','RECONCILING')").fetchone()[0]
            conn.execute("UPDATE runtime_state SET value=? WHERE key='restore'",('NEEDS_RECONCILE' if unresolved else 'READY',))
            conn.commit()
        finally: conn.close()
        (temp/'restore-state.json').write_text(json.dumps({'state':'NEEDS_RECONCILE' if unresolved else 'READY'}),encoding='utf-8')
        temp.rename(target_dir)
        return target_dir
    finally:
        if temp.exists(): shutil.rmtree(temp)
