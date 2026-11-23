"""
アプリケーション制御層（GemioController）から UI 層への対話確認コールバック定義モジュール。
"""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class UserSearchRequest:
    """AIからユーザーへ外部Web検索協力を依頼するリクエストデータ。"""

    purpose: str
    queries: tuple[str, ...]
    what_to_check: str
    suggested_title: str
    allow_browser_open: bool = True


@dataclass(frozen=True)
class UserSearchReply:
    """専用検索ダイアログ（UserSearchModal）からのユーザー応答データ。"""

    action: Literal["send_once", "save_material", "skip", "abort"]
    content: str = ""


@runtime_checkable
class InteractionHandler(Protocol):
    """
    AI実行中の承認確認・ユーザーへの質問・外部検索協力依頼・連続実行上限確認を UI 側へ委譲するプロトコル。
    Textual UI の ThreadModalBridge や単体テスト用のモッククラスで実装します。
    """

    def request_approval(self, action_description: str) -> str:
        """
        変更系ツールやシェルコマンド実行前の承認確認を行います。

        Returns:
            'y' (許可) / 'n' (拒否) / 'a' (作業中止 Abort) / 'k' (プロセス終了 Kill)
        """
        ...

    def ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """
        ask_user ツールによる AI からの質問・確認を提示し、ユーザーの回答文字列を返します。
        中止が選択された場合は AgentExecutionAborted を送出するか 'a' 相当を処理します。
        """
        ...

    def request_user_search(self, request: UserSearchRequest) -> UserSearchReply:
        """
        request_user_search ツールによる外部検索協力ダイアログを提示し、
        ユーザーの回答（通常応答 / 前提資料登録 / スキップ / 中止）を返します。
        """
        ...

    def confirm_iteration_limit(self) -> tuple[bool, int]:
        """
        連続ツール呼び出し上限到達時に、継続可否と追加ループ回数 `(should_continue, extra_iterations)` を返します。
        """
        ...


__all__ = [
    "InteractionHandler",
    "UserSearchReply",
    "UserSearchRequest",
]
