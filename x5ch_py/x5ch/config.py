"""アプリ設定。Crystal版 cmd/config.cr に対応(環境変数 + ホームディレクトリのデフォルトパス方式)。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    discord_bot_token: str
    discord_channel_id: str
    history_file: str
    queue_file: str
    lock_file: str
    pid_file: str
    webhook_urls_file: str
    cache_expiration: float  # 秒
    user_agent: str


def _env_or(key: str, fallback: str) -> str:
    v = os.environ.get(key)
    return v if v else fallback


def load_config() -> AppConfig:
    home = str(Path.home())
    return AppConfig(
        discord_bot_token=os.environ.get("X5CH_DISCORD_BOT_TOKEN", ""),
        discord_channel_id=os.environ.get("X5CH_DISCORD_CHANNEL_ID", ""),
        history_file=_env_or("X5CH_HISTORY_FILE", str(Path(home) / ".x5ch_history.json")),
        queue_file=_env_or("X5CH_QUEUE_FILE", str(Path(home) / ".x5ch_queue.json")),
        lock_file=_env_or("X5CH_LOCK_FILE", str(Path(home) / ".x5ch.lock")),
        pid_file=_env_or("X5CH_PID_FILE", str(Path(home) / ".x5ch.pid")),
        webhook_urls_file=_env_or(
            "X5CH_WEBHOOK_URLS_FILE", str(Path(home) / ".x5ch_webhooks.json")
        ),
        cache_expiration=300.0,
        user_agent="w3m/0.5.3",
    )
