"""
AI モデル選択モーダル（ModelSelectModal）モジュール。
"""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from gemio.core.models import MODEL_PRESETS, resolve_model_name


class ModelSelectModal(ModalScreen[str | None]):
    """プリセットモデル一覧からの選択またはエイリアス直接指定を行うモーダル画面。"""

    def __init__(self, current_model: str) -> None:
        super().__init__()
        self.current_model = current_model

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(
                f"AIモデル選択 (現在: {self.current_model})",
                markup=False,
                classes="modal-title",
            )

            options: list[Option] = []
            for idx, (m_name, m_desc) in enumerate(MODEL_PRESETS, 1):
                line = Text()
                marker = "* " if m_name == self.current_model else "  "
                line.append(marker, style="bold green")
                line.append(f"[{idx}] ", style="bold cyan")
                line.append(m_name, style="bold")
                line.append(f" - {m_desc}", style="dim")
                options.append(Option(line, id=f"preset-{idx - 1}"))

            yield OptionList(
                *options,
                id="model-option-list",
                classes="modal-option-list",
            )
            yield Input(
                placeholder="またはモデル名・エイリアス (argon, 3.8, lite 等) を直接入力して Enter...",
                id="model-custom-input",
            )
            yield Static(
                "数字キー(1-9)またはEnterで選択 / Tabで直接入力欄へ移動 / Escでキャンセル",
                markup=False,
                classes="modal-hint",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("直接指定を適用", id="btn-apply-custom", variant="primary", compact=True)
                yield Button("キャンセル (Esc)", id="btn-cancel", variant="default", compact=True)

    def on_mount(self) -> None:
        opt_list = self.query_one("#model-option-list", OptionList)
        for idx, (m_name, _) in enumerate(MODEL_PRESETS):
            if m_name == self.current_model:
                opt_list.highlighted = idx
                break
        opt_list.focus()

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        event.stop()
        opt_id = event.option.id or ""
        if opt_id.startswith("preset-"):
            idx = int(opt_id.split("-", 1)[1])
            if 0 <= idx < len(MODEL_PRESETS):
                self.dismiss(MODEL_PRESETS[idx][0])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        raw = event.value.strip()
        if raw:
            self.dismiss(resolve_model_name(raw))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
            return

        custom_input = self.query_one("#model-custom-input", Input)
        if self.focused is custom_input:
            return

        if event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < len(MODEL_PRESETS):
                event.stop()
                self.dismiss(MODEL_PRESETS[idx][0])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-apply-custom":
            raw = self.query_one("#model-custom-input", Input).value.strip()
            if raw:
                self.dismiss(resolve_model_name(raw))
            else:
                self.query_one("#model-custom-input", Input).focus()


__all__ = [
    "ModelSelectModal",
]
