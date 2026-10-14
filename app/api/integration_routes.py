"""Owner-only external connections. All actions use session + CSRF."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel, Field
from app.api.auth_routes import get_current_owner, require_csrf, get_settings
from app.api.pages_routes import _common, _require_page, templates
from app.services.libtv_connection import Connection
from app.services.provider_credentials import Credentials

router = APIRouter(tags=['integrations'])


class ProviderIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192, repr=False)
    model: str = Field(min_length=1, max_length=200)


@router.get('/api/v1/integrations/providers')
def providers(request: Request, session=Depends(get_current_owner)):
    return invoke(Credentials(get_settings(request)).list, session['owner_id'])


@router.put('/api/v1/integrations/providers/{provider}')
def save_provider(provider: str, body: ProviderIn, request: Request, session=Depends(require_csrf)):
    return invoke(Credentials(get_settings(request)).save, session['owner_id'], provider, body.api_key, body.model)


@router.delete('/api/v1/integrations/providers/{provider}')
def delete_provider(provider: str, request: Request, session=Depends(require_csrf)):
    return invoke(Credentials(get_settings(request)).delete, session['owner_id'], provider)


def connection(request):
    return Connection(get_settings(request))


def invoke(fn, *args):
    try:
        return {'data': fn(*args)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get('/integrations', response_class=HTMLResponse)
def page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse('/login')
    return templates.TemplateResponse(request, 'integrations.html', {
        **_common(request), 'page_title': '服务连接'})


@router.get('/api/v1/integrations/libtv')
def status(request: Request, session=Depends(get_current_owner)):
    return invoke(connection(request).status, session['owner_id'])


@router.post('/api/v1/integrations/libtv/connect')
def start(request: Request, session=Depends(require_csrf)):
    # Canonical loopback host ensures the authorization callback receives the same session.
    if request.url.hostname != '127.0.0.1':
        raise HTTPException(422, '请从 http://127.0.0.1:8510 登录平台后再连接')
    return invoke(connection(request).start, session)


@router.get('/integrations/libtv/callback')
def callback(request: Request, state: str = '', code: str = '', error: str = '',
             session=Depends(get_current_owner)):
    if error or not state or not code:
        return RedirectResponse('/integrations?libtv=cancelled', headers={'Referrer-Policy': 'no-referrer'})
    try:
        connection(request).callback(session, state, code)
        outcome = 'connected'
    except ValueError:
        outcome = 'failed'
    return RedirectResponse('/integrations?libtv=' + outcome,
                            headers={'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'})


@router.post('/api/v1/integrations/libtv/disconnect')
def disconnect(request: Request, session=Depends(require_csrf)):
    return invoke(connection(request).disconnect, session['owner_id'])


@router.post('/api/v1/integrations/libtv/check')
def check(request: Request, session=Depends(require_csrf)):
    return invoke(connection(request).call, session['owner_id'], 'doctor', {})


@router.post('/api/v1/integrations/libtv/projects')
def projects(request: Request, session=Depends(require_csrf)):
    return invoke(connection(request).call, session['owner_id'], 'list_projects', {})


class ModelQuery(BaseModel):
    query: str = Field(default='Seedance', max_length=100)
    node_type: str = Field(default='video', pattern='^(image|video)$')


@router.post('/api/v1/integrations/libtv/models')
def models(body: ModelQuery, request: Request, session=Depends(require_csrf)):
    return invoke(connection(request).call, session['owner_id'], 'search_models', {
        'action': 'search', 'nodeType': body.node_type, 'query': body.query})
