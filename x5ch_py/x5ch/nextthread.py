"""次スレ検出。Crystal版 fivechbrowser/nextthread.cr に対応。"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .errors import FetchError
from .fetch import Fetcher
from .menu import decode_to_utf8
from .models import Post, ThreadInfo
from .threads import HistoryStore

CURRENT_BOARD_URL_PATTERN = re.compile(r"^https?://([^/]+)/([^/]+)/")
TITLE_TAG_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TITLE_SUFFIX_PATTERN = re.compile(r"\s*[-|]\s*5ch\.(net|io).*", re.IGNORECASE | re.DOTALL)


async def detect_and_add_next_thread(
    fetcher: Fetcher, history: HistoryStore, posts: list[Post], current: ThreadInfo
) -> None:
    """現スレの900番以降のレスから次スレURLを検出し、未登録なら履歴に追加する。"""
    candidates = [p for p in posts if p.num >= 900]
    if not candidates:
        return

    m = CURRENT_BOARD_URL_PATTERN.match(current.board_url)
    if not m:
        return
    server, board = m.group(1), m.group(2)

    next_thread_pattern = re.compile(
        rf"https?://{re.escape(server)}/test/read\.cgi/{re.escape(board)}/(\d+)/?"
    )

    for post in candidates:
        for match in next_thread_pattern.finditer(post.message):
            dat_key = match.group(1)
            dat_file = f"{dat_key}.dat"

            if await history.exists(current.board_url, dat_file):
                continue
            if dat_file == current.dat_file:
                continue

            title = await fetch_thread_title(fetcher, current.board_url, dat_key)
            if title:
                await history.add_new_thread(title, current.board_url, dat_file)


async def fetch_thread_title(fetcher: Fetcher, board_url: str, dat_key: str) -> str:
    uri = urlparse(board_url)
    segments = [s for s in uri.path.split("/") if s]
    if not segments:
        return ""
    board_name = segments[-1]

    read_url = f"{uri.scheme}://{uri.netloc}/test/read.cgi/{board_name}/{dat_key}/"

    try:
        body, _ = await fetcher.fetch(read_url)
    except FetchError:
        return ""

    html_content = decode_to_utf8(body)

    tm = TITLE_TAG_PATTERN.search(html_content)
    if not tm:
        return ""

    title = tm.group(1).strip()
    title = TITLE_SUFFIX_PATTERN.sub("", title)
    return title.strip()
