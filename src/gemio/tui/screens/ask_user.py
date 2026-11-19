"""
ask_user ツールによる AI からの質問・確認モーダル（AskUserModal）モジュール。
"""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from gemio.tui.bridge import MODAL_ABORT_SENTINEL


class AskUserModal(ModalScreen[str]):
    """
    AIからの質問・背景（Markdown表示）と選択肢（OptionList + 自由記述 Input）を表示するモーダル画面。
    """

    def __init__(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> None:
        super().__init__()
        self.question = question.strip()
        self.options = [str(opt).strip() for opt in (options or []) if str(opt).strip()]
        self.context = context.strip()

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(
                "Gemio からの確認・質問",
                markup=False,
                classes="modal-title",
            )
            with VerticalScroll(classes="modal-scroll-body"):
                if self.context:
                    yield Static("【背景・状況】", markup=False)
                    yield Markdown(self.context)
                yield Static("【質問】", markup=False)
                yield Markdown(self.question)

            if self.options:
                opt_items: list[Option] = []
                for idx, opt_str in enumerate(self.options, 1):
                    label = Text()
                    label.append(f"[{idx}] ", style="bold cyan")
                    label.append(opt_str)
                    opt_items.append(Option(label, id=f"opt-{idx}"))

                other_label = Text()
                other_label.append("[0] ", style="bold cyan")
                other_label.append("その他（下の入力欄に自由記述を入力）")
                opt_items.append(Option(other_label, id="opt-custom"))

                yield OptionList(
                    *opt_items,
                    id="ask-option-list",
                    classes="modal-option-list",
                )

            yield Input(
                placeholder="自由記述で回答・指示を入力して Enter...",
                id="ask-custom-input",
            )
            hint = (
                "数字キー(1-9)またはEnterで選択 / 0で自由記述へ移動 / EscまたはAbortで作業中止"
                if self.options
                else "回答を入力して Enter / EscまたはAbortで作業中止"
            )
            yield Static(hint, markup=False, classes="modal-hint")
            with Horizontal(classes="modal-buttons"):
                yield Button("回答を送信", id="btn-submit-custom", variant="primary", compact=True)
                yield Button("作業中止 (Abort)", id="btn-abort", variant="error", compact=True)

    def on_mount(self) -> None:
        if self.options:
            self.query_one("#ask-option-list", OptionList).focus()
        else:
            self.query_one("#ask-custom-input", Input).focus()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        event.stop()
        opt_id = event.option.id or ""
        if opt_id == "opt-custom":
            self.query_one("#ask-custom-input", Input).focus()
            return
        if opt_id.startswith("opt-"):
            idx = int(opt_id.split("-", 1)[1]) - 1
            if 0 <= idx < len(self.options):
                self.dismiss(self.options[idx])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        val = event.value.strip()
        if not val:
            return
        self.dismiss(val)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(MODAL_ABORT_SENTINEL)
            return

        # 自由入力欄以外にフォーカスがある場合のみ、数字ショートカットと 'a' 中止を有効化
        custom_input = self.query_one("#ask-custom-input", Input)
        if self.focused is custom_input:
            return

        key = event.key.lower()
        if key == "a":
            event.stop()
            self.dismiss(MODAL_ABORT_SENTINEL)
            return

        if self.options and key.isdigit():
            num = int(key)
            if num == 0:
                event.stop()
                custom_input.focus()
            elif 1 <= num <= len(self.options):
                event.stop()
                self.dismiss(self.options[num - 1])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-abort":
            self.dismiss(MODAL_ABORT_SENTINEL)
            return
        if event.button.id == "btn-submit-custom":
            val = self.query_one("#ask-custom-input", Input).value.strip()
            if val:
                self.dismiss(val)
            else:
                self.query_one("#ask-custom-input", Input).focus()


__all__ = [
    "AskUserModal",
]
