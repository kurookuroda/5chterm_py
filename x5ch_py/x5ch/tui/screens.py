"""TUI画面群。Crystal版 cmd/main.cr のメニュー/board/thread/pagerループ(主線)に対応。

selector.cr/pager.cr/terminal.cr が担っていた「生ターミナル制御・自前セレクタ・
自前ページャー」は、ここでは全てTextual標準のScreen/ListView/スクロール
コンテナに置き換えている。ただし selector.cr の核心的な操作体系である
「番号入力→Enterで確定」(IndexedListViewMixin)だけは、Crystal版との操作感の
齟齬を避けるため明示的に踏襲している。Crystal版は0始まりのインデックスを
そのまま表示・入力に使う(`[start_idx + i]`表示、`input_buffer.to_i?`を
そのまま配列添字に使用)ため、ここでも0始まりで揃えている。
Textualの矢印キーによるハイライト移動+Enterとも併用でき、数字未入力なら
ハイライト中の行が対象になる。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from ..errors import BrowserError, ThreadGoneError
from ..export_file import perform_export
from ..models import Board, Category, Post, ThreadInfo
from ..transfer import Task
from ..webhook import broadcast_post, broadcast_posts, load_webhook_urls


class IndexedListViewMixin:
    """`ListView`を持つScreenに「数字入力→Enter/コマンドキーで確定」を付与するmixin。

    サブクラス側の責務:
      - compose()で `Label("", id="index-status")` を配置する(入力中の番号を表示)
      - `_index_list_view_id()` : 対象ListViewのCSSセレクタ("#thread-list"等)
      - `_index_item_count()`   : 現在の行数(範囲チェック用)
      - `_activate(idx)`        : 数字未入力時のEnter(ハイライト選択)、および
        数字入力+Enterの両方で呼ばれる「確定」処理(idxはNoneの場合がある)
      - (任意)`_indexed_command_keys`と`_run_indexed_command(key, idx)`:
        「番号+特定キー」でハイライトによらず指定インデックスに対して
        コマンドを実行したい画面だけオーバーライドする(デフォルトは空集合=
        番号ジャンプ機能のみ)。数字未入力でこれらのキーが押された場合は
        on_keyでは処理せず、通常のBINDING解決(action_*、ハイライト対象)に委ねる。
    """

    _indexed_command_keys: ClassVar[set[str]] = set()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._index_buffer: str = ""

    # --- サブクラスが実装するフック ---

    def _index_list_view_id(self) -> str:
        raise NotImplementedError

    def _index_item_count(self) -> int:
        raise NotImplementedError

    def _activate(self, idx: int | None) -> None:
        raise NotImplementedError

    def _run_indexed_command(self, key: str, idx: int) -> None:
        """番号+コマンドキーの挙動。デフォルトは何もしない(ジャンプ専用画面向け)。"""

    # --- 共通ロジック ---

    def _update_index_status(self) -> None:
        try:
            label = self.query_one("#index-status", Label)
        except Exception:
            return
        label.update(f"> {self._index_buffer}" if self._index_buffer else "")

    def _clear_index_buffer(self) -> None:
        self._index_buffer = ""
        self._update_index_status()

    def _resolved_index(self) -> int | None:
        if not self._index_buffer:
            return None
        try:
            idx = int(self._index_buffer)
        except ValueError:
            return None
        return idx if 0 <= idx < self._index_item_count() else None

    def on_key(self, event) -> None:
        key = event.key

        if len(key) == 1 and key.isdigit():
            self._index_buffer += key
            self._update_index_status()
            event.stop()
            return

        if not self._index_buffer:
            return  # バッファなし: 通常のBINDING解決(矢印ハイライト/Enter等)に委ねる

        if key == "escape":
            # バッファは破棄しつつ、戻る動作自体は通常のBINDING解決に任せる。
            self._clear_index_buffer()
            return

        if key == "backspace":
            self._index_buffer = self._index_buffer[:-1]
            self._update_index_status()
            event.stop()
            return

        if key == "enter":
            idx = self._resolved_index()
            self._clear_index_buffer()
            event.stop()
            if idx is None:
                self.notify("無効なインデックスです", severity="warning")
            else:
                self._activate(idx)
            return

        if key in self._indexed_command_keys:
            idx = self._resolved_index()
            self._clear_index_buffer()
            event.stop()
            if idx is None:
                self.notify("無効なインデックスです", severity="warning")
            else:
                self._run_indexed_command(key, idx)
            return

        # 未対応キー: バッファを破棄し、このキー自体は通常処理(BINDING解決)に委ねる。
        self._clear_index_buffer()


class SearchModal(ModalScreen[str | None]):
    """全板検索のキーワード入力モーダル。Ruby/Crystal版の's'キー相当。"""

    DEFAULT_CSS = """
    SearchModal {
        align: center middle;
    }
    #search-box {
        width: 60;
        height: auto;
        border: heavy $accent;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="search-box"):
            yield Label("検索キーワード (Escでキャンセル)")
            yield Input(placeholder="キーワード", id="keyword-input")

    def on_mount(self) -> None:
        self.query_one("#keyword-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


@dataclass
class _RecentEntry:
    thread: ThreadInfo


@dataclass
class _CategoryEntry:
    category: Category


class MainMenuScreen(IndexedListViewMixin, Screen):
    """メインメニュー: ★最近読んだスレッド + カテゴリ一覧。"""

    BINDINGS = [
        ("s", "search", "検索"),
        ("t", "queue_manage", "キュー管理"),
        ("H", "history_manage", "履歴管理"),
        ("r", "reload", "再読込"),
        ("q", "quit", "終了"),
    ]

    def _index_list_view_id(self) -> str:
        return "#menu-list"

    def _index_item_count(self) -> int:
        return len(self._entries)

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="menu-list")
        yield Label("", id="index-status")
        yield Footer()

    async def on_mount(self) -> None:
        await self.load()

    async def load(self) -> None:
        list_view = self.query_one("#menu-list", ListView)
        await list_view.clear()
        self._entries: list[_RecentEntry | _CategoryEntry] = []

        list_view.append(ListItem(Label("読み込み中...")))

        recent = await self.app.history.get_recent_threads()
        categories = await self.app.browser.get_menu()

        await list_view.clear()

        width = len(str(len(recent[:15]) + len(categories) - 1)) if (recent or categories) else 1
        width = max(width, 1)

        for rt in recent[:15]:
            i = len(self._entries)
            self._entries.append(_RecentEntry(thread=rt.thread_info))
            list_view.append(
                ListItem(Label(f"{i:>{width}} ★ {rt.thread_info.title}"), classes="recent")
            )

        for cat in categories:
            i = len(self._entries)
            self._entries.append(_CategoryEntry(category=cat))
            list_view.append(ListItem(Label(f"{i:>{width}} {cat.title}")))

        if self._entries:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._activate(event.list_view.index)

    def _activate(self, idx: int | None) -> None:
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if isinstance(entry, _RecentEntry):
            self.app.push_screen(ThreadPagerScreen(entry.thread))
        else:
            self.app.push_screen(BoardListScreen(entry.category))

    async def action_reload(self) -> None:
        await self.app.browser.invalidate_menu_cache()
        await self.load()

    def action_search(self) -> None:
        def on_result(keyword: str | None) -> None:
            if keyword:
                self.run_worker(self._do_search(keyword), exclusive=True)

        self.app.push_screen(SearchModal(), on_result)

    async def _do_search(self, keyword: str) -> None:
        try:
            results = await self.app.browser.search_global(keyword)
        except Exception as ex:
            self.notify(f"検索エラー: {ex}", severity="error")
            return
        if not results:
            self.notify("該当なし", severity="warning")
            return
        self.app.push_screen(ThreadListScreen(threads=results, title=f"検索: {keyword}"))

    def action_queue_manage(self) -> None:
        self.app.push_screen(QueueManageScreen())

    def action_history_manage(self) -> None:
        self.app.push_screen(HistoryManageScreen())

    def action_quit(self) -> None:
        self.app.exit()


