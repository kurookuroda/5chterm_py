"""Discord Webhook送信。discord.py(Bot API方式)とは独立した、より簡易な送信経路。

Botトークンもスレッド作成も不要で、指定したチャンネルのWebhook URLへ直接
メッセージをPOSTするだけ。複数URLを指定した場合は全URLへ同一内容を
ブロードキャストする。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from .discord import split_by_runes
from .models import Post

MESSAGE_INTERVAL = 1.0


def load_webhook_urls(path: str) -> list[str]:
    """webhook URL一覧を読み込む。JSON配列・改行区切りテキストのどちらにも対応する
    (`#`始まりの行はコメントとして無視)。ファイルが無ければ空リストを返す。
    """
    p = Path(path)
    if not p.exists():
        return []

    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        return [str(u).strip() for u in data if str(u).strip()]

    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def format_post(post: Post) -> str:
    """discord.py(Bot API版)と同じフォーマット。"""
    return f"**{post.num}** : {post.name} : {post.date}\n{post.message}"


async def _post_once(url: str, content: str) -> None:
    body = json.dumps({"content": content})
    headers = {"Content-Type": "application/json"}

    while True:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, content=body)

        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 1.0))
            except (ValueError, TypeError, json.JSONDecodeError):
                retry_after = 1.0
            await asyncio.sleep(retry_after if retry_after > 0 else 1.0)
            continue

        if resp.status_code >= 400:
            raise RuntimeError(f"Webhook送信エラー: {resp.status_code} {resp.text}")

        return


async def send_to_webhook(url: str, content: str) -> None:
    """1件のメッセージを1つのURLへ送信する(2000字超は自動分割)。"""
    if len(content) <= 2000:
        await _post_once(url, content)
        return

    parts = split_by_runes(content, 1900)
    for i, part in enumerate(parts):
        chunk = part + ("\n(続く...)" if i < len(parts) - 1 else "")
        await _post_once(url, chunk)
        await asyncio.sleep(0.5)


async def broadcast_post(urls: list[str], post: Post) -> list[str]:
    """1件の投稿を全URLへブロードキャストする。送信に失敗したURLの一覧を返す。"""
    content = format_post(post)
    failed: list[str] = []
    for url in urls:
        try:
            await send_to_webhook(url, content)
        except Exception:
            failed.append(url)
    return failed


async def broadcast_posts(
    urls: list[str], posts: list[Post], interval: float = MESSAGE_INTERVAL
) -> dict[str, list[int]]:
    """複数の投稿を、1件ずつ全URLへ順にブロードキャストする。

    戻り値は {webhook_url: [送信失敗したpost.numのリスト]} の形。
    """
    failures: dict[str, list[int]] = {u: [] for u in urls}
    for post in posts:
        failed_urls = await broadcast_post(urls, post)
        for u in failed_urls:
            failures[u].append(post.num)
        await asyncio.sleep(interval)
    return failures
