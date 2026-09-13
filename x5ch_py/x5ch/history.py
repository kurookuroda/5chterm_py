"""閲覧履歴の永続化。Crystal版 history/manager.cr に対応。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import ThreadInfo


@dataclass
class _Entry:
    res: int
    title: str
    board_url: str
    dat_file: str
    timestamp: int
    discord_thread_id: str | None = None


@dataclass
class RecentThread:
    thread_info: ThreadInfo
    timestamp: int


class Manager:
    """閲覧履歴をJSONファイルに永続化する。"""

    def __init__(self, file_path: str):
        self._file_path = Path(file_path)
        self._lock = asyncio.Lock()
        self._data: dict[str, _Entry] = {}
        self._load()

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}
            return
        self._data = {k: _Entry(**v) for k, v in raw.items()}

    def _save(self) -> bool:
        try:
            body = json.dumps(
                {k: asdict(v) for k, v in self._data.items()},
                ensure_ascii=False,
                indent=2,
            )
            self._file_path.write_text(body, encoding="utf-8")
            return True
        except OSError:
            return False

    @staticmethod
    def _normalize_url(raw_url: str) -> str:
        u = re.sub(r"^https?://", "", raw_url)
        u = re.sub(r"^www\.", "", u)
        u = re.sub(r"/$", "", u)
        u = u.replace("2ch.net", "5ch.io").replace("5ch.net", "5ch.io")
        return u

    def _generate_key(self, board_url: str, dat_file: str) -> str:
        return f"{self._normalize_url(board_url)}::{dat_file}"

    async def get_last_read(self, board_url: str, dat_file: str) -> int:
        async with self._lock:
            entry = self._data.get(self._generate_key(board_url, dat_file))
            return entry.res if entry else 0

    async def get_discord_thread_id(self, board_url: str, dat_file: str) -> str | None:
        async with self._lock:
            entry = self._data.get(self._generate_key(board_url, dat_file))
            return entry.discord_thread_id if entry else None

    async def exists(self, board_url: str, dat_file: str) -> bool:
        async with self._lock:
            return self._generate_key(board_url, dat_file) in self._data

    async def has_history_in_board(self, board_url: str) -> bool:
        async with self._lock:
            target = self._normalize_url(board_url)
            return any(self._normalize_url(e.board_url) == target for e in self._data.values())

    async def has_history_in_category(self, boards) -> bool:
        for b in boards:
            if await self.has_history_in_board(b.url):
                return True
        return False

    async def add_new_thread(self, title: str, board_url: str, dat_file: str) -> None:
        async with self._lock:
            key = self._generate_key(board_url, dat_file)
            if key in self._data:
                return
            self._data[key] = _Entry(
                res=0,
                title=title,
                board_url=board_url,
                dat_file=dat_file,
                timestamp=int(time.time()),
            )
            self._save()

    async def delete_thread(self, board_url: str, dat_file: str) -> bool:
        async with self._lock:
            key = self._generate_key(board_url, dat_file)
            if key not in self._data:
                return False
            del self._data[key]
            self._save()
            return True

    async def update_history(
        self, t: ThreadInfo, res_num: int, discord_thread_id: str | None = None
    ) -> None:
        async with self._lock:
            key = self._generate_key(t.board_url, t.dat_file)
            current = self._data.get(key)

            new_res = max(res_num, current.res) if current else res_num
            new_discord_id = discord_thread_id or (current.discord_thread_id if current else None)

            self._data[key] = _Entry(
                res=new_res,
                title=t.title,
                board_url=t.board_url,
                dat_file=t.dat_file,
                timestamp=int(time.time()),
                discord_thread_id=new_discord_id,
            )
            self._save()

    async def get_recent_threads(self) -> list[RecentThread]:
        """履歴を新しい順(タイムスタンプ降順)に返す。"""
        async with self._lock:
            threads = [
                RecentThread(
                    thread_info=ThreadInfo(
                        dat_file=e.dat_file,
                        title=e.title,
                        board_url=e.board_url,
                        last_read=e.res,
                    ),
                    timestamp=e.timestamp,
                )
                for e in self._data.values()
            ]
            threads.sort(key=lambda r: r.timestamp, reverse=True)
            return threads

    async def all_entries(self) -> list[_Entry]:
        """export-batch用: 全履歴エントリをそのまま返す。"""
        async with self._lock:
            return list(self._data.values())


class NullHistory:
    """永続化を一切行わないダミーのHistoryStore実装。search/read/exportのような
    使い捨てCLIコマンドで使う。"""

    async def get_last_read(self, board_url: str, dat_file: str) -> int:
        return 0

    async def exists(self, board_url: str, dat_file: str) -> bool:
        return False

    async def add_new_thread(self, title: str, board_url: str, dat_file: str) -> None:
        return None
