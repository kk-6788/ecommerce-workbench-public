"""素材服务：上传格式探测、实际解码校验、文件 hash、用途/版本、混款淘汰、路径安全。

- 拒绝路径穿越、超限/损坏/伪装文件、URL 访问本机管理端口/内网
- R20：内容白名单 + 实际解码/尺寸限制；SVG/HTML 等活性内容拒收
- R21：文件对象按 hash 去重；商品/SKU/用途/版本使用独立关联记录
- R23：is_synthetic 贯穿素材登记，生产聚合可排除合成数据
- R35：resolve 后真实路径归属判断；URL scheme/域名白名单 + IP 范围检查
- 私有预览认证（不暴露任意路径）
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import uuid
from pathlib import Path

from app.db import connect
from app.domain.evidence import utcnow_iso
from app.services import plans

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200MB
MAX_IMAGE_PIXELS = 60_000_000         # 约 8000x7500 上限，防解压炸弹
ALLOWED_KINDS = {"IMAGE", "VIDEO", "BRIEF", "SCRIPT", "OTHER"}
ALLOWED_ROLES = {"MAIN", "SKU", "DETAIL", "REFERENCE", "RAW", "OUTPUT", "OTHER"}

# 拒绝的 URL scheme 与能力域名白名单（R35：下载入口只允许白名单域名）
ALLOWED_URL_SCHEMES = {"http", "https"}
ALLOWED_DOWNLOAD_DOMAINS = {
    "alicdn.com", "taobao.com", "tmall.com", "1688.com", "douyin.com",
    "byteimg.com", "pstatp.com", "zjcdn.com", "xiaohongshu.com", "xhscdn.com",
    "sinaimg.cn", "weibo.com", "qq.com", "gtimg.com", "w3.org", "example.com",
}


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode_verify_image(data: bytes) -> tuple[str, int, int]:
    """R20：Pillow 实际解码校验图片，返回 (mime, width, height)。

    拒绝损坏文件（仅头字节伪造）、SVG/HTML 等活性内容与超尺寸文件。
    """
    from PIL import Image, UnidentifiedImageError
    try:
        img = Image.open(__import__("io").BytesIO(data))
        img.load()  # 触发完整解码，损坏数据在此暴露
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError("图片解码失败：文件损坏或格式不支持")
    if img.format is None or img.format.upper() in ("SVG", "HTML"):
        raise ValueError("禁止 SVG/HTML 等活性内容入库")
    w, h = img.size
    if w * h > MAX_IMAGE_PIXELS:
        raise ValueError(f"图片尺寸过大（{w}x{h}）")
    mime = {
        "JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp",
        "GIF": "image/gif", "BMP": "image/bmp", "TIFF": "image/tiff",
    }.get(img.format.upper())
    if mime is None:
        raise ValueError(f"图片格式不支持：{img.format}")
    return mime, w, h


def _detect_video(data: bytes, metadata=None) -> str:
    import subprocess, shutil, tempfile
    probe, decoder = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not decoder: raise ValueError("未发现视频校验工具，不能验证视频；请先配置 ffmpeg")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"upload.bin"
        path.write_bytes(data)
        try:
            result = subprocess.run([probe,"-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,timeout=15)
            info=json.loads(result.stdout)
            videos=[s for s in info.get("streams",[]) if s.get("codec_type")=="video"]
            duration=float(info.get("format",{}).get("duration",0))
            if result.returncode or not videos or not 0<duration<=3600: raise ValueError("视频流或时长无效")
            if any(not 0<s.get("width",0)*s.get("height",0)<=MAX_IMAGE_PIXELS for s in videos): raise ValueError("视频尺寸无效")
            decoded=subprocess.run([decoder,"-v","error","-xerror","-i",str(path),"-map","0:v:0","-f","null","-"],capture_output=True,timeout=60)
            if decoded.returncode or decoded.stderr: raise ValueError("视频解码失败")
            if metadata is not None:
                metadata.update(duration_seconds=duration,width=videos[0]['width'],height=videos[0]['height'],codec=videos[0].get('codec_name'))
            return "video/webm" if "webm" in info.get("format",{}).get("format_name","") else "video/mp4"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("视频损坏或超过校验时限") from exc


def _detect_mime(data: bytes, filename: str, kind: str) -> str:
    """按 kind 探测真实格式（不只看扩展名；WebP 伪装 jpg 等）。

    IMAGE 走 Pillow 实际解码（R20）；VIDEO 走容器头探测；其他回退内容/扩展名。
    """
    if kind == "IMAGE":
        mime, _w, _h = _decode_verify_image(data)
        return mime
    if kind == "VIDEO":
        return _detect_video(data)
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # 回退到扩展名
    guess, _ = mimetypes.guess_type(filename)
    return guess or "application/octet-stream"


def store_asset(db_path, assets_dir: Path, *, product_id: str, kind: str, role: str,
                data: bytes, filename: str, sku_key: str | None = None,
                metadata: dict | None = None, source_evidence_id: str | None = None,
                parent_id: str | None = None, is_synthetic: bool = False) -> dict:
    """存储素材并登记（含用途关联）。返回 asset 记录（含 usages）。

    R21：文件对象按 hash 去重；每次登记的用途（role/sku_key）写入
    asset_usages，同字节可被多个用途/SKU 引用且身份正确。
    R23：is_synthetic 落库，供生产聚合排除合成数据。
    """
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"kind 无效：{kind}")
    if role not in ALLOWED_ROLES:
        raise ValueError(f"role 无效：{role}")
    if len(data) == 0:
        raise ValueError("文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("文件超过 200MB 上限")
    if ".." in (filename or "") or "/" in (filename or "") or "\\" in (filename or ""):
        raise ValueError("文件名含非法路径字符")

    check = connect(db_path)
    try:
        if not check.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone(): raise ValueError("商品不存在")
        if source_evidence_id:
            ev=check.execute("SELECT * FROM evidence WHERE id=?",(source_evidence_id,)).fetchone()
            if not ev or ev["product_id"]!=product_id: raise ValueError("来源证据不属于商品")
            is_synthetic = bool(is_synthetic or ev["is_synthetic"])
        if parent_id and not check.execute("SELECT 1 FROM asset_usages WHERE asset_id=? AND product_id=?",(parent_id,product_id)).fetchone(): raise ValueError("父素材未显式关联到商品")
    finally: check.close()
    file_hash = _file_hash(data)
    metadata = dict(metadata or {})
    mime = _detect_video(data, metadata) if kind == "VIDEO" else _detect_mime(data, filename, kind)
    # 按 hash 存放，避免覆盖
    ext = Path(filename).suffix.lower() or ".bin"
    rel = f"{kind.lower()}/{file_hash[:2]}/{file_hash}{ext}"
    target = _resolve_asset_path(assets_dir, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)

    conn = connect(db_path)
    try:
        # 文件对象按 hash 全局去重（R21）
        existing = conn.execute(
            "SELECT * FROM assets WHERE file_hash=? AND relative_path=?",
            (file_hash, rel)).fetchone()
        if existing:
            asset_id = existing["id"]
        else:
            asset_id = str(uuid.uuid4())
            metadata = metadata or {}
            metadata["filename"] = filename
            metadata["original_size"] = len(data)
            conn.execute(
                "INSERT INTO assets(id, product_id, kind, role, sku_key, relative_path,"
                " file_hash, mime_type, metadata_json, source_evidence_id, parent_id,"
                " review_status, is_synthetic, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,?)",
                (asset_id, product_id, kind, role, sku_key, rel, file_hash, mime,
                 json.dumps(metadata, ensure_ascii=False), source_evidence_id,
                 parent_id, 1 if is_synthetic else 0, utcnow_iso()),
            )
        # 用途关联：同 asset + product + role + sku 幂等
        usage = conn.execute(
            "SELECT id FROM asset_usages WHERE asset_id=? AND product_id=? AND role=?"
            " AND (sku_key IS ? OR sku_key=?)",
            (asset_id, product_id, role, sku_key, sku_key)).fetchone()
        if usage is None:
            usage_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO asset_usages(id, asset_id, product_id, role, sku_key,"
                " version, created_at, source_evidence_id, is_synthetic) VALUES(?,?,?,?,?,?,?,?,?)",
                (usage_id, asset_id, product_id, role, sku_key, 1, utcnow_iso(),source_evidence_id,int(is_synthetic)))
        else:
            usage_id = usage["id"]
            conn.execute("UPDATE asset_usages SET is_synthetic=MAX(is_synthetic,?) WHERE id=?",(int(is_synthetic),usage_id))
        conn.commit()
        out = get_asset(db_path, asset_id)
        current = dict(conn.execute("SELECT * FROM asset_usages WHERE id=?",(usage_id,)).fetchone())
        out.update({"usage_id":usage_id,"product_id":product_id,"role":role,"sku_key":sku_key,"review_status":current["review_status"],"is_synthetic":current["is_synthetic"],"version":current["version"]})
        return out
    finally:
        conn.close()


def get_asset(db_path, asset_id: str) -> dict:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise KeyError("素材不存在")
        out = dict(row)
        out["metadata"] = json.loads(out.pop("metadata_json"))
        out["usages"] = [dict(r) for r in conn.execute(
            "SELECT * FROM asset_usages WHERE asset_id=? ORDER BY created_at",
            (asset_id,)).fetchall()]
        return out
    finally:
        conn.close()


def _resolve_asset_path(assets_dir: Path, rel: str) -> Path:
    """resolve 后真实路径归属判断（R35）：符号链接/同名前缀兄弟目录均不可越界。"""
    base = assets_dir.resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise ValueError("路径越界")
    return p


def resolve_asset_path(assets_dir: Path, asset: dict) -> Path:
    """解析相对路径；拒绝越界。"""
    return _resolve_asset_path(assets_dir, asset["relative_path"])


def mark_review(db_path, asset_id: str, status: str, note=None, *, usage_id=None):
    if status not in ("PENDING","APPROVED","REJECTED"): raise ValueError("status 无效")
    conn=connect(db_path)
    try:
        rows=conn.execute("SELECT * FROM asset_usages WHERE asset_id=? AND (? IS NULL OR id=?)",(asset_id,usage_id,usage_id)).fetchall()
        if not rows: raise ValueError("素材用途不存在")
        if len(rows)!=1: raise ValueError("素材有多个用途，请指定 usage_id 逐项审阅")
        usage=rows[0]
        conn.execute("UPDATE asset_usages SET review_status=?,review_note=?,version=version+1 WHERE id=?",(status,note,usage["id"]))
        conn.commit()
        out=get_asset(db_path,asset_id)
        out.update(dict(conn.execute("SELECT * FROM asset_usages WHERE id=?",(usage["id"],)).fetchone()))
        out['usage_id']=usage['id']
        out['id']=asset_id
        return out
    finally: conn.close()


def _host_is_forbidden(host: str) -> bool:
    """R35：解析后的 IP 范围检查——回环/链路本地/私网/保留均拒绝。"""
    host_l = (host or "").lower().rstrip(".")
    if host_l in {"localhost", "localhost.localdomain"}:
        return True
    # 字面 IPv4/IPv6（IPv6 可能带 [ ]，需先剥离）
    literal = host_l.strip("[]")
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        # 非字面 IP：域名交给白名单检查
        return host_l.endswith(".local")
    return (ip.is_loopback or ip.is_link_local or ip.is_private
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def validate_url_safety(url: str) -> None:
    """媒体下载禁止随输入 URL 访问本机/内网/回环/链路本地地址。

    R35：scheme 白名单 + 能力域名白名单；IPv4/IPv6 解析后的 IP 范围检查；
    公共允许域通过。域名不在白名单（非字面 IP）直接拒绝，避免解析后绕过。
    """
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except ValueError:
        raise ValueError("URL 无效")
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError("仅允许 http/https 下载")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少主机名")
    if _host_is_forbidden(host):
        raise ValueError("禁止访问本机/回环/内网/链路本地地址")
    # 非字面 IP 的域名必须命中能力白名单（含子域）
    literal = host.lower().strip("[]")
    try:
        ipaddress.ip_address(literal)
        return  # 字面公网 IP 少见；为安全保守放行（已在上面排除私网/回环）
    except ValueError:
        pass
    if not any(host.lower() == d or host.lower().endswith("." + d)
               for d in ALLOWED_DOWNLOAD_DOMAINS):
        raise ValueError(f"域名不在允许白名单：{host}")


# ============ 图片工作流（T16）============

def provider_available(registry, provider: str) -> bool:
    """提供方是否已注册（缺失 → CAPABILITY_UNAVAILABLE）。"""
    try:
        registry.get(provider)
        return True
    except KeyError:
        return False


def plan_image_generation(db_path, *, product_id: str, provider: str,
                          sku_key: str, facts: dict, input_asset_hashes: list[str],
                          prompt: str, output_count: int = 1,
                          target_platform: str = "pending-verification",
                          estimated_amount: str | None = None,
                          maximum_amount: str | None = None,
                          currency: str = "CNY") -> dict:
    """形成 IMAGE 生成方案（DRAFT，未批准不生成）。"""
    spec = plans.PlanSpec(
        product_id=product_id,
        action=provider,
        provider=provider,
        requires_content=True,
        requires_cost=maximum_amount is not None,
        payload={
            "kind": "IMAGE",
            "sku_key": sku_key,
            "facts": facts,
            "input_asset_hashes": sorted(input_asset_hashes),
            "prompt": prompt,
            "output_count": output_count,
            "target_platform": target_platform,
            "_cost": {
                "estimated_amount": estimated_amount,
                "maximum_amount": maximum_amount,
                "failure_billing": "unknown"
                                   if not maximum_amount else
                                   "unknown",
            },
        },
        cost=plans.CostSpec(
            currency=currency,
            estimated_amount=estimated_amount,
            maximum_amount=maximum_amount,
            billing_event="one_generation_request",
            failure_billing="unknown" if not maximum_amount else
                            "unknown",
            retry_needs_new_approval=True,
        ),
    )
    return plans.create_plan(db_path, spec=spec)


# ============ 视频工作流（T17）============

def plan_video_generation(db_path, *, product_id: str, provider: str,
                          title: str, script: str, shot_list: list[dict],
                          missing_materials: list[str] | None = None,
                          estimated_amount: str | None = None,
                          maximum_amount: str | None = None,
                          currency: str = "CNY") -> dict:
    """形成 VIDEO 生成/剪辑方案（DRAFT）。缺素材暂停；付费生成单独 plan。

    R03：任何费用报价（estimated 或 maximum）都必须 COST 审批；只有
    maximum_amount 存在时才可执行（费用上限可限制）。缺素材时执行层阻塞。
    """
    spec = plans.PlanSpec(
        product_id=product_id,
        action=provider,
        provider=provider,
        requires_content=True,
        requires_cost=estimated_amount is not None or maximum_amount is not None,
        payload={
            "kind": "VIDEO",
            "title": title,
            "script": script,
            "shot_list": shot_list,
            "missing_materials": missing_materials or [],
            "_cost": {
                "estimated_amount": estimated_amount,
                "maximum_amount": maximum_amount,
                "failure_billing": ("unknown"
                                    if maximum_amount is not None
                                    else "no_charge_no_execute"),
            },
        },
        cost=plans.CostSpec(
            currency=currency,
            estimated_amount=estimated_amount,
            maximum_amount=maximum_amount,
            billing_event="one_video_request",
            failure_billing=("unknown"
                             if maximum_amount is not None
                             else "no_charge_no_execute"),
            retry_needs_new_approval=True,
        ) if maximum_amount is not None or estimated_amount is not None else None,
    )
    return plans.create_plan(db_path, spec=spec)
