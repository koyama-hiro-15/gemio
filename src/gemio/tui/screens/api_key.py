"""
コンテナ内起動モード（in_container）向けのセキュア API キー入力モーダル（ApiKeyModal）モジュール。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class ApiKeyModal(ModalScreen[str | None]):
    """
    コンテナ内での環境変数経由のキー露出を防ぐため、起動時にマスク入力で API キーを受け取るモーダル画面。
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(
                "コンテナ内起動: GEMINI_API_KEY の安全入力",
                markup=False,
                classes="modal-title",
            )
            with VerticalScroll(classes="modal-scroll-body"):
                yield Static(
                    "コンテナ内でのAPIキー漏洩（環境変数や /proc/*/environ 読み取り）を防ぐため、起動時の直接入力が必要です。\n"
                    "ホストOSの別ターミナルで以下のコマンドを実行してコピーし、下の欄に貼り付けてください:\n\n"
                    "  • Windows (PowerShell) : Set-Clipboard $env:GEMINI_API_KEY\n"
                    "  • Windows (CMD)        : echo | set /p=\"%GEMINI_API_KEY%\" | clip\n"
                    "  • macOS                : printf \"%s\" \"$GEMINI_API_KEY\" | pbcopy\n"
                    "  • Linux / WSL          : echo $GEMINI_API_KEY\n\n"
                    "※ 入力文字はすべてマスク表示され、環境変数には保存されません。",
                    markup=False,
                )
            yield Input(
                password=True,
                placeholder="GEMINI_API_KEY を貼り付けて Enter...",
                id="api-key-input",
            )
            yield Static("", markup=False, id="api-key-error", classes="modal-error-msg")
            with Horizontal(classes="modal-buttons"):
                yield Button("確定 (Enter)", id="btn-submit", variant="success", compact=True)
                yield Button("終了 (Esc)", id="btn-cancel", variant="error", compact=True)

    def on_mount(self) -> None:
        self.query_one("#api-key-error", Static).display = False
        self.query_one("#api-key-input", Input).focus()

    def _try_submit(self) -> None:
        val = self.query_one("#api-key-input", Input).value.strip()
        if not val:
            err_w = self.query_one("#api-key-error", Static)
            err_w.update("APIキーが空です。貼り付けてから Enter を押してください。")
            err_w.display = True
            self.query_one("#api-key-input", Input).focus()
            return
        self.dismiss(val)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._try_submit()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-submit":
            self._try_submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)


__all__ = [
    "ApiKeyModal",
]
