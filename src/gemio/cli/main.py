"""
Gemio CLI 起動ブートストラップモジュール。

引数解析、起動前プリフライト検証（TTY確認・ディレクトリ検証・サンドボックス安全性検証）、
および Textual TUI アプリケーション（GemioApp）の起動・終了コード管理を行います。
"""

import argparse
import sys
from typing import Any

from gemio.app import AppConfig, AppStartupError, GemioController
from gemio.core.models import DEFAULT_AI_MODEL_NAME
from gemio.project import SandboxSecurityError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析します。"""
    parser = argparse.ArgumentParser(description="Gemio (Textual TUI)")
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="対象とするプロジェクトディレクトリのパス（省略時はカレントディレクトリ）",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=DEFAULT_AI_MODEL_NAME,
        help=f"使用するAIモデル名またはエイリアス（デフォルト: {DEFAULT_AI_MODEL_NAME}）",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="開発者モード（Dev Container / Dockerサンドボックス内でのシェルコマンド実行機能）を有効化します",
    )
    parser.add_argument(
        "--container",
        default=None,
        help="開発者モードをホストOSから起動する際、接続先のDockerコンテナ名またはIDを指定します",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="エディットモード（ファイル作成・編集・移動・コピーは自動承認、削除とコマンド実行は都度確認）で起動します",
    )
    parser.add_argument(
        "--trust",
        "--auto",
        "--autonomous",
        dest="trust",
        action="store_true",
        help="トラストモード（信頼済みプロジェクトで事前同意の上、変更操作を自動承認）で起動します",
    )
    parser.add_argument(
        "--dynamic-tools",
        action="store_true",
        help="[Experimental] 動的ツールセット機能（AIからのツール要求によるオンデマンド有効化）を使用します",
    )
    parser.add_argument(
        "--no-web-search",
        action="store_true",
        help="AIによるWeb検索ツール（+WEB）を初期状態で無効化（-WEB）して起動します",
    )
    return parser.parse_args(argv)


def check_tty_available(
    stdin: Any = None,
    stdout: Any = None,
) -> None:
    """
    標準入出力が対話型ターミナル（TTY）に接続されていることを検証します。
    非TTY環境（パイプ経由や `-it` なしの docker exec 等）では AppStartupError を送出します。
    """
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    in_tty = bool(getattr(in_stream, "isatty", lambda: False)())
    out_tty = bool(getattr(out_stream, "isatty", lambda: False)())

    if not (in_tty and out_tty):
        raise AppStartupError(
            "エラー: Gemio TUI の起動には対話型ターミナル（TTY）が必要です。\n"
            "（Docker コンテナ内で起動する場合は `docker exec -it <container> ...` を指定してください）"
        )


def create_controller_from_args(args: argparse.Namespace) -> GemioController:
    """解析済み引数から AppConfig を構築し、GemioController を初期化します。"""
    config = AppConfig.from_namespace(args)
    return GemioController(config)


def run_cli(argv: list[str] | None = None) -> int:
    """
    プリフライト検証を経て GemioApp を起動し、終了コード（正常時 0、異常時 1 以上）を返します。
    """
    args = parse_args(argv)

    try:
        check_tty_available()
        controller = create_controller_from_args(args)
    except AppStartupError as err:
        print(str(err), file=sys.stderr)
        return 1
    except SandboxSecurityError as sec_err:
        print(
            f"エラー: 開発者モード（--dev）の起動が拒否されました:\n{sec_err}",
            file=sys.stderr,
        )
        return 1
    except ValueError as val_err:
        msg = str(val_err)
        if "key" in msg.lower() or "api" in msg.lower():
            print(
                "エラー: Gemini APIキーが設定されていません。\n"
                "ホストOSの環境変数 `GEMINI_API_KEY` にAPIキーを設定してから起動してください。\n"
                f"（詳細: {msg}）",
                file=sys.stderr,
            )
        else:
            print(f"エラー: 初期化に失敗しました: {msg}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n起動をキャンセルしました。", file=sys.stderr)
        return 130

    from gemio.tui import GemioApp

    app = GemioApp(controller)
    try:
        result = app.run()
        if app.return_code is not None and app.return_code != 0:
            return int(app.return_code)
        if isinstance(result, int):
            return result
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        controller.close()


def main(argv: list[str] | None = None) -> None:
    """Gemio CLI エントリポイント。"""
    exit_code = run_cli(argv)
    if exit_code != 0:
        sys.exit(exit_code)


__all__ = [
    "check_tty_available",
    "create_controller_from_args",
    "main",
    "parse_args",
    "run_cli",
]


if __name__ == "__main__":
    main()