class BoardListScreen(IndexedListViewMixin, Screen):
    """カテゴリ内の板一覧。"""

    BINDINGS = [("b", "back", "戻る"), ("escape", "back", "戻る")]

    def __init__(self, category: Category):
        super().__init__()
        self._category = category

    def _index_list_view_id(self) -> str:
        return "#board-list"

    def _index_item_count(self) -> int:
        return len(self._category.boards)

    def compose(self) -> ComposeResult:
        yield Header()
        yield ListView(id="board-list")
        yield Label("", id="index-status")
        yield Footer()

    async def on_mount(self) -> None:
        list_view = self.query_one("#board-list", ListView)
        width = max(len(str(max(len(self._category.boards) - 1, 0))), 1)
        for i, board in enumerate(self._category.boards):
            has_history = await self.app.history.has_history_in_board(board.url)
            mark = "✔ " if has_history else "  "
            list_view.append(ListItem(Label(f"{i:>{width}} {mark}{board.title}")))
        if self._category.boards:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._activate(event.list_view.index)

    def _activate(self, idx: int | None) -> None:
        if idx is None or idx >= len(self._category.boards):
            return
        board = self._category.boards[idx]
        self.app.push_screen(ThreadListScreen(board=board))

    def action_back(self) -> None:
        self.app.pop_screen()


