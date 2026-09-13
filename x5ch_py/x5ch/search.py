"""全板横断検索。Crystal版 fivechbrowser/search.cr に対応。"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from .errors import FetchError, SearchError
from .fetch import Fetcher
from .models import ThreadInfo
from .parse import HTML_TAG_PATTERN
from .threads import HistoryStore

SEARCH_BASE_URL = "https://ff5ch.syoboi.jp/?q="

SEARCH_RESULT_PATTERN = re.compile(
    r'<a\s+[^>]*href="(https?://[^.]+\.5ch\.(?:net|io)/test/read\.cgi/[^/]+/\d+/?)"[^>]*>(.+?)</a>',
    re.IGNORECASE,
)
THREAD_URL_PATTERN = re.compile(
    r"https?://([^.]+)\.5ch\.(?:net|io)/test/read\.cgi/([^/]+)/(\d+)/?"
)
TITLE_COUNT_PATTERN = re.compile(r"^(.*)\((\d+)\)$")


def to_valid_utf8(body: bytes) -> str:
    """ff5chはUTF-8で応答するため、妥当性チェックのみ行う(不正バイトは置換文字に)。"""
    return body.decode("utf-8", errors="replace")


async def search_global(fetcher: Fetcher, history: HistoryStore, keyword: str) -> list[ThreadInfo]:
    """キーワードでff5ch経由の全板横断検索を行う。"""
    search_url = SEARCH_BASE_URL + quote_plus(keyword)

    try:
        body, _ = await fetcher.fetch(search_url)
    except FetchError as ex:
        raise SearchError(f"検索エラー: {ex}") from ex

    html_content = to_valid_utf8(body)

    results: list[ThreadInfo] = []

    for m in SEARCH_RESULT_PATTERN.finditer(html_content):
        full_url = m.group(1)
        raw_title = m.group(2)

        url_parts = THREAD_URL_PATTERN.match(full_url)
        if not url_parts:
            continue

        server, board_name, dat_num = url_parts.group(1), url_parts.group(2), url_parts.group(3)

        title = HTML_TAG_PATTERN.sub("", raw_title).strip()
        count = 0
        cm = TITLE_COUNT_PATTERN.match(title)
        if cm:
            title = cm.group(1).strip()
            try:
                count = int(cm.group(2))
            except ValueError:
                count = 0

        board_url = f"https://{server}.5ch.io/{board_name}/"
        dat_file = f"{dat_num}.dat"
        last_read = await history.get_last_read(board_url, dat_file)

        results.append(
            ThreadInfo(
                dat_file=dat_file,
                title=title,
                count=count,
                ikioi=0.0,
                board_url=board_url,
                last_read=last_read,
                url=f"https://{server}.5ch.io/test/read.cgi/{board_name}/{dat_num}/",
            )
        )

    return results
