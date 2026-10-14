"""素材文件预览：认证后按 id 读取，Range 支持，不暴露任意路径。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.auth_routes import get_current_owner, get_settings
from app.services import media

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

# R20：素材响应策略——不随应用同源执行脚本，防御活性内容
_MEDIA_CSP = "default-src 'none'; sandbox; style-src 'unsafe-inline'"


@router.get("/{asset_id}/file")
def asset_file(asset_id: str, request: Request, _=Depends(get_current_owner)):
    settings = get_settings(request)
    try:
        asset = media.get_asset(settings.db_path, asset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="素材不存在")
    try:
        path = media.resolve_asset_path(settings.assets_dir, asset)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径越界")
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件缺失")
    return FileResponse(
        path, media_type=asset["mime_type"],
        headers={
            "Content-Security-Policy": _MEDIA_CSP,
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
