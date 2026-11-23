"""
Gemio Textual UI 内部で使用するカスタム Message 定義モジュール。
"""

from typing import Literal

from textual.message import Message

HeaderBadgeTarget = Literal["sandbox", "approval", "web_search", "model", "tokens"]


class PromptSubmitted(Message):
    """PromptComposer からテキスト（通常プロンプトまたはスラッシュコマンド）が送信された際のメッセージ。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class HeaderBadgeClicked(Message):
    """StatusHeader 上の各ステータスバッジがクリックされた際のメッセージ。"""

    def __init__(self, target: HeaderBadgeTarget) -> None:
        super().__init__()
        self.target: HeaderBadgeTarget = target


class CopyTextRequested(Message):
    """タイムライン内のカード等からテキストのクリップボードコピーが要求された際のメッセージ。"""

    def __init__(self, text: str, label: str = "テキスト") -> None:
        super().__init__()
        self.text = text
        self.label = label


class CancelTurnRequested(Message):
    """ActivityBar 等から現在実行中のAIターンの中断が要求された際のメッセージ。"""


__all__ = [
    "CancelTurnRequested",
    "CopyTextRequested",
    "HeaderBadgeClicked",
    "HeaderBadgeTarget",
    "PromptSubmitted",
]