class ThreadListScreen(IndexedListViewMixin, Screen):
    """板内スレ一覧、または検索結果一覧。

    番号入力+Enterでのジャンプ(Pagerを開く)に加え、番号入力+w/e/E/m/Hで
    「ハイライト行ではなく指定インデックスのスレッド」に対してコマンドを
    実行できる。番号未入力時はハイライト行が対象になる。
    """

    _indexed_command_keys: ClassVar[set[str]] = {"w", "e", "E", "m", "H"}

    BINDINGS = [
        ("b", "back", "戻る"),
        ("escape", "back", "戻る"),
        ("r", "reload", "再読込"),
        ("w", "webhook_send", "未読をWebhook送信"),
        ("e", "export_markdown", "MD Export"),
        ("E", "export_json", "JSON Export"),
        ("m", "enqueue", "Discord送信予約"),
        ("H", "delete_history", "履歴削除"),
    ]

    def __init__(
        self,
        board: Board | None = None,
        threads: list[ThreadInfo] | None = None,
        title: str = "",
    ):
        super().__init__()
        self._board = board
        self._threads: list[ThreadInfo] = threads or []
        self._title = title or (board.title if board else "スレッド一覧")
        self._queued: set[tuple[str, str]] = set()

    def _index_list_view_id(self) -> str:
        return "#thread-list"

    def _index_item_count(self) -> int:
        return len(self._threads)

    def _run_indexed_command(self, key: str, idx: int) -> None:
        t = self._threads[idx]
        if key == "w":
            self.run_worker(self._do_webhook_send(t), exclusive=True)
        elif key == "e":
            self._do_export(t, as_markdown=True)
        elif key == "E":
            self._do_export(t, as_markdown=False)
        elif key == "m":
            self._do_enqueue(t)
        elif key == "H":
            self._do_delete_history(t)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(self._title, id="thread-list-title")
        yield ListView(id="thread-list")
        yield Label("", id="index-status")
        yield Footer()

    async def on_mount(self) -> None:
        if self._board is not None and not self._threads:
            await self._load(force_reload=False)
        else:
            await self._refresh_list()

    async def _load(self, force_reload: bool) -> None:
        list_view = self.query_one("#thread-list", ListView)
        await list_view.clear()
        list_view.append(ListItem(Label("読み込み中...")))
        try:
            self._threads = await self.app.browser.get_threads(
                self._board, force_reload=force_reload
            )
        except Exception as ex:
            await list_view.clear()
            list_view.append(ListItem(Label(f"取得エラー: {ex}")))
            return
        await self._refresh_list()

    async def _refresh_list(self) -> None:
        list_view = self.query_one("#thread-list", ListView)
        await list_view.clear()
        width = max(len(str(max(len(self._threads) - 1, 0))), 1)
        for i, t in enumerate(self._threads):
            mark = "+" if t.has_new else ("✔" if t.last_read > 0 else " ")
            queued_mark = "📨" if (t.board_url, t.dat_file) in self._queued else " "
            info = f"({t.count}/{int(t.ikioi)})" if t.ikioi > 0 else f"({t.count})"
            list_view.append(
                ListItem(Label(f"{i:>{width}} {mark}{queued_mark} {info} {t.title}"))
            )
        if self._threads:
            list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._activate(event.list_view.index)

    def _activate(self, idx: int | None) -> None:
        if idx is None or idx >= len(self._threads):
            return
        self.app.push_screen(ThreadPagerScreen(self._threads[idx]))

    async def action_reload(self) -> None:
        if self._board is not None:
            await self._load(force_reload=True)

    def _highlighted_thread(self) -> ThreadInfo | None:
        list_view = self.query_one("#thread-list", ListView)
        idx = list_view.index
        if idx is None or idx >= len(self._threads):
            return None
        return self._threads[idx]

    def action_webhook_send(self) -> None:
        self.run_worker(self._do_webhook_send(self._highlighted_thread()), exclusive=True)

    async def _do_webhook_send(self, t: ThreadInfo | None) -> None:
        if t is None:
            return

        urls = load_webhook_urls(self.app.config.webhook_urls_file)
        if not urls:
            self.notify("webhook URLが設定されていません", severity="warning")
            return

        try:
            posts = await self.app.browser.get_thread_data(t)
        except Exception as ex:
            self.notify(f"取得エラー: {ex}", severity="error")
            return

        last_read = await self.app.history.get_last_read(t.board_url, t.dat_file)
        new_posts = [p for p in posts if p.num > last_read]
        if not new_posts:
            self.notify("新着なし")
            return

        self.notify(f"送信中... ({len(new_posts)}件)")
        failures = await broadcast_posts(urls, new_posts)
        total_failed = sum(len(v) for v in failures.values())
        if total_failed:
            self.notify(f"一部失敗しました({total_failed}件)", severity="warning")
        else:
            self.notify(f"{len(new_posts)}件送信しました")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_export_markdown(self) -> None:
        self._do_export(self._highlighted_thread(), as_markdown=True)

    def action_export_json(self) -> None:
        self._do_export(self._highlighted_thread(), as_markdown=False)

    def _do_export(self, t: ThreadInfo | None, as_markdown: bool) -> None:
        if t is None:
            return
        self.run_worker(self._export_worker(t, as_markdown), exclusive=True)

    async def _export_worker(self, t: ThreadInfo, as_markdown: bool) -> None:
        self.notify("エクスポート中...")
        message = await perform_export(self.app.browser, t.board_url, t.dat_file, as_markdown)
        severity = "error" if message.startswith("エクスポート失敗") else "information"
        self.notify(message, severity=severity)

    def action_enqueue(self) -> None:
        self._do_enqueue(self._highlighted_thread())

    def _do_enqueue(self, t: ThreadInfo | None) -> None:
        if t is None:
            return
        if not self.app.discord.enabled():
            self.notify("Discordトークン/チャンネルIDが未設定です", severity="error")
            return
        self.run_worker(self._enqueue_worker(t), exclusive=True)

    async def _enqueue_worker(self, t: ThreadInfo) -> None:
        await self.app.worker.enqueue(
            Task(title=t.title, board_url=t.board_url, dat_file=t.dat_file)
        )
        await self.app.history.add_new_thread(t.title, t.board_url, t.dat_file)
        t.last_read = 0
        self._queued.add((t.board_url, t.dat_file))
        self.notify(f"キューに追加: {t.title}")
        await self._refresh_list()

    def action_delete_history(self) -> None:
        self._do_delete_history(self._highlighted_thread())

    def _do_delete_history(self, t: ThreadInfo | None) -> None:
        if t is None:
            return
        self.run_worker(self._delete_history_worker(t), exclusive=True)

    async def _delete_history_worker(self, t: ThreadInfo) -> None:
        if t.last_read <= 0:
            self.notify("履歴がない(未読の)スレッドです")
            return
        if await self.app.history.delete_thread(t.board_url, t.dat_file):
            t.last_read = 0
            self.notify(f"履歴を削除しました: {t.title}")
            await self._refresh_list()
        else:
            self.notify("削除に失敗しました", severity="error")


