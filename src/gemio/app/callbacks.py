"""
アプリケーション制御層（GemioController）から UI 層への対話確認コールバック定義モジュール。
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class InteractionHandler(Protocol):
    """
    AI実行中の承認確認・ユーザーへの質問・連続実行上限確認を UI 側へ委譲するプロトコル。
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

    def confirm_iteration_limit(self) -> tuple[bool, int]:
        """
        連続ツール呼び出し上限到達時に、継続可否と追加ループ回数 `(should_continue, extra_iterations)` を返します。
        """
        ...


__all__ = [
    "InteractionHandler",
]
