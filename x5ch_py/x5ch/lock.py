"""多重起動防止(ファイルロック)。Crystal版 cmd/lock.cr に対応。

既にロックされている場合(=バックグラウンドの転送プロセスが稼働中)、
PIDファイルからそのプロセスをTERM→(5秒待機)→KILLの順で停止させてから
ロックを取得し直す「ハンドオフ」を行う。asyncioループ開始前に同期的に
呼ぶことを想定している(Crystal版もFiber開始前に同期的に呼んでいる)。
"""
from __future__ import annotations

import fcntl
import os
import signal
import time
from pathlib import Path
from typing import IO


class LockError(Exception):
    pass


def acquire_lock_with_handoff(lock_path: str, pid_path: str) -> IO:
    """ロックファイルのファイルオブジェクトを返す。

    呼び出し元はプロセス終了までこのファイルオブジェクトを保持し続けること
    (クローズ、またはガベージコレクトされるとロックが解放される)。
    """
    try:
        lock_file = open(lock_path, "a+")
    except OSError as ex:
        raise LockError(f"ロックファイルを開けません: {ex}") from ex

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file  # 即座に取得できた(通常ケース)
    except OSError:
        pass

    print("\x1b[33m[Notice] バックグラウンドで転送プロセス(Cron)が稼働中です。\x1b[0m")

    pid = read_pid(pid_path)
    if pid and pid > 0:
        print(f"プロセス(PID: {pid})を停止し、処理を引き継ぎます...", end="", flush=True)

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

        for _ in range(5):
            time.sleep(1)
            if not process_alive(pid):
                break
            print(".", end="", flush=True)

        if process_alive(pid):
            print(" 応答がないため強制終了します(KILL)...", end="", flush=True)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    print(" ロック取得...", end="", flush=True)
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    except OSError as ex:
        raise LockError(f"ロック取得に失敗しました: {ex}") from ex

    print(" 完了。\n\x1b[32m>> 処理を引き継いで起動します。\x1b[0m")
    time.sleep(1)

    return lock_file


def read_pid(pid_path: str) -> int | None:
    p = Path(pid_path)
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None


def process_alive(pid: int) -> bool:
    """シグナル0を送ってプロセスの生存を確認する(実際にはシグナルを送らない)。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write_pid(pid_path: str) -> None:
    Path(pid_path).write_text(str(os.getpid()))


def remove_pid(pid_path: str) -> None:
    try:
        Path(pid_path).unlink()
    except OSError:
        pass