class ThreadPagerScreen(Screen):
    """スレッド本文表示。閲覧位置(res)を追跡し、離脱時に履歴を更新する。

    Crystal版 pager.cr の「現在ビューポート下端が指すレス番号」追跡を、
    Textualのスクロールイベントで簡易的に再現している。
    ページャーは単一スレッドの本文表示であり、選択対象となる「複数項目のリスト」を
    持たないため、IndexedListViewMixin(番号入力)は適用しない。
    """

    BINDINGS = [
        ("b", "back", "戻る"),
        ("escape", "back", "戻る"),
        ("w", "webhook_unread", "未読をWebhook送信"),
        ("W", "webhook_current", "現在位置をWebhook送信"),
    ]

    def __init__(self, thread: ThreadInfo):
        super().__init__()
        self._thread = thread
        self._post_nums: list[tuple[Static, int]] = []
        self._all_posts: list[Post] = []
        self._current_res = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="pager-body")
        yield Footer()

    async def on_mount(self) -> None:
        body = self.query_one("#pager-body", VerticalScroll)
        await body.mount(Static("スレッド取得中...", id="pager-loading"))

        saved = await self.app.history.get_last_read(
            self._thread.board_url, self._thread.dat_file
        )
        if saved > 0:
            self._thread.last_read = saved

        try:
            posts = await self.app.browser.get_thread_data(self._thread)
        except ThreadGoneError:
            await body.remove_children()
            await body.mount(Static("[red]スレッドはdat落ちしています[/red]"))
            return
        except BrowserError as ex:
            await body.remove_children()
            url_part = f"\nURL: {ex.url}" if ex.url else ""
            await body.mount(Static(f"[red]通信エラー: {ex}{url_part}[/red]"))
            return

        await body.remove_children()

        if not posts:
            await body.mount(Static("取得はできましたが、レスを1件も抽出できませんでした"))
            return

        self._thread.count = len(posts)
        self._all_posts = posts

        await body.mount(
            Static(
                f"[bold]{self._thread.title}[/bold]\n{self._thread.board_url}",
                id="pager-header",
            )
        )

        marker_inserted = False
        for p in posts:
            if not marker_inserted and p.num > self._thread.last_read:
                await body.mount(Static("[yellow]── 未読ここから ──[/yellow]"))
                marker_inserted = True

            widget = Static(f"[cyan]{p.num}[/cyan] {p.name} {p.date}\n{p.message}\n")
            await body.mount(widget)
            self._post_nums.append((widget, p.num))

        if not marker_inserted:
            await body.mount(Static("[yellow]── 未読ここから ──[/yellow]"))
            await body.mount(Static("(新着なし - 最終レスまで既読です)"))

    def on_scroll(self, event) -> None:
        self._update_current_res()

    def _update_current_res(self) -> None:
        body = self.query_one("#pager-body", VerticalScroll)
        visible_bottom = body.scroll_y + body.size.height
        best = self._current_res
        for widget, num in self._post_nums:
            if widget.region.y <= visible_bottom:
                best = max(best, num)
        self._current_res = best

    async def action_back(self) -> None:
        self._update_current_res()
        if self._current_res > 0:
            await self.app.history.update_history(self._thread, self._current_res)
            self.notify(f"履歴を更新しました: {self._current_res}")
        self.app.pop_screen()

    def action_webhook_unread(self) -> None:
        self.run_worker(self._do_webhook_unread(), exclusive=True)

    async def _do_webhook_unread(self) -> None:
        urls = load_webhook_urls(self.app.config.webhook_urls_file)
        if not urls:
            self.notify("webhook URLが設定されていません", severity="warning")
            return

        new_posts = [p for p in self._all_posts if p.num > self._thread.last_read]
        if not new_posts:
            self.notify("新着なし")
            return

        self.notify(f"送信中... ({len(new_posts)}件)")
        failures = await broadcast_posts(urls, new_posts)
        total_failed = sum(len(v) for v in failures.values())
        if total_failed:
            self.notify(f"一部失敗しました({total_failed}件)", severity="warning")
        else:
            self.notify(f"{len(new_posts)}件送信しました")

    def action_webhook_current(self) -> None:
        self.run_worker(self._do_webhook_current(), exclusive=True)

    async def _do_webhook_current(self) -> None:
        self._update_current_res()
        if self._current_res <= 0:
            self.notify("送信対象がありません", severity="warning")
            return

        post = next((p for p in self._all_posts if p.num == self._current_res), None)
        if post is None:
            self.notify("送信対象が見つかりません", severity="warning")
            return

        urls = load_webhook_urls(self.app.config.webhook_urls_file)
        if not urls:
            self.notify("webhook URLが設定されていません", severity="warning")
            return

        self.notify("送信中...")
        failed = await broadcast_post(urls, post)
        if failed:
            self.notify(f"一部失敗しました({len(failed)}件)", severity="warning")
        else:
            self.notify(f"送信しました: {post.num}")


