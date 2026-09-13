# x5ch-py

[5chanterm (x5ch-cr)](https://github.com/kurookuroda/5chanterm) のPythonポート。

**系譜:** Ruby(`kogfx/x5ch`) → Go(`Neko-Kuroi/termchan`) → Crystal(`5chanterm`, x5ch-cr) →
Python(このリポジトリ)。

## 実装方針

- 並行処理: `asyncio`
- HTTP取得: `httpx`(自動リダイレクト・自動gzip展開)
- HTMLパース: `selectolax`(レス単位の構造をDOMとして解析。Crystal版の
  正規表現チャンク分割より壊れたHTMLに頑健)

## 状態(フェーズ1〜5: コア基盤+非対話CLI)

実装済み:

- `models.py` / `errors.py` / `config.py` — 基盤
- `fetch.py` — HTTP取得(`Fetcher`)
- `menu.py` / `threads.py` / `search.py` / `nextthread.py` — 板メニュー・スレ一覧・
  全板検索・次スレ検出
- `parse.py` — 軽量Post抽出(TUI/search/read向け)
- `export.py` — アーカイブ用完全構造(`ExportResult`)・Markdown変換
- `browser.py` — 各モジュールをまとめるファサード(`Browser`)、キャッシュ付き
- `history.py` — 閲覧履歴のJSON永続化(`Manager`)、使い捨て用`NullHistory`
- `cli/main.py` — 非対話CLI: `search` / `read` / `export` / `export-batch`
- `discord.py` — Discord Bot API連携(Webhookではなくトークン+チャンネルID方式。
  スレッド作成・2000字超の自動分割送信・429リトライに対応)
- `transfer.py` — Discord転送Worker(`asyncio.Condition`でキュー監視・
  一時停止/再開/強制終了・ネットワーク/Discord APIエラー時の自動リトライに対応)

未実装(次フェーズ):

- 非対話の`webhook-send <json_file>`サブコマンド(TUI版は実装済み)
- ヘルプ画面(`render.cr`のキー一覧表示相当)

## 対話TUI(フェーズ7・主線のみ)

```bash
python -m x5ch.cli.main   # 引数なし起動でTUIが立ち上がる
```

実装済みの画面遷移: メインメニュー(★最近読んだスレッド + カテゴリ一覧) →
板一覧 → スレ一覧(勢い順、新着`+`/既読`✔`/Discordキュー予約済`📨`表示) →
スレ本文(Pager)。`s`キーで全板検索、`t`キーで転送待機列の管理
(`QueueManageScreen`)、`H`キーで閲覧履歴の管理(`HistoryManageScreen`、
いずれも一覧表示・Enterで削除)。Pager終了時(`b`)にスクロール位置から
既読レス番号を推定して履歴を更新する。アプリ起動時にDiscord転送Worker
(`transfer.Worker`)もバックグラウンドで起動・終了時にkillされる。

スレ一覧画面(通常一覧・検索結果一覧共通)ではハイライト中のスレッドに対して:

- `e` / `E` — Markdown / JSON でファイルにエクスポート(`export_file.py`、
  `$X5CH_EXPORT_DIR`未設定時はカレントディレクトリの`x5ch_exports/`)
- `m` — Discord転送Workerのキューに追加(Bot未設定ならエラー表示)、
  合わせて履歴にも登録し既読状態をリセット
- `H` — そのスレッドの履歴を削除(未読なら「履歴がない」旨を表示するだけ)

`terminal.cr`/`selector.cr`/`pager.cr`(生ターミナル制御・自前セレクタ・
自前ページャー)はTextual標準のScreen/ListView/VerticalScrollに置き換えており、
移植対象から除外している。`lock.cr`の多重起動防止(TERM→5秒待機→KILLの
ハンドオフ)は`lock.py`にそのまま踏襲し、実プロセスでの動作確認済み。

## Discord連携の設定

Webhookではなく **Discord Bot** 方式です。

1. Discord Developer PortalでBotを作成し、トークンを取得
2. 対象サーバーにBotを招待(`Create Public Threads` / `Send Messages` 権限)
3. 転送先チャンネルのIDを控える
4. 環境変数で設定: `X5CH_DISCORD_BOT_TOKEN` / `X5CH_DISCORD_CHANNEL_ID`

## Webhook送信(Bot APIとは別の簡易経路)

`discord.py`(Bot API)とは独立に、Botトークン不要でWebhook URLへ直接投稿する
簡易機能もある。複数URLを指定すると全URLへ同一内容をブロードキャストする。

設定ファイル(`X5CH_WEBHOOK_URLS_FILE`、デフォルト`~/.x5ch_webhooks.json`)に
webhook URLをJSON配列、または1行1URL(`#`始まりはコメント)で記述する:

```json
["https://discord.com/api/webhooks/xxxx/yyyy", "https://discord.com/api/webhooks/zzzz/wwww"]
```

TUIでのキー(いずれも送信内容フォーマットはBot版と同じ「番号:名前:日時+本文」):

- **スレ一覧画面**: `w` — 選択中スレッドの未読分(history基準)を送信
- **Pager画面**: `w` — 表示中スレッドの未読分を送信 / `W` — 現在のスクロール位置の1件のみ送信

非対話の`webhook-send <json_file>`サブコマンド(export/export-batchの出力を
そのまま流し込む版)は未実装。

## CLIコマンド

```bash
# 全板検索(TTS用軽量)
python -m x5ch.cli.main search "キーワード"

# スレッド全レス取得(TTS用軽量)
python -m x5ch.cli.main read <board_url> <dat_file>

# 単一スレッドをアーカイブ用JSONでエクスポート(BBSインポート用)
python -m x5ch.cli.main export <board_url> <dat_file> [--since-num N]

# history.json全体を対象に、各スレッドをres値からの差分でまとめてエクスポート
python -m x5ch.cli.main export-batch [history_file]
# history_file省略時は $X5CH_HISTORY_FILE (デフォルト ~/.x5ch_history.json)
```

`export-batch`の出力形式:

```json
{
  "ok": true,
  "threads": [ { "source": {...}, "thread": {...}, "posts": [...] } ],
  "errors": [ { "board_url": "...", "dat_file": "...", "error": "...", "error_type": "..." } ]
}
```

## 注意

- このサンドボックス環境は5ch/Discord関連ドメインへの通信が許可されていないため、
  実際のレスポンスに対する動作確認は未実施です。合成HTMLによる
  `parse.py`/`export.py`のロジック検証、フェイクdependencyによる
  `transfer.py`(Worker)の一連の流れ(enqueue→スレッド作成→メッセージ送信→
  完了待ち→終了)の検証、`discord.py`のユニットレベル(有効判定・文字数分割・
  429の`retry_after`解析)の検証、`lock.py`の多重起動ハンドオフを実プロセスで
  行う検証、TUI主線(メニュー→板→スレ一覧→検索→Pager→戻る)をTextualの
  `run_test()`(ヘッドレスpilot)でフェイクdependency相手に一通りたどる検証、
  Webhook送信(`w`/`W`キー、複数URLへのブロードキャスト・件数)を実際のHTTP送信
  部分だけモック化してTUI経由で検証、キュー管理画面・履歴管理画面(`t`/`H`キー、
  一覧表示・削除・再採番)、スレ一覧の`e`/`E`(実ファイル書き出しを確認)・`m`
  (キュー追加+履歴登録)・`H`(実際に保存した履歴が正しく削除されること)を
  TUI経由で検証は行っていますが、実データ・実API・実ターミナルでの
  最終確認をお願いします。
- `parse.py`/`export.py`のレス境界検出は `div[class*="clear post"]` を
  前提にしています。実際のHTML構造とズレがあれば調整が必要です。
