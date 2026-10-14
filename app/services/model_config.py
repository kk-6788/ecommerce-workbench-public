"""版本明确选择；只允许有完整配置的追加版本。"""
import json
from decimal import Decimal
from app.db import connect

def validate_strategy(config):
    if not isinstance(config,dict) or config.get('engine')!='legacy-v4.2.2': raise ValueError('不支持的策略引擎')
    if 'minimum_margin' not in config: raise ValueError('缺 minimum_margin 配置')
    value=Decimal(str(config['minimum_margin']))
    if not value.is_finite() or value<0: raise ValueError('minimum_margin 无效')
    return dict(config)

def load_strategy(row):
    config=json.loads(row['content_json'])
    # Only the exact historical v1 shape has a compatibility interpretation.
    if row['version']=='v1' and config=={'version':'v1','kind':'STRATEGY'}:
        return {'engine':'legacy-v4.2.2','minimum_margin':'0','status':'PROVISIONAL'}
    return validate_strategy(config)

def apply_config(tier,config):
    config=validate_strategy(config)
    if tier.cm is not None and tier.cm<Decimal(str(config['minimum_margin'])):
        tier.block_execution=True
        tier.reason+='；低于选定版本最低贡献利润'
        tier.rule_hits.append('版本最低利润门槛')
    return tier

def apply_selected(db_path,tier,version_id):
    conn=connect(db_path)
    try: row=conn.execute('SELECT * FROM model_versions WHERE id=?',(version_id,)).fetchone()
    finally: conn.close()
    return apply_config(tier,load_strategy(row)),dict(row)

def select_strategy(db_path,version_id,owner_id):
    conn=connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM owners WHERE id=?',(owner_id,)).fetchone(): raise ValueError('owner 不存在')
        row=conn.execute("SELECT * FROM model_versions WHERE id=? AND kind='STRATEGY'",(version_id,)).fetchone()
        if not row: raise ValueError('策略版本不存在')
        load_strategy(row)
        conn.execute("INSERT OR REPLACE INTO runtime_state VALUES('selected_strategy',?)",(version_id,))
        from app.services.audit import log_event
        log_event(conn,actor_type='OWNER',actor_id=owner_id,event_type='MODEL_SELECTED',entity_type='MODEL',entity_id=version_id,detail={'content_hash':row['content_hash']})
        conn.commit()
    finally: conn.close()
