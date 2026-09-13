"""スレッドHTML→Post変換。Crystal版 fivechbrowser/parse.cr に対応。

Crystal版は正規表現による「レス単位のチャンク分割」を素朴な文字列split相当で
行っていたが(壊れたHTMLに弱い)、Python版では selectolax でDOM構造として
レス要素を辿ることで、この部分をより頑健にしている。レス内の個々のフィールド
抽出・本文の整形(<br>→改行、アンチリンク復元等)はCrystal版のロジックをそのまま踏襲する。
"""
from __future__ import annotations

import html as html_mod
import re

from selectolax.parser import HTMLParser

from .models import Post

# 他モジュール(search.py/export.py)からも参照される汎用のタグ除去パターン。
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# 5chの「アンチリンク」表記("http://"の先頭hを1文字落として"ttp://"にする慣習)の復元。
# 直前がhでない ttp(s):// にマッチさせ、hを補う。
H_RESTORE_PATTERN = re.compile(r"(?<!h)(ttps?://)")


def post_nodes(tree: HTMLParser):
    """レス1件分を表すdiv要素(class属性に'clear post'を含む)を列挙する。

    export.py からも共有されるヘルパー。
    """
    for node in tree.css("div"):
        cls = node.attributes.get("class") or ""
        if "clear post" in cls:
            yield node


def inner_html(node) -> str:
    """selectolaxのNodeから子要素を含む生のinner HTMLを取り出す。"""
    if node is None:
        return ""
    outer = node.html or ""
    start = outer.find(">")
    end = outer.rfind("<")
    if start == -1 or end == -1 or end <= start:
        return ""
    return outer[start + 1 : end]


def _clean_content(content_html: str) -> str:
    """本文HTML断片を、Crystal版と同じ手順でプレーンテキストに整形する。"""
    text = content_html.replace("<br>", "\n")
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = html_mod.unescape(text)
    text = text.strip()
    return H_RESTORE_PATTERN.sub(lambda m: f"h{m.group(0)}", text)


def parse_posts(html_content: str) -> list[Post]:
    """スレッドHTMLから軽量なPost一覧(TUI/search/read等の用途向け)を抽出する。"""
    tree = HTMLParser(html_content)
    posts: list[Post] = []

    for node in post_nodes(tree):
        id_node = node.css_first("span.postid")
        if id_node is None:
            continue
        try:
            num = int(id_node.text(strip=True))
        except ValueError:
            continue

        name = "名無し"
        name_node = node.css_first("span.postusername")
        if name_node is not None:
            name = HTML_TAG_PATTERN.sub("", inner_html(name_node)).strip() or name

        date = ""
        date_node = node.css_first("span.date")
        if date_node is not None:
            date = date_node.text(strip=True)

        uid = ""
        uid_node = node.css_first("span.uid")
        if uid_node is not None:
            uid = uid_node.text(strip=True)
        date = f"{date} {uid}"

        content_node = node.css_first("div.post-content")
        message = _clean_content(inner_html(content_node))

        posts.append(Post(num=num, name=name, date=date, message=message))

    return posts
