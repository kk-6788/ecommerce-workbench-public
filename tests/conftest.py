"""共享测试工具：真实图片字节生成（R20 解码校验后伪 PNG 不再可用）。"""
from __future__ import annotations

import io
import pytest


def make_png_bytes(size: tuple[int, int] = (64, 64), color: tuple[int, int, int] = (200, 60, 60)) -> bytes:
    """Pillow 生成真实 PNG。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def make_webp_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 160, 90)).save(buf, format="WEBP")
    return buf.getvalue()


@pytest.fixture
def png_bytes() -> bytes:
    return make_png_bytes()


@pytest.fixture
def jpeg_bytes() -> bytes:
    return make_jpeg_bytes()


@pytest.fixture
def webp_bytes() -> bytes:
    return make_webp_bytes()


def make_video_bytes():
    import subprocess, tempfile, shutil
    from pathlib import Path
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg: pytest.skip("缺少已安装的 ffmpeg，真实视频正向验证不可运行")
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/"valid.mp4"
        subprocess.run([ffmpeg,"-v","error","-f","lavfi","-i","color=c=blue:s=32x32:d=0.2","-pix_fmt","yuv420p",str(path)],check=True,capture_output=True,timeout=15)
        return path.read_bytes()
