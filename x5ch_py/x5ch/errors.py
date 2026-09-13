"""例外階層。Crystal版の各層のErrorクラスに対応させている。"""
from __future__ import annotations


class X5chError(Exception):
    """このプロジェクトの全例外の基底クラス。"""


class FetchError(X5chError):
    """HTTP取得層での失敗(fetch.cr FetchError)。"""


class NetworkFetchError(FetchError):
    """ネットワークレベルの失敗(タイムアウト・接続エラー)。リトライ対象の分類に使う。"""


class MenuError(X5chError):
    """板メニュー取得の失敗(menu.cr MenuError)。"""


class ThreadsError(X5chError):
    """スレッド一覧取得の失敗(threads.cr ThreadsError)。"""


class SearchError(X5chError):
    """全板検索の失敗(search.cr SearchError)。"""


class BrowserError(X5chError):
    """Browser層での失敗。url には実際に失敗したURLを可能な限り持たせる。"""

    def __init__(self, message: str, url: str | None = None):
        super().__init__(message)
        self.url = url


THREAD_GONE_MESSAGE = "スレッドはdat落ちしています"


class ThreadGoneError(BrowserError):
    """スレッドがdat落ちしている場合。"""

    def __init__(self) -> None:
        super().__init__(THREAD_GONE_MESSAGE)


class DiscordAPIError(X5chError):
    """Discord API呼び出しレベルでの失敗(例: 429)。Workerがこれでリトライ分類する。"""

    def __init__(self, msg: str):
        super().__init__(f"Discord API Error: {msg}")
