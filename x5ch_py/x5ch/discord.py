"""Discord Bot API連携。Crystal版 discord/manager.cr に対応。

Webhookではなく、Botトークンでチャンネル内にスレッドを作成しメッセージを送信する方式。
"""
from __future__ import annotations

import asyncio
import json as json_mod

import httpx

from .errors import DiscordAPIError
from .models import Post

API_BASE = "https://discord.com/api/v10"


class Manager:
    """Discord Botとしてスレッド作成・メッセージ送信を行う。"""

    def __init__(self, token: str, channel_id: str, api_base: str = API_BASE):
        self._token = token
        self._channel_id = channel_id
        self._api_base = api_base

    def enabled(self) -> bool:
        if not self._token or "YOUR_BOT_TOKEN" in self._token:
            return False
        return bool(self._channel_id)

    async def create_thread(self, title: str) -> str:
        if not self.enabled():
            raise DiscordAPIError("Discord機能が無効です(トークン/チャンネルID未設定)")

        safe_title = truncate_runes(title, 95)
        body = json_mod.dumps({"name": safe_title, "type": 11, "auto_archive_duration": 1440})
        url = f"{self._api_base}/channels/{self._channel_id}/threads"

        status, resp_body = await self._do_post(url, body)
        if status != 201:
            raise DiscordAPIError(f"{status} {resp_body}")

        try:
            parsed = json_mod.loads(resp_body)
            thread_id = parsed.get("id")
        except json_mod.JSONDecodeError:
            thread_id = None
        if not thread_id:
            raise DiscordAPIError("レスポンス解析エラー: id フィールドがありません")
        return thread_id

    async def send_message(self, discord_thread_id: str, post: Post) -> None:
        if not self.enabled() or not discord_thread_id:
            return

        header = f"**{post.num}** : {post.name} : {post.date}"
        full_content = f"{header}\n{post.message}"

        if len(full_content) <= 2000:
            await self._post_content(discord_thread_id, full_content)
            return

        parts = split_by_runes(full_content, 1900)
        for i, part in enumerate(parts):
            content = part
            if i < len(parts) - 1:
                content += "\n(続く...)"
            await self._post_content(discord_thread_id, content)
            await asyncio.sleep(0.5)

    async def _post_content(self, thread_id: str, content: str) -> None:
        body = json_mod.dumps({"content": content})
        url = f"{self._api_base}/channels/{thread_id}/messages"

        while True:
            status, resp_body = await self._do_post(url, body)

            if status == 429:
                await asyncio.sleep(self._parse_retry_after(resp_body))
                continue

            if status >= 400:
                raise DiscordAPIError(f"{status} {resp_body}")

            return

    @staticmethod
    def _parse_retry_after(resp_body: str) -> float:
        try:
            parsed = json_mod.loads(resp_body)
            retry_after = float(parsed.get("retry_after", 0))
        except (json_mod.JSONDecodeError, TypeError, ValueError):
            return 1.0
        return retry_after if retry_after > 0 else 1.0

    async def _do_post(self, url: str, body: str) -> tuple[int, str]:
        headers = {
            "Authorization": f"Bot {self._token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, content=body)
        return resp.status_code, resp.text


def truncate_runes(s: str, n: int) -> str:
    """文字数(コードポイント数)基準で切り詰める。"""
    if len(s) <= n:
        return s
    return s[:n] + "..."


def split_by_runes(s: str, n: int) -> list[str]:
    """文字数基準でn文字ずつのチャンクに分割する。"""
    return [s[i : i + n] for i in range(0, len(s), n)]
