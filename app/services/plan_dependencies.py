"""在创建与提交边界验证同一组商品、预测和素材版本。"""
import json
from app.domain.evidence import content_hash

def dependency_snapshot(conn, product_id, payload):
    product=conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
    if not product: raise ValueError('商品不存在')
    usages=[dict(r) for r in conn.execute('SELECT u.*,a.kind,a.file_hash,a.relative_path FROM asset_usages u JOIN assets a ON a.id=u.asset_id WHERE u.product_id=? ORDER BY u.id',(product_id,))]
    offers=[dict(r) for r in conn.execute('SELECT * FROM offers WHERE product_id=? ORDER BY id',(product_id,))]
    prediction=conn.execute('SELECT * FROM predictions WHERE product_id=? ORDER BY created_at DESC LIMIT 1',(product_id,)).fetchone()
    from pathlib import Path
    from hashlib import sha256
    dbfile=conn.execute('PRAGMA database_list').fetchone()[2]
    assets=(Path(dbfile).parent/'assets').resolve()
    requested=set(payload.get('input_asset_hashes',payload.get('asset_hashes',[])))
    for usage in usages:
        if usage['file_hash'] in requested or usage['asset_id']==payload.get('video_asset_id'):
            path=(assets/usage['relative_path']).resolve()
            if not path.is_relative_to(assets) or not path.is_file() or sha256(path.read_bytes()).hexdigest()!=usage['file_hash']:
                raise ValueError('素材文件缺失或已改变，请重新上传和审阅')
    kind=payload.get('kind')
    if payload.get('missing_materials'): raise ValueError('缺少视频材料')
    if kind=='VIDEO' and (not payload.get('script') or not payload.get('shot_list')): raise ValueError('缺视频脚本或镜头材料')
    if kind in ('IMAGE','LISTING','VIDEO_PUBLISH'):
        hashes=payload.get('input_asset_hashes',payload.get('asset_hashes',[]))
        if kind in ('IMAGE','LISTING') and not hashes: raise ValueError('缺少素材引用')
        for digest in hashes:
            if not any(u['file_hash']==digest and u['review_status']=='APPROVED' and u['kind']=='IMAGE' for u in usages):
                raise ValueError('素材 hash 不存在、不属于商品或未经审阅')
    if kind=='LISTING':
        if not prediction or prediction['decision']!='TEST': raise ValueError('缺少可执行预测')
        frozen=json.loads(prediction['input_json'])
        if frozen.get('revision')!=product['revision']: raise ValueError('商品已变更，请重新预测')
        if not any(o['verification_status']=='VERIFIED' and o['procurement_minor'] is not None for o in offers): raise ValueError('缺已核验货源')
        for role in ('MAIN','SKU','DETAIL'):
            if not any(u['role']==role and u['kind']=='IMAGE' and u['review_status']=='APPROVED' and u['file_hash'] in hashes for u in usages): raise ValueError('缺已审 '+role+' 图片')
        for sku in payload.get('sku_map',{}):
            if not any(o['sku_key']==sku and o['verification_status']=='VERIFIED' and o['procurement_minor'] is not None for o in offers): raise ValueError('SKU 缺已核验货源')
            if not any(u['role']=='SKU' and u['sku_key']==sku and u['review_status']=='APPROVED' and u['file_hash'] in hashes for u in usages): raise ValueError('SKU 缺匹配审阅图片')
    if kind=='VIDEO_PUBLISH':
        if not any(u['asset_id']==payload.get('video_asset_id') and u['kind']=='VIDEO' and u['review_status']=='APPROVED' for u in usages): raise ValueError('视频未审或归属不匹配')
    return {'revision':product['revision'],'usages':content_hash(usages),'offers':content_hash(offers),'prediction':prediction['id'] if prediction else None}

def validate_dependencies(conn,product_id,payload):
    current=dependency_snapshot(conn,product_id,payload)
    frozen=payload.get('_dependencies')
    if frozen is not None and frozen!=current: raise ValueError('方案依赖已变更，请创建新方案并重新审批')
