"""应用工厂。create_app(data_dir, adapters, test_mode) -> FastAPI app。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.settings import APP_NAME, APP_VERSION, Settings
from app.db import init_db
from app.adapters.registry import default_registry
from app.api import (auth_routes, product_routes, evidence_routes, pages_routes,
                     media_routes, business_routes, workflow_routes, integration_routes, ai_routes, table_routes)


def create_app(data_dir=None, adapters=None, *, test_mode: bool = False, settings: Optional[Settings] = None):
    """构建 FastAPI 应用。

    adapters: 可注入的 adapter 注册表；为 None 时使用默认注册表（fake 仅在 test_mode 可用）。
    """
    settings = settings or Settings(data_dir=data_dir, test_mode=test_mode)
    settings.ensure_dirs()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(title=APP_NAME, version=APP_VERSION)
    from app.services.sensitive_logs import OAuthAccessFilter
    access_logger = logging.getLogger('uvicorn.access')
    if not any(isinstance(f, OAuthAccessFilter) for f in access_logger.filters):
        access_logger.addFilter(OAuthAccessFilter())
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def safe_validation(request, exc):
        if request.url.path.startswith(('/api/v1/integrations/providers/', '/api/v1/auth/')):
            return JSONResponse(status_code=422, content={'detail': '输入格式错误，请检查填写内容'})
        return await request_validation_exception_handler(request, exc)

    # 把 settings 挂到 app.state，供各路由使用
    app.state.settings = settings
    app.state.test_mode = test_mode
    # G01：adapter 注册表挂载到 app.state，业务 API 执行方案时使用
    app.state.registry = adapters if adapters is not None else default_registry(test_mode=test_mode, settings=settings)

    # 初始化数据库（迁移）
    init_db(settings.db_path)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "app": APP_NAME,
            "version": APP_VERSION,
            "test_mode": test_mode,
        }

    app.include_router(auth_routes.router)
    app.include_router(product_routes.router)
    app.include_router(evidence_routes.router)
    app.include_router(media_routes.router)
    app.include_router(business_routes.router)
    app.include_router(workflow_routes.router)
    app.include_router(integration_routes.router)
    app.include_router(ai_routes.router)
    app.include_router(table_routes.router)
    app.include_router(pages_routes.router)

    # 静态文件（CSS/JS）
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    return app