class QueueManageScreen(IndexedListViewMixin, Screen):
    """転送待機列の一覧・削除。Crystal版 menus.cr manage_queue に対応。"""

    BINDINGS = [("b", "back", "戻る"), ("escape", "back", "戻る")]

    def __init__(self) -> None:
        super().__init__()
        self._tasks: list = []

    def _index_list_view_id(self) -> str:
        return "#queue-list"

    def _index_item_count(self) -> int:
        return len(self._tasks)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(
            "転送待機列の管理 (番号+Enter、またはハイライトしてEnterで削除 / b:戻る)",
            id="queue-title",
        )
        yield ListView(id="queue-list")
        yield Label("", id="index-status")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        list_view = self.query_one("#queue-list", ListView)
        await list_view.clear()

        self._tasks = await self.app.worker.queue_list()

        if not self._tasks:
            list_view.append(ListItem(Label("(待機中のタスクはありません)")))
            return

        width = max(len(str(max(len(self._tasks) - 1, 0))), 1)
        for i, task in enumerate(self._tasks):
            list_view.append(ListItem(Label(f"{i:>{width}} {task.title}")))
        list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._activate(event.list_view.index)

    def _activate(self, idx: int | None) -> None:
        if idx is None or idx >= len(self._tasks):
            return
        self.run_worker(self._do_delete(idx), exclusive=True)

    async def _do_delete(self, idx: int) -> None:
        deleted = await self.app.worker.delete_at(idx)
        if deleted:
            self.notify(f"削除しました: {deleted.title}")
        await self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()


