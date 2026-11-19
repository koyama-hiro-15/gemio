"""
連続ツール呼び出し上限到達時の継続確認モーダル（IterationLimitModal）モジュール。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class IterationLimitModal(ModalScreen[tuple[bool, int]]):
    """連続ツール呼び出しが上限に達した際、追加ループ回数または中断を確認するモーダル画面。"""

    def __init__(self, default_extra: int = 10) -> None:
        super().__init__()
        self.default_extra = max(1, int(default_extra))

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-dialog-warn"):
            yield Static(
                "連続ツール呼び出しの上限に到達しました",
                markup=False,
                classes="modal-title modal-title-warn",
            )
            yield Static(
                "処理を継続（追加ループを実行）しますか？ 追加ループ回数を指定して Enter を押してください:",
                markup=False,
            )
            yield Input(
                value=str(self.default_extra),
                restrict=r"[0-9]*",
                id="iter-extra-input",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("継続 (Enter)", id="btn-continue", variant="success", compact=True)
                yield Button("中断 (Esc)", id="btn-stop", variant="error", compact=True)

    def on_mount(self) -> None:
        self.query_one("#iter-extra-input", Input).focus()

    def _parse_extra_count(self) -> int:
        raw = self.query_one("#iter-extra-input", Input).value.strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
        return self.default_extra

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss((True, self._parse_extra_count()))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss((False, 0))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-continue":
            self.dismiss((True, self._parse_extra_count()))
        elif event.button.id == "btn-stop":
            self.dismiss((False, 0))


__all__ = [
    "IterationLimitModal",
]
