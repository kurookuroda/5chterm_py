"""板内スレッド一覧取得。Crystal版 fivechbrowser/threads.cr に対応。"""
from __future__ import annotations

import re
import time
from typing import Protocol

from .errors import FetchError, ThreadsError
from .fetch import Fetcher
from .menu import decode_to_utf8
from .models import Board, ThreadInfo


class HistoryStore(Protocol):
    async def get_last_read(self, board_url: str, dat_file: str) -> int: ...
    async def exists(self, board_url: str, dat_file: str) -> bool: ...
    async def add_new_thread(self, title: str, board_url: str, dat_file: str) -> None: ...


SUBJECT_LINE_PATTERN = re.compile(r"^(\d+\.dat)<>(.*?)\((\d+)\)\s*$")


async def get_threads(fetcher: Fetcher, history: HistoryStore, board: Board) -> list[ThreadInfo]:
    """板のスレッド一覧(subject.txt)を取得し、勢い順(降順)に並べて返す。

    subject_urlがリダイレクトされた場合、board.url をその場で書き換える
    (Crystal版が参照型Boardで実現していた「呼び出し元への伝播」を、
    Pythonでも同じオブジェクトを書き換えることで再現する)。
    """
    subject_url = board.url + "subject.txt"

    try:
        body, final_url = await fetcher.fetch(subject_url)
    except FetchError as ex:
        raise ThreadsError(f"スレッド一覧の取得に失敗しました: {ex}") from ex

    if final_url != subject_url:
        board.url = re.sub(r"subject\.txt$", "", final_url)

    data = decode_to_utf8(body)

    now = int(time.time())
    threads: list[ThreadInfo] = []

    for line in data.split("\n"):
        m = SUBJECT_LINE_PATTERN.match(line)
        if not m:
            continue

        dat_file = m.group(1)
        title = m.group(2).strip()
        try:
            count = int(m.group(3))
        except ValueError:
            continue

        ikioi = calc_ikioi(dat_file, count, now)
        last_read = await history.get_last_read(board.url, dat_file)

        threads.append(
            ThreadInfo(
                dat_file=dat_file,
                title=title,
                count=count,
                ikioi=ikioi,
                board_url=board.url,
                last_read=last_read,
            )
        )

    threads.sort(key=lambda t: t.ikioi, reverse=True)
    return threads


def calc_ikioi(dat_file: str, count: int, now: int) -> float:
    ts_str = re.sub(r"\.dat$", "", dat_file)
    try:
        ts = int(ts_str)
    except ValueError:
        return 0.0

    elapsed_seconds = now - ts
    if elapsed_seconds < 1:
        elapsed_seconds = 1
    elapsed_days = elapsed_seconds / 86400.0

    return count / elapsed_days