class HistoryManageScreen(IndexedListViewMixin, Screen):
    """閲覧履歴の一覧・削除。Crystal版 menus.cr manage_history に対応。"""

    BINDINGS = [("b", "back", "戻る"), ("escape", "back", "戻る")]

    def __init__(self) -> None:
        super().__init__()
        self._items: list = []

    def _index_list_view_id(self) -> str:
        return "#history-list"

    def _index_item_count(self) -> int:
        return len(self._items)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(
            "閲覧履歴の管理 (番号+Enter、またはハイライトしてEnterで削除 / b:戻る)",
            id="history-title",
        )
        yield ListView(id="history-list")
        yield Label("", id="index-status")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        list_view = self.query_one("#history-list", ListView)
        await list_view.clear()

        self._items = await self.app.history.get_recent_threads()

        if not self._items:
            list_view.append(ListItem(Label("(履歴はありません)")))
            return

        width = max(len(str(max(len(self._items) - 1, 0))), 1)
        for i, item in enumerate(self._items):
            t = item.thread_info
            list_view.append(
                ListItem(Label(f"{i:>{width}} {t.title} (Read: {t.last_read})"))
            )
        list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._activate(event.list_view.index)

    def _activate(self, idx: int | None) -> None:
        if idx is None or idx >= len(self._items):
            return
        self.run_worker(self._do_delete(idx), exclusive=True)

    async def _do_delete(self, idx: int) -> None:
        target = self._items[idx].thread_info
        if await self.app.history.delete_thread(target.board_url, target.dat_file):
            self.notify(f"履歴を削除しました: {target.title}")
        else:
            self.notify("削除に失敗しました", severity="error")
        await self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()
