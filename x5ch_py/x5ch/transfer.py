"""Discord転送Worker。Crystal版 transfer/worker.cr に対応。

Crystal版はFiber+Channelでcond変数を再現していたが、Pythonでは asyncio.Condition
がそのまま「ロック+待機+notify_all」を提供するため、素直に書き直している。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import DiscordAPIError, NetworkFetchError, ThreadGoneError
from .models import ThreadInfo

DEFAULT_NETWORK_RETRY_DELAY = 30.0
DEFAULT_DISCORD_RETRY_DELAY = 10.0
DEFAULT_MESSAGE_INTERVAL = 1.0


@dataclass
class Task:
    """Discordへの転送待ちタスク。"""

    title: str
    board_url: str
    dat_file: str


class Worker:
    """キューを監視し、順番にスレッドをDiscordへミラーするバックグラウンドワーカー。"""

    def __init__(
        self,
        browser,
        discord,
        history,
        queue_file: str,
        network_retry_delay: float = DEFAULT_NETWORK_RETRY_DELAY,
        discord_retry_delay: float = DEFAULT_DISCORD_RETRY_DELAY,
        message_interval: float = DEFAULT_MESSAGE_INTERVAL,
    ):
        self._browser = browser
        self._discord = discord
        self._history = history
        self._queue_file = Path(queue_file)
        self._network_retry_delay = network_retry_delay
        self._discord_retry_delay = discord_retry_delay
        self._message_interval = message_interval

        self._cond = asyncio.Condition()
        self._queue: list[Task] = []
        self._working = False
        self._suspended = False
        self._shutdown = False
        self._current_task: Task | None = None
        self._current_idx = 0
        self._total_msgs = 0
        self._last_error = ""

        self._load_queue()
        self._run_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run())

    async def enqueue(self, task: Task) -> None:
        async with self._cond:
            self._last_error = ""
            self._queue.append(task)
            self._save_queue_locked()
            self._cond.notify_all()

    async def delete_at(self, index: int) -> Task | None:
        async with self._cond:
            if index < 0 or index >= len(self._queue):
                return None
            removed = self._queue.pop(index)
            self._save_queue_locked()
            return removed

    async def queue_list(self) -> list[Task]:
        async with self._cond:
            return list(self._queue)

    async def busy(self) -> bool:
        async with self._cond:
            return bool(self._queue) or self._working

    async def remaining_threads(self) -> int:
        async with self._cond:
            return len(self._queue)

    async def last_error(self) -> str:
        async with self._cond:
            return self._last_error

    async def suspend(self) -> None:
        async with self._cond:
            self._suspended = True

    async def resume(self) -> None:
        async with self._cond:
            self._suspended = False
            self._cond.notify_all()

    async def kill(self) -> None:
        """現在処理中のタスクをキュー先頭に戻したうえでシャットダウンする。"""
        async with self._cond:
            self._shutdown = True
            if self._working and self._current_task is not None:
                self._queue.insert(0, self._current_task)
            self._save_queue_locked()
            self._cond.notify_all()

    async def wait_until_stopped(self) -> None:
        if self._run_task is not None:
            await self._run_task

    async def wait_until_done(self) -> None:
        """キューが空になり、処理中のタスクもなくなるまで待つ。"""
        async with self._cond:
            while self._queue or self._working:
                await self._cond.wait()

    async def status_string(self) -> str:
        """TUIのステータス行用の短い文字列を返す。"""
        async with self._cond:
            if self._last_error:
                return f" \x1b[41m[{self._last_error}]\x1b[0m"

            is_busy = bool(self._queue) or self._working
            if not is_busy:
                return ""

            title_info = "準備中"
            if self._current_task is not None:
                title_info = f"{self._current_task.title[:8]}..."

            progress = ""
            if self._working and self._total_msgs > 0:
                progress = f"({self._current_idx}/{self._total_msgs})"

            queue_info = f" [待機スレ:{len(self._queue)}]" if self._queue else ""
            return f" \x1b[33m[転送中:{title_info}{progress}{queue_info}]\x1b[0m"

    async def _run(self) -> None:
        while True:
            task: Task | None = None
            async with self._cond:
                while (not self._queue or self._suspended) and not self._shutdown:
                    await self._cond.wait()

                if self._shutdown and not self._queue:
                    break

                if self._queue:
                    task = self._queue.pop(0)
                    self._current_task = task
                    self._save_queue_locked()

            if task is None:
                continue

            async with self._cond:
                self._working = True
                self._current_idx = 0
                self._total_msgs = 0

            try:
                await self._process_mirror(task)
                async with self._cond:
                    self._last_error = ""
            except Exception as ex:
                await self._handle_task_error(task, ex)

            async with self._cond:
                self._current_task = None
                self._working = False
                self._cond.notify_all()

    async def _process_mirror(self, task: Task) -> None:
        t = ThreadInfo(dat_file=task.dat_file, title=task.title, board_url=task.board_url)

        try:
            posts = await self._browser.get_thread_data(t)
        except ThreadGoneError:
            return  # dat落ちは失敗として扱わず、単に諦める

        if not posts:
            return

        discord_thread_id = await self._history.get_discord_thread_id(
            task.board_url, task.dat_file
        )
        if not discord_thread_id:
            discord_thread_id = await self._discord.create_thread(task.title)
            await self._history.update_history(t, 0, discord_thread_id)

        last_read = await self._history.get_last_read(task.board_url, task.dat_file)
        new_posts = [p for p in posts if p.num > last_read]
        if not new_posts:
            return

        async with self._cond:
            self._total_msgs = len(new_posts)

        for idx, post in enumerate(new_posts):
            async with self._cond:
                if self._shutdown:
                    return
                self._current_idx = idx + 1

            await self._discord.send_message(discord_thread_id, post)
            await self._history.update_history(t, post.num, discord_thread_id)
            await asyncio.sleep(self._message_interval)

    async def _handle_task_error(self, task: Task, ex: Exception) -> None:
        if isinstance(ex, NetworkFetchError):
            await self._set_last_error("ネットワークエラー、後で再試行します")
            await asyncio.sleep(self._network_retry_delay)
            await self._requeue(task)
        elif isinstance(ex, DiscordAPIError):
            await self._set_last_error(_truncate(str(ex), 20))
            await asyncio.sleep(self._discord_retry_delay)
            await self._requeue(task)
        else:
            # 想定外のエラーはエラー表示のみで、requeueはしない。
            await self._set_last_error(_truncate(str(ex), 20))

    async def _set_last_error(self, msg: str) -> None:
        async with self._cond:
            self._last_error = msg

    async def _requeue(self, task: Task) -> None:
        async with self._cond:
            self._queue.append(task)
            self._save_queue_locked()
            self._cond.notify_all()

    def _load_queue(self) -> None:
        if not self._queue_file.exists():
            return
        try:
            raw = json.loads(self._queue_file.read_text(encoding="utf-8"))
            self._queue = [Task(**item) for item in raw]
        except (json.JSONDecodeError, OSError, TypeError):
            self._queue = []

    def _save_queue_locked(self) -> None:
        """呼び出し元が既に self._cond のロックを保持している前提のヘルパー。"""
        try:
            body = json.dumps([asdict(t) for t in self._queue], ensure_ascii=False, indent=2)
            self._queue_file.write_text(body, encoding="utf-8")
        except OSError:
            pass


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "..."
