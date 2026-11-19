"""
Human-in-the-loop 承認確認モーダル（ApprovalModal）モジュール。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ApprovalModal(ModalScreen[str]):
    """
    変更操作やシェルコマンド実行前にユーザーの承認（y / n / a / k）を求めるモーダル画面。
    """

    def __init__(self, action_description: str) -> None:
        super().__init__()
        self.action_description = action_description

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-dialog-warn"):
            yield Static(
                "Human-in-the-loop 承認要求",
                markup=False,
                classes="modal-title modal-title-warn",
            )
            yield Static(
                "Gemio が以下の操作を実行しようとしています:",
                markup=False,
            )
            with VerticalScroll(classes="modal-scroll-body"):
                yield Static(
                    self.action_description,
                    markup=False,
                    id="approval-desc-text",
                )
            yield Static(
                "キー操作: [Y] 許可  |  [N] 拒否  |  [A / Esc] 作業中止(Abort)  |  [K] 即時終了(Kill)",
                markup=False,
                classes="modal-hint",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("許可 (Y)", id="btn-approve", variant="success", compact=True)
                yield Button("拒否 (N)", id="btn-reject", variant="warning", compact=True)
                yield Button("中止 (A)", id="btn-abort", variant="error", compact=True)
                yield Button("終了 (K)", id="btn-kill", variant="error", compact=True)

    def on_mount(self) -> None:
        self.query_one("#btn-reject", Button).focus()

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "y":
            event.stop()
            self.dismiss("y")
        elif key == "n":
            event.stop()
            self.dismiss("n")
        elif key in {"a", "escape"}:
            event.stop()
            self.dismiss("a")
        elif key == "k":
            event.stop()
            self.dismiss("k")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        mapping = {
            "btn-approve": "y",
            "btn-reject": "n",
            "btn-abort": "a",
            "btn-kill": "k",
        }
        if event.button.id in mapping:
            self.dismiss(mapping[event.button.id])


__all__ = [
    "ApprovalModal",
]
