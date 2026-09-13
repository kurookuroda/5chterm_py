"""非対話CLIコマンド。Crystal版 cmd/search_read_cmd.cr・export_cmd.cr・
(計画のみで未実装だった)export-batch に対応。

対話TUI(ロック取得・履歴更新・Discord転送)は別のフェーズで扱うため、
ここではhistory/queueを一切更新しない使い捨てコマンドのみを実装する。
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ..browser import Browser
from ..config import load_config
from ..errors import BrowserError, NetworkFetchError, ThreadGoneError
from ..history import Manager, NullHistory
from ..models import Post, ThreadInfo
from ..webhook import broadcast_posts, load_webhook_urls


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


@dataclass
class _BatchTarget:
    board_url: str
    dat_file: str
    since_num: int = 0


async def _load_targets_from_history(history_file: str, incremental: bool) -> list[_BatchTarget]:
    hist = Manager(history_file)
    entries = await hist.all_entries()
    return [
        _BatchTarget(
            board_url=e.board_url,
            dat_file=e.dat_file,
            since_num=e.res if incremental else 0,
        )
        for e in entries
    ]


def _load_targets_from_queue(queue_file: str) -> list[_BatchTarget]:
    path = Path(queue_file)
    if not path.exists():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        print(f"キューファイル読み込みエラー: {ex}", file=sys.stderr)
        sys.exit(1)

    targets: list[_BatchTarget] = []
    for item in raw:
        board_url = item.get("board_url")
        dat_file = item.get("dat_file")
        if not board_url or not dat_file:
            continue
        # export-batchの errors 配列をそのまま --input に渡すリトライ運用のため、
        # since_num が含まれていればそれを差分取得に使う(通常のqueue.jsonには無いので0扱い)。
        since_num = item.get("since_num") or 0
        targets.append(_BatchTarget(board_url=board_url, dat_file=dat_file, since_num=since_num))
    return targets


async def run_export_batch_command(args: list[str]) -> int:
    """`x5ch export-batch --source history|queue [--incremental] [--input <file>]`

    --source history (デフォルト): 履歴(history.json)全体を対象にした網羅的・
      継続的アーカイブ(cron想定)。--incrementalを付けた場合のみ、各スレッドの
      履歴res値をsince_numとして差分取得する(未指定時は常にフル取得)。
    --source queue: queue.json(またはdefault --input で指定した任意ファイル)を
      対象にした選択的エクスポート。常にフル取得。

    出力の errors 配列は、失敗したスレッドをそのままリトライ用の
    --source queue --input <このファイル> の入力として再利用できる形
    (board_url/dat_file/since_num)を保持している。
    """
    source = "history"
    incremental = False
    input_path: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--source":
            i += 1
            if i < len(args):
                source = args[i]
        elif arg.startswith("--source="):
            source = arg.split("=", 1)[1]
        elif arg == "--incremental":
            incremental = True
        elif arg == "--input":
            i += 1
            if i < len(args):
                input_path = args[i]
        elif arg.startswith("--input="):
            input_path = arg.split("=", 1)[1]
        i += 1

    cfg = load_config()

    if source == "history":
        targets = await _load_targets_from_history(input_path or cfg.history_file, incremental)
    elif source == "queue":
        targets = _load_targets_from_queue(input_path or cfg.queue_file)
    else:
        print(f"不明な--source: {source} (historyまたはqueueを指定)", file=sys.stderr)
        return 1

    if not targets:
        print("対象のスレッドがありません", file=sys.stderr)
        return 1

    browser = Browser(cfg.user_agent, NullHistory(), cfg.cache_expiration)

    threads: list[dict] = []
    errors: list[dict] = []
    total = len(targets)

    for idx, t in enumerate(targets, start=1):
        print(f"[{idx}/{total}] 取得中: {t.board_url} {t.dat_file}", file=sys.stderr)
        try:
            result = await browser.export_thread_data(
                t.board_url, t.dat_file, since_num=t.since_num
            )
        except Exception as ex:
            errors.append(
                {
                    "board_url": t.board_url,
                    "dat_file": t.dat_file,
                    "since_num": t.since_num,
                    "error": str(ex),
                    "error_type": _classify_error_type(ex),
                    "error_url": _extract_error_url(ex),
                }
            )
            continue
        threads.append(result.to_dict())

    _write_envelope({"ok": True, "threads": threads, "errors": errors})
    return 0


async def run_webhook_send_command(args: list[str]) -> int:
    """`x5ch webhook-send <json_file|-> [--webhook-url URL ...]`

    `export`/`export-batch`が出力したJSON(単一ExportResult、または
    {ok,threads,errors}形式のどちらも可)を読み込み、各スレッドのposts配列を
    1件ずつ、指定した(または設定ファイルの)全webhook URLへブロードキャストする。
    """
    urls_override: list[str] = []
    positional: list[str] = []

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--webhook-url":
            i += 1
            if i < len(args):
                urls_override.append(args[i])
        elif arg.startswith("--webhook-url="):
            urls_override.append(arg.split("=", 1)[1])
        else:
            positional.append(arg)
        i += 1

    if not positional:
        print("使い方: x5ch webhook-send <json_file|-> [--webhook-url URL ...]", file=sys.stderr)
        print("例:     x5ch export ... | x5ch webhook-send -", file=sys.stderr)
        return 1

    json_path = positional[0]

    if json_path == "-":
        raw = sys.stdin.read()
    else:
        try:
            raw = Path(json_path).read_text(encoding="utf-8")
        except OSError as ex:
            print(f"ファイル読み込みエラー: {ex}", file=sys.stderr)
            return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        print(f"JSON解析エラー: {ex}", file=sys.stderr)
        return 1

    cfg = load_config()
    urls = urls_override or load_webhook_urls(cfg.webhook_urls_file)
    if not urls:
        print("webhook URLが指定/設定されていません", file=sys.stderr)
        print(
            f"--webhook-url で指定するか、{cfg.webhook_urls_file} を用意してください",
            file=sys.stderr,
        )
        return 1

    if isinstance(data, dict) and "threads" in data:
        thread_entries = data.get("threads") or []
    elif isinstance(data, dict) and "posts" in data:
        thread_entries = [data]
    else:
        print(
            "未対応のJSON形式です(export/export-batchが出力したJSONを指定してください)",
            file=sys.stderr,
        )
        return 1

    total_sent = 0
    all_failures: dict[str, list[str]] = {}

    for entry in thread_entries:
        posts_raw = entry.get("posts") or []
        if not posts_raw:
            continue

        thread_title = (entry.get("thread") or {}).get("title", "")
        posts = [
            Post(
                num=p.get("num", 0),
                name=p.get("author_name_display") or "名無し",
                date=p.get("posted_at") or p.get("posted_at_raw") or "",
                message=p.get("body_display") or p.get("body_raw") or "",
            )
            for p in posts_raw
        ]

        failures = await broadcast_posts(urls, posts)
        total_sent += len(posts)
        for url, failed_nums in failures.items():
            if failed_nums:
                all_failures.setdefault(url, []).extend(
                    f"{thread_title}#{n}" for n in failed_nums
                )

    _write_envelope(
        {
            "ok": not all_failures,
            "sent_threads": len(thread_entries),
            "sent_posts": total_sent,
            "failures": all_failures,
        }
    )
    return 0 if not all_failures else 1


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
        "webhook-send": run_webhook_send_command,
    }
    handler = handlers.get(command)
    if handler is None:
        print(f"不明なコマンド: {command}", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(handler(rest))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
