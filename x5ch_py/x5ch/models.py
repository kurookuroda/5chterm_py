"""コアデータモデル。Crystal版 fivechbrowser/types.cr に対応。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Board:
    """5chの1つの板。"""

    title: str
    url: str


@dataclass
class Category:
    """メニュー上の1カテゴリと、そこに属する板一覧。"""

    title: str
    boards: list[Board] = field(default_factory=list)


@dataclass
class ThreadInfo:
    """1スレッドの識別情報と、閲覧に伴って更新される状態。"""

    dat_file: str
    title: str = ""
    count: int = 0
    ikioi: float = 0.0
    board_url: str = ""
    last_read: int = 0
    url: str = ""

    @property
    def has_new(self) -> bool:
        return self.count > self.last_read


@dataclass
class Post:
    """1レス(TUI/search/read等の軽量用途向け)。"""

    num: int
    name: str
    date: str
    message: str
