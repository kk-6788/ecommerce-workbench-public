"""应用配置与数据目录解析。不硬编码机器绝对路径。"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "xuanpin-assistant"
APP_VERSION = "0.1.0"
DEFAULT_PORT = 8510

# 允许在 settings 中读取的环境变量
ENV_APP_DATA_DIR = "APP_DATA_DIR"
ENV_APP_HOST = "APP_HOST"
ENV_APP_PORT = "APP_PORT"


class Settings:
    """本地单用户应用设置。"""

    def __init__(self, data_dir: str | Path | None = None, *, test_mode: bool = False):
        self.test_mode = test_mode
        env_dir = os.environ.get(ENV_APP_DATA_DIR)
        if data_dir:
            self.data_dir = Path(data_dir).resolve()
        elif env_dir:
            self.data_dir = Path(env_dir).resolve()
        else:
            # 默认相对调用方当前目录的 runtime/
            self.data_dir = (Path.cwd() / "runtime").resolve()

        self.host = os.environ.get(ENV_APP_HOST, "127.0.0.1")
        try:
            self.port = int(os.environ.get(ENV_APP_PORT, str(DEFAULT_PORT)))
        except ValueError:
            self.port = DEFAULT_PORT

        # 子目录
        self.assets_dir = self.data_dir / "assets"
        self.evidence_dir = self.data_dir / "evidence"
        self.logs_dir = self.data_dir / "logs"
        self.backups_dir = self.data_dir / "backups"
        self.private_dir = self.data_dir / "private"
        self.db_path = self.data_dir / "app.db"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.assets_dir,
            self.evidence_dir,
            self.logs_dir,
            self.backups_dir,
            self.private_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
