"""HTTP取得層。Crystal版 fivechbrowser/fetch.cr に対応。

Crystal版は Accept-Encoding: gzip の明示送信+手動展開、手動リダイレクトループを
自前実装していたが、これは Crystal の HTTP::Client が自動フォロー/自動展開を
しないための対処。httpx はどちらも標準機能で行うため、ここでは httpx の
follow_redirects / 自動gzip展開にそのまま委ねる。
"""
from __future__ import annotations

import httpx

from .errors import FetchError, NetworkFetchError

REQUEST_TIMEOUT = 30.0
MAX_REDIRECTS = 5


class Fetcher:
    """5chへのHTTPアクセスを担当する。"""

    def __init__(self, user_agent: str):
        self._headers = {"User-Agent": user_agent}

    async def fetch(self, url: str) -> tuple[bytes, str]:
        """URLを取得し、本文(生バイト列・エンコーディング変換なし)と最終URLを返す。"""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=REQUEST_TIMEOUT,
                headers=self._headers,
            ) as client:
                resp = await client.get(url)
        except httpx.TooManyRedirects as ex:
            raise FetchError(f"リダイレクト回数が上限({MAX_REDIRECTS})に達しました") from ex
        except httpx.TimeoutException as ex:
            raise NetworkFetchError(f"通信タイムアウト: {ex}") from ex
        except httpx.TransportError as ex:
            raise NetworkFetchError(f"通信エラー: {ex}") from ex

        if not (200 <= resp.status_code < 300):
            raise FetchError(f"HTTP Error: {resp.status_code} {resp.reason_phrase}")

        return resp.content, str(resp.url)
