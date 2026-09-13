"""Browser調整役。Crystal版 fivechbrowser/browser.cr に対応。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import menu as menu_mod
from . import nextthread as nextthread_mod
from . import parse as parse_mod
from . import search as search_mod
from . import threads as threads_mod
from .errors import BrowserError, FetchError, ThreadGoneError, ThreadsError
from .export import ExportResult, ExportSource, ExportThread, dat_timestamp_to_rfc3339
from .export import extract_board_name, parse_posts_for_export
from .fetch import Fetcher
from .models import Board, Category, Post, ThreadInfo


@dataclass
class _CachedThreads:
    data: list[ThreadInfo]
    time: float


class Browser:
    """各モジュール関数(get_menu/get_threads/search_global/export等)をまとめ、
    メニュー・スレ一覧のキャッシュを保持する調整役。
    """

    def __init__(self, user_agent: str, history, cache_expire: float):
        self._history = history
        self._cache_expire = cache_expire
        self._fetcher = Fetcher(user_agent)
        self._menu_lock = asyncio.Lock()
        self._menu_cache: list[Category] | None = None
        self._thread_lock = asyncio.Lock()
        self._thread_cache: dict[str, _CachedThreads] = {}

    async def get_menu(self) -> list[Category]:
        async with self._menu_lock:
            if self._menu_cache is not None:
                return self._menu_cache
            cats = await menu_mod.get_menu(self._fetcher)
            self._menu_cache = cats
            return cats

    async def invalidate_menu_cache(self) -> None:
        async with self._menu_lock:
            self._menu_cache = None

    async def get_threads(self, board: Board, force_reload: bool = False) -> list[ThreadInfo]:
        """force_reload=Falseの場合、cache_expire以内ならキャッシュを返す。
        取得に失敗した場合、期限切れであっても古いキャッシュがあればそれにフォールバックする。
        """
        if not force_reload:
            async with self._thread_lock:
                cached = self._thread_cache.get(board.url)
                if cached and (time.time() - cached.time) < self._cache_expire:
                    return cached.data

        async with self._thread_lock:
            stale = self._thread_cache.get(board.url)

        try:
            threads = await threads_mod.get_threads(self._fetcher, self._history, board)
        except ThreadsError:
            if stale:
                return stale.data
            raise

        async with self._thread_lock:
            self._thread_cache[board.url] = _CachedThreads(data=threads, time=time.time())

        return threads

    async def search_global(self, keyword: str) -> list[ThreadInfo]:
        return await search_mod.search_global(self._fetcher, self._history, keyword)

    async def export_thread_data(
        self, board_url: str, dat_file: str, since_num: int = 0
    ) -> ExportResult:
        """指定スレッドをアーカイブ用の完全なJSON構造で取得する。

        since_num > 0 の場合、その番号以下のレスは posts から除外する
        (thread.post_count は除外前の総レス数のまま)。
        """
        read_url = build_read_url(board_url, dat_file)

        try:
            body, final_url = await self._fetcher.fetch(read_url)
        except FetchError as ex:
            raise BrowserError(f"スレッド取得に失敗しました: {ex}", read_url) from ex

        html_content = menu_mod.decode_to_utf8(body)

        if "dat落ち" in html_content:
            raise ThreadGoneError()

        uri = urlparse(board_url)
        if not uri.scheme or not uri.netloc:
            raise BrowserError(f"board_urlの解析に失敗: {board_url}", board_url)

        dat_num = dat_file.removesuffix(".dat")
        thread_external_id = f"5ch:{uri.netloc}{uri.path}{dat_num}"

        all_posts = parse_posts_for_export(html_content, thread_external_id)

        title = ""
        tm = nextthread_mod.TITLE_TAG_PATTERN.search(html_content)
        if tm:
            title = nextthread_mod.TITLE_SUFFIX_PATTERN.sub("", tm.group(1).strip()).strip()

        posts = [p for p in all_posts if p.num > since_num] if since_num > 0 else all_posts

        board_name = extract_board_name(html_content)
        created_at = dat_timestamp_to_rfc3339(dat_file)

        return ExportResult(
            source=ExportSource(
                provider="5ch",
                board_url=board_url,
                dat_file=dat_file,
                thread_url=final_url,
                scraped_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            thread=ExportThread(
                external_id=thread_external_id,
                title=title,
                board_name=board_name or None,
                created_at=created_at or None,
                post_count=len(all_posts),
            ),
            posts=posts,
        )

    async def get_thread_data(self, t: ThreadInfo) -> list[Post]:
        """指定スレッドの全レスを取得する。取得後、900番以降のレスから次スレを検出して履歴に追加する。"""
        read_url = build_read_url(t.board_url, t.dat_file)

        try:
            body, final_url = await self._fetcher.fetch(read_url)
        except FetchError as ex:
            raise BrowserError(f"スレッド取得に失敗しました: {ex}", read_url) from ex

        html_content = menu_mod.decode_to_utf8(body)

        if "dat落ち" in html_content:
            raise ThreadGoneError()

        posts = parse_mod.parse_posts(html_content)
        t.url = final_url

        await nextthread_mod.detect_and_add_next_thread(self._fetcher, self._history, posts, t)

        return posts


def build_read_url(board_url: str, dat_file: str) -> str:
    """board_url + dat_file から read.cgi の完全URLを組み立てる。"""
    uri = urlparse(board_url)
    if not uri.scheme or not uri.netloc:
        raise BrowserError(f"board_urlの解析に失敗: {board_url}", board_url)

    segments = [s for s in uri.path.split("/") if s]
    if not segments:
        raise BrowserError(f"board_urlから板名を特定できません: {board_url}", board_url)
    board_name = segments[-1]

    dat_num = dat_file.removesuffix(".dat")
    if not dat_num.isdigit():
        raise BrowserError(f"不正なdat_file: {dat_file}", board_url)

    return f"{uri.scheme}://{uri.netloc}/test/read.cgi/{board_name}/{dat_num}/"
