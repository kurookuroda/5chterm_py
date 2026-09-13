"""TUI内エクスポート機能(`e`/`E`キー)。Crystal版 cmd/export_file.cr に対応。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .browser import Browser
from .errors import BrowserError, ThreadGoneError

FILENAME_UNSAFE_PATTERN = re.compile(r'[/\\:*?"<>|\r\n]')


def export_output_dir() -> str:
    """`X5CH_EXPORT_DIR`未設定時は、実行時のカレントディレクトリ直下の x5ch_exports。

    ホームディレクトリ基準にすると、Codespaces/Colab等で実行ディレクトリと
    ホームディレクトリが別階層/別マウントになっている環境で見つけにくくなるため。
    """
    env = os.environ.get("X5CH_EXPORT_DIR")
    return env if env else str(Path.cwd() / "x5ch_exports")


def sanitize_filename(s: str) -> str:
    cleaned = FILENAME_UNSAFE_PATTERN.sub("_", s).strip()
    if len(cleaned) > 80:
        cleaned = cleaned[:80]
    return cleaned if cleaned else "no_title"


def export_filename_base(dat_file: str, title: str) -> str:
    dat_num = re.sub(r"\.dat$", "", dat_file)
    return f"{dat_num}_{sanitize_filename(title)}"


async def perform_export(
    browser: Browser, board_url: str, dat_file: str, as_markdown: bool
) -> str:
    """指定スレッドを取得し、json/markdownいずれか1ファイルに書き出す。
    例外は投げず、成否どちらもそのまま表示できるメッセージ文字列を返す。
    """
    try:
        result = await browser.export_thread_data(board_url, dat_file)
    except ThreadGoneError:
        return "エクスポート失敗: スレッドはdat落ちしています"
    except BrowserError as ex:
        url_part = f" (URL: {ex.url})" if ex.url else ""
        return f"エクスポート失敗: {ex}{url_part}"
    except Exception as ex:
        return f"エクスポート失敗: {type(ex).__name__}: {ex}"

    out_dir = Path(export_output_dir())
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return f"エクスポート失敗: 保存先ディレクトリを作成できません ({ex})"

    base = export_filename_base(dat_file, result.thread.title)
    ext = "md" if as_markdown else "json"
    path = out_dir / f"{base}.{ext}"
    body = result.to_markdown() if as_markdown else result.to_pretty_json()

    try:
        path.write_text(body, encoding="utf-8")
    except OSError as ex:
        return f"エクスポート失敗: 書き込みエラー ({ex})"

    return f"エクスポートしました: {path}"
