"""商品档案 API。写操作需要 CSRF + 认证。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.auth_routes import get_current_owner, get_settings, require_csrf
from app.services import products as svc

router = APIRouter(prefix="/api/v1", tags=["products"])


class ProductIn(BaseModel):
    is_synthetic: bool = False
    title: str = Field(..., min_length=1, max_length=200, description="商品标题，必填")
    category: str = Field(..., min_length=1, max_length=100)
    strategy_mode: str = "PUHUO"
    opportunity_id: str | None = None
    batch_id: str | None = None
    facts: dict = Field(default_factory=dict)

    @field_validator("strategy_mode")
    @classmethod
    def _check_mode(cls, v):
        if v not in ("PUHUO", "RND"):
            raise ValueError("strategy_mode 必须是 PUHUO 或 RND")
        return v

    @field_validator("title", "category")
    @classmethod
    def _check_not_blank(cls, v):
        if not v or not v.strip():
            raise ValueError("标题/品类不能为空")
        return v.strip()


class ProductUpdate(BaseModel):
    revision: int = Field(..., ge=1)
    facts: dict | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("title", "category")
    @classmethod
    def _check_not_blank(cls, v):
        if v is not None and not v.strip():
            raise ValueError("标题/品类不能为空")
        return v.strip() if v else v


class OfferIn(BaseModel):
    supplier: str = Field(..., min_length=1)
    sku_key: str = Field(..., min_length=1)
    specification: dict = Field(default_factory=dict)
    procurement_minor: int | None = Field(None, ge=0, description="拿货价（分），非负")
    freight_minor: int | None = Field(None, ge=0)
    currency: str = "CNY"
    sample_count: int | None = Field(None, ge=0)
    verification_status: str = "UNVERIFIED"
    evidence_id: str | None = None

    @field_validator("verification_status")
    @classmethod
    def _check_status(cls, v):
        if v not in ("UNVERIFIED", "VERIFIED", "PENDING", "REJECTED"):
            raise ValueError(f"verification_status 无效：{v}")
        return v


@router.post("/products")
def create_product(body: ProductIn, request: Request, _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        p = svc.create_product(
            settings.db_path, title=body.title, category=body.category,
            strategy_mode=body.strategy_mode, opportunity_id=body.opportunity_id,
            batch_id=body.batch_id, facts=body.facts, is_synthetic=body.is_synthetic,
        )
        return {"data": p}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/products/{product_id}")
def get_product(product_id: str, request: Request, _=Depends(get_current_owner)):
    settings = get_settings(request)
    try:
        return {"data": svc.get_product(settings.db_path, product_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="商品不存在")


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: ProductUpdate, request: Request,
                   _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        return {"data": svc.update_product(
            settings.db_path, product_id, revision=body.revision,
            facts=body.facts, title=body.title, category=body.category)}
    except KeyError:
        raise HTTPException(status_code=404, detail="商品不存在")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except svc.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/products/{product_id}/offers")
def add_offer(product_id: str, body: OfferIn, request: Request, _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        o = svc.add_offer(settings.db_path, product_id=product_id, **body.model_dump())
        return {"data": o}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
