"""板メニュー取得。Crystal版 fivechbrowser/menu.cr に対応。"""
from __future__ import annotations

import json
import re

from .errors import MenuError
from .fetch import Fetcher
from .models import Board, Category

MENU_URL_JSON = "https://menu.5ch.io/bbsmenu.json"
MENU_URL_HTML = "https://menu.5ch.io/bbsmenu.html"

HTML_MENU_PATTERN = re.compile(
    r"(?:<B>([^<]+)</B>)|(?:<A HREF=[\"']?([^ >\"']+)[\"']?[^>]*>([^<]+)</A>)",
    re.IGNORECASE,
)


def decode_to_utf8(data: bytes) -> str:
    """CP932(Shift_JIS)バイト列をUTF-8文字列に変換する。

    Crystal版同様、デコード失敗時も例外にせず可能な限り文字列化する寛容な方針。
    """
    try:
        return data.decode("cp932")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


async def get_menu(fetcher: Fetcher) -> list[Category]:
    """板メニューをJSON優先・HTML fallbackで取得する。"""
    try:
        cats = await get_menu_from_json(fetcher)
        if cats:
            return cats
    except Exception:
        pass  # JSON失敗時はHTMLへフォールバック

    try:
        cats = await get_menu_from_html(fetcher)
        if cats:
            return cats
    except Exception:
        pass

    raise MenuError("メニューの取得に失敗しました(JSON/HTML両方とも失敗)")


async def get_menu_from_json(fetcher: Fetcher, url: str = MENU_URL_JSON) -> list[Category]:
    body, _ = await fetcher.fetch(url)

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = decode_to_utf8(body)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise MenuError(f"JSON解析エラー: {ex}") from ex

    categories: list[Category] = []
    for cat in data.get("menu_list") or []:
        boards: list[Board] = []
        for b in cat.get("category_content") or []:
            url_ = b.get("url")
            if not url_:
                continue
            boards.append(Board(title=b.get("board_name") or "", url=normalize_menu_url(url_)))
        if boards:
            categories.append(Category(title=cat.get("category_name") or "", boards=boards))
    return categories


async def get_menu_from_html(fetcher: Fetcher, url: str = MENU_URL_HTML) -> list[Category]:
    body, _ = await fetcher.fetch(url)
    html_content = decode_to_utf8(body)

    categories: list[Category] = []
    current_category = ""
    current_boards: list[Board] = []
    has_category = False

    for m in HTML_MENU_PATTERN.finditer(html_content):
        cat_name = m.group(1)
        if cat_name:
            if has_category and current_boards:
                categories.append(Category(title=current_category, boards=current_boards))
            current_category = cat_name.strip()
            current_boards = []
            has_category = True
            continue

        href = m.group(2)
        if href:
            if not any(d in href for d in ("5ch.io", "5ch.net", "2ch.net", "bbspink.com")):
                continue
            normalized = normalize_menu_url(href)
            if has_category:
                current_boards.append(Board(title=(m.group(3) or "").strip(), url=normalized))

    if has_category and current_boards:
        categories.append(Category(title=current_category, boards=current_boards))

    return categories


def normalize_menu_url(url: str) -> str:
    url = url.replace("2ch.net", "5ch.io").replace("5ch.net", "5ch.io")
    if url.startswith("http:"):
        url = "https:" + url[len("http:"):]
    if not url.endswith("/"):
        url += "/"
    return url
