"""TUIアプリ本体。Crystal版 cmd/main.cr のエントリポイント(対話TUI部分)に対応。"""
from __future__ import annotations

import sys

from textual.app import App

from ..browser import Browser
from ..config import AppConfig, load_config
from ..discord import Manager as DiscordManager
from ..history import Manager
from ..lock import LockError, acquire_lock_with_handoff, remove_pid, write_pid
from ..transfer import Worker
from .screens import MainMenuScreen


class X5chApp(App):
    """5chanterm TUI(主線: メニュー→板→スレ一覧→Pager)。"""

    TITLE = "5chanterm"

    def __init__(self, browser: Browser, history: Manager, config: AppConfig):
        super().__init__()
        self.browser = browser
        self.history = history
        self.config = config
        self.discord = DiscordManager(config.discord_bot_token, config.discord_channel_id)
        self.worker = Worker(browser, self.discord, history, config.queue_file)

    async def on_mount(self) -> None:
        self.worker.start()
        await self.push_screen(MainMenuScreen())

    async def on_unmount(self) -> None:
        await self.worker.kill()
        await self.worker.wait_until_stopped()


def run() -> None:
    """CLIエントリポイント。ロック取得→アプリ起動→ロック解放まで面倒を見る。"""
    cfg = load_config()

    try:
        lock_file = acquire_lock_with_handoff(cfg.lock_file, cfg.pid_file)
    except LockError as ex:
        print(f"起動に失敗しました: {ex}", file=sys.stderr)
        sys.exit(1)

    write_pid(cfg.pid_file)

    history = Manager(cfg.history_file)
    browser = Browser(cfg.user_agent, history, cfg.cache_expiration)
    app = X5chApp(browser, history, cfg)

    try:
        app.run()
    finally:
        remove_pid(cfg.pid_file)
        lock_file.close()


if __name__ == "__main__":
    run()
