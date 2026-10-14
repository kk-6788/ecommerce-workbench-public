"""证据 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth_routes import get_current_owner, require_csrf, get_settings
from app.services import products as svc

router = APIRouter(prefix="/api/v1", tags=["evidence"])


class EvidenceIn(BaseModel):
    product_id: str | None = None
    opportunity_id: str | None = None
    source_key: str
    source_url: str | None = None
    source_path: str | None = None
    evidence_kind: str = "FACT"
    collection_mode: str = "MANUAL"
    observed_at: str | None = None
    content: dict = Field(default_factory=dict)
    is_synthetic: bool = False


@router.post("/products/{product_id}/evidence")
def add_evidence(product_id: str, body: EvidenceIn, request: Request,
                 _=Depends(require_csrf)):
    settings = get_settings(request)
    try:
        e = svc.add_evidence(
            settings.db_path, product_id=product_id, opportunity_id=body.opportunity_id,
            source_key=body.source_key, source_url=body.source_url,
            source_path=body.source_path, evidence_kind=body.evidence_kind,
            collection_mode=body.collection_mode, observed_at=body.observed_at,
            content=body.content, is_synthetic=body.is_synthetic,
        )
        return {"data": e}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
