"""认证相关 API 路由。写操作带 CSRF 与 Origin 校验。"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

COOKIE_NAME = "xp_session"


class LoginIn(BaseModel):
    username: str
    password: str


def get_settings(request: Request):
    return request.app.state.settings


def get_current_owner(request: Request):
    """从 cookie 取会话。未登录抛 401。"""
    token = request.cookies.get(COOKIE_NAME)
    settings = get_settings(request)
    sess = auth.validate_session(settings.db_path, token or "")
    if sess is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return sess


def require_csrf(request: Request):
    """写操作：校验 CSRF header 与 Origin 同源。

    validate_session 返回的 csrf 是摘要（csrf_hash），因此对请求令牌取
    SHA-256 摘要后恒定时间比较，避免明文比较与侧信道。
    """
    sess = get_current_owner(request)
    header = request.headers.get("X-CSRF-Token", "")
    if not header:
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
    token_hash = hashlib.sha256(header.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(token_hash, sess["csrf"]):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
    origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
    host = request.headers.get("Host", "")
    if origin and host and not _origin_matches(origin, host):
        raise HTTPException(status_code=403, detail="跨源请求被拒绝")
    return sess


def _origin_matches(origin: str, host: str) -> bool:
    try:
        from urllib.parse import urlparse
        o = urlparse(origin)
        return o.netloc == host
    except ValueError:
        return False


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    settings = get_settings(request)
    try:
        result = auth.create_session(settings.db_path, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    response.set_cookie(
        COOKIE_NAME, result["token"], httponly=True, samesite="lax",
        max_age=14 * 24 * 3600,
    )
    return {"data": {"csrf": result["csrf"]}}


@router.post("/logout")
def logout(request: Request, response: Response, _=Depends(require_csrf)):
    token = request.cookies.get(COOKIE_NAME)
    settings = get_settings(request)
    if token:
        auth.revoke_session(settings.db_path, token)
    response.delete_cookie(COOKIE_NAME)
    return {"data": {"ok": True}}


@router.get("/me")
def me(request: Request):
    sess = get_current_owner(request)
    return {"data": {"owner_id": sess["owner_id"]}}


class RegisterIn(BaseModel):
    username: str
    password: str
    password_confirm: str


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response):
    # Browser-only same-origin JSON endpoint; local bootstrap is never exposed remotely.
    if request.headers.get("origin") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "请从平台页面创建账号")
    settings = get_settings(request)
    sess = auth.validate_session(settings.db_path, request.cookies.get(COOKIE_NAME, ""))
    if sess:
        require_csrf(request)
    else:
        from ipaddress import ip_address
        try:
            local = ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not local:
            raise HTTPException(403, "首次创建账号仅允许本机访问")
    username = body.username.strip()
    if not 1 <= len(username) <= 64 or not 8 <= len(body.password) <= 128:
        raise HTTPException(400, "用户名为 1–64 个字符，密码为 8–128 个字符")
    if body.password != body.password_confirm:
        raise HTTPException(400, "两次输入的密码不一致")
    try:
        auth.create_owner(settings.db_path, username, body.password, first_only=not bool(sess))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    # Keep the current owner's session when creating an additional account.
    if not sess:
        return login(LoginIn(username=username, password=body.password), request, response)
    return {"data": {"created": True}}
