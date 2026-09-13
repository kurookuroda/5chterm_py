"""非対話CLIコマンド。Crystal版 cmd/search_read_cmd.cr・export_cmd.cr・
(計画のみで未実装だった)export-batch に対応。

対話TUI(ロック取得・履歴更新・Discord転送)は別のフェーズで扱うため、
ここではhistory/queueを一切更新しない使い捨てコマンドのみを実装する。
"""
from __future__ import annotations

import asyncio
import json
import sys

from ..browser import Browser
from ..config import load_config
from ..errors import BrowserError, NetworkFetchError, ThreadGoneError
from ..history import Manager, NullHistory
from ..models import ThreadInfo


def _classify_error_type(ex: Exception) -> str:
    if isinstance(ex, ThreadGoneError):
        return "thread_gone"
    if isinstance(ex, NetworkFetchError):
        return "network"
    return "other"


def _extract_error_url(ex: Exception) -> str | None:
    return ex.url if isinstance(ex, BrowserError) else None


def _write_envelope(env: dict, file=sys.stdout) -> None:
    print(json.dumps(env, ensure_ascii=False, indent=2), file=file)


async def run_search_command(args: list[str]) -> int:
    if not args:
        _write_envelope(
            {"ok": False, "error": "使い方: x5ch search <keyword>", "error_type": "other"},
            file=sys.stderr,
        )
        return 1
    keyword = args[0]

    cfg = load_config()
    browser = Browser(cfg.user_agent, NullHistory(), cfg.cache_expiration)

    try:
        results = await browser.search_global(keyword)
    except Exception as ex:
        _write_envelope(
            {
                "ok": False,
                "error": str(ex),
                "error_type": _classify_error_type(ex),
                "error_url": _extract_error_url(ex),
            },
            file=sys.stderr,
        )
        return 1

    _write_envelope(
        {
            "ok": True,
            "results": [
                {
                    "title": r.title,
                    "count": r.count,
                    "board_url": r.board_url,
                    "dat_file": r.dat_file,
                    "url": r.url,
                }
                for r in results
            ],
        }
    )
    return 0


async def run_read_command(args: list[str]) -> int:
    if len(args) < 2:
        _write_envelope(
            {"ok": False, "error": "使い方: x5ch read <board_url> <dat_file>", "error_type": "other"},
            file=sys.stderr,
        )
        return 1
    board_url, dat_file = args[0], args[1]

    cfg = load_config()
    browser = Browser(cfg.user_agent, NullHistory(), cfg.cache_expiration)

    t = ThreadInfo(dat_file=dat_file, board_url=board_url)
    try:
        posts = await browser.get_thread_data(t)
    except Exception as ex:
        _write_envelope(
            {
                "ok": False,
                "error": str(ex),
                "error_type": _classify_error_type(ex),
                "error_url": _extract_error_url(ex),
            },
            file=sys.stderr,
        )
        return 1

    _write_envelope(
        {
            "ok": True,
            "thread": {
                "title": t.title,
                "board_url": board_url,
                "dat_file": dat_file,
                "count": len(posts),
            },
            "posts": [
                {"num": p.num, "name": p.name, "date": p.date, "message": p.message}
                for p in posts
            ],
        }
    )
    return 0


async def run_export_command(args: list[str]) -> int:
    since_num = 0
    rest: list[str] = []

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--since-num":
            i += 1
            since_num = int(args[i]) if i < len(args) and args[i].lstrip("-").isdigit() else 0
        elif arg.startswith("--since-num="):
            val = arg.split("=", 1)[1]
            since_num = int(val) if val.lstrip("-").isdigit() else 0
        else:
            rest.append(arg)
        i += 1

    if len(rest) < 2:
        print("使い方: x5ch export <board_url> <dat_file> [--since-num N]", file=sys.stderr)
        print("例:     x5ch export https://mao.5ch.io/linux/ 1765829109.dat", file=sys.stderr)
        return 1
    board_url, dat_file = rest[0], rest[1]

    cfg = load_config()
    # export専用の用途では閲覧履歴を一切参照・更新しないため、NullHistoryで構わない。
    browser = Browser(cfg.user_agent, NullHistory(), cfg.cache_expiration)

    try:
        result = await browser.export_thread_data(board_url, dat_file, since_num)
    except ThreadGoneError:
        print("スレッドはdat落ちしています", file=sys.stderr)
        return 1
    except Exception as ex:
        url_part = f" (URL: {ex.url})" if isinstance(ex, BrowserError) and ex.url else ""
        print(f"エラー: {ex}{url_part}", file=sys.stderr)
        return 1

    print(result.to_pretty_json())
    return 0


async def run_export_batch_command(args: list[str]) -> int:
    """`x5ch export-batch [history_file]` — 履歴(history.json)を入力に、
    各スレッドを since_num=履歴のres値 で差分エクスポートし、threads配列でまとめて出力する。
    履歴ファイル省略時は設定のhistory_fileを使う。
    """
    cfg = load_config()
    history_path = args[0] if args else cfg.history_file

    hist = Manager(history_path)
    entries = await hist.all_entries()

    browser = Browser(cfg.user_agent, NullHistory(), cfg.cache_expiration)

    threads: list[dict] = []
    errors: list[dict] = []

    for entry in entries:
        try:
            result = await browser.export_thread_data(
                entry.board_url, entry.dat_file, since_num=entry.res
            )
        except Exception as ex:
            errors.append(
                {
                    "board_url": entry.board_url,
                    "dat_file": entry.dat_file,
                    "error": str(ex),
                    "error_type": _classify_error_type(ex),
                    "error_url": _extract_error_url(ex),
                }
            )
            continue
        threads.append(result.to_dict())

    _write_envelope({"ok": True, "threads": threads, "errors": errors})
    return 0


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from ..tui.app import run as run_tui

        run_tui()
        return

    command, rest = args[0], args[1:]
    handlers = {
        "search": run_search_command,
        "read": run_read_command,
        "export": run_export_command,
        "export-batch": run_export_batch_command,
    }
    handler = handlers.get(command)
    if handler is None:
        print(f"不明なコマンド: {command}", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(handler(rest))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
