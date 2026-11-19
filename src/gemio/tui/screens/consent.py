"""
トラストモード有効化前の注意事項表示・同意確認モーダル（TrustConsentModal）モジュール。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class TrustConsentModal(ModalScreen[bool]):
    """トラストモード（自動承認）を有効化する前にユーザーの明示同意を求めるモーダル画面。"""

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-dialog-danger"):
            yield Static(
                "トラストモード（Trust Mode）の許可確認",
                markup=False,
                classes="modal-title modal-title-danger",
            )
            yield Static(
                "トラストモードは、信頼できるプロジェクト（自作コードや安全確認済みのリポジトリ）で\n"
                "AIに自由かつ高速に作業させるためのモードです。\n\n"
                "有効にすると、致命的なセキュリティ違反（プロジェクト外アクセスや機密ファイル操作等）を除き、\n"
                "ファイルの書き込み・削除・移動やサンドボックス内コマンド実行がすべて事前確認なしで自動承認されます。\n\n"
                "※ 未知の外部リポジトリが含まれる場合は、通常承認モードまたはエディットモードをご利用ください。\n"
                "※ Git 等でいつでも変更を元に戻せる状態で実行することを強く推奨します。",
                markup=False,
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("キャンセル (Esc)", id="btn-cancel", variant="default", compact=True)
                yield Button("同意して有効化", id="btn-consent", variant="error", compact=True)

    def on_mount(self) -> None:
        # 誤操作防止のため初期フォーカスは必ずキャンセル側に置く
        self.query_one("#btn-cancel", Button).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "btn-consent")


__all__ = [
    "TrustConsentModal",
]
