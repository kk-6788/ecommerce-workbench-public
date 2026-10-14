from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response, RedirectResponse
from pydantic import BaseModel, Field
from app.api.auth_routes import require_csrf, get_current_owner, get_settings
from app.api.pages_routes import _common, _require_page, templates
from app.services import table_imports as service

router = APIRouter(tags=["table-imports"])

@router.get("/imports")
def page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse("/login")
    ctx = _common(request)
    ctx.update(page_title="表格导入", import_kinds=service.KINDS)
    return templates.TemplateResponse(request, "imports.html", ctx)

@router.get("/api/v1/table-imports/template/{kind}")
def template(kind: str, owner=Depends(get_current_owner)):
    if kind not in service.KINDS:
        raise HTTPException(400, "类型无效")
    text = "\ufeff" + ",".join(x[0] for x in service.KINDS[kind]["fields"].values()) + "\r\n"
    return Response(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-template.csv"'})

@router.post("/api/v1/table-imports/preview")
async def preview(request: Request, kind: str = Form(...), file: UploadFile = File(...), owner=Depends(require_csrf)):
    try:
        data = await file.read(service.LIMIT + 1)
        return {"data": service.prepare(get_settings(request).db_path, owner["owner_id"], file.filename or "", data, kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        await file.close()

class CommitIn(BaseModel):
    sheet: str = Field(max_length=200)
    mapping: dict[str, str]

@router.post("/api/v1/table-imports/{batch}/commit")
def commit(batch: str, body: CommitIn, request: Request, owner=Depends(require_csrf)):
    try:
        return {"data": service.commit(get_settings(request).db_path, owner["owner_id"], batch, body.sheet, body.mapping)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@router.get("/api/v1/table-imports")
def history(request: Request, owner=Depends(get_current_owner)):
    return {"data": service.history(get_settings(request).db_path, owner["owner_id"])}

@router.get("/api/v1/table-imports/records/{kind}")
def records(kind: str, request: Request, owner=Depends(get_current_owner)):
    try:
        return {"data": service.records(get_settings(request).db_path, owner["owner_id"], kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/api/v1/table-imports/export/{kind}')
def export(kind: str, request: Request, owner=Depends(get_current_owner)):
    import csv
    import io
    try:
        rows = service.export_rows(get_settings(request).db_path, owner['owner_id'], kind)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    columns = list(rows[0]) if rows else [v[0] for v in service.KINDS[kind]['fields'].values()]
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(columns)
    def safe(value):
        text = '' if value is None else str(value)
        if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
            return "'" + text
        return text
    for row in rows:
        writer.writerow([safe(row.get(column)) for column in columns])
    return Response('\ufeff' + stream.getvalue(), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="{kind}-export.csv"', 'Cache-Control': 'no-store'})


@router.get('/help')
def help_page(request: Request):
    if _require_page(request) is None:
        return RedirectResponse('/login')
    ctx = _common(request)
    ctx['page_title'] = '使用教程'
    return templates.TemplateResponse(request, 'help.html', ctx)
