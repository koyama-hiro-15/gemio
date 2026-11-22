"""
AI モデル選択モーダル（ModelSelectModal）モジュール。

キャッシュ済みまたは標準フォールバック一覧を待ち時間ゼロで即時表示しつつ、
バックグラウンドで API から最新のテキスト生成モデル一覧・Thinking対応可否・コンテキスト上限を取得して更新できます。
"""

from typing import Callable

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from gemio.core.models import (
    ModelInfo,
    get_fallback_model_catalog,
    resolve_model_name,
)

# ダイアログ内の固定UI要素（上下枠線2 + タイトル2 + OptionList枠線/余白3 + Input3 + ヒント2 + ボタン2）の合計行数
_DIALOG_FIXED_CHROME_ROWS = 14
_DIALOG_MAX_WIDTH = 112
_MIN_OPTION_CELL_WIDTH = 36


class ModelSelectModal(ModalScreen[str | None]):
    """利用可能モデル一覧（動的カタログ対応）からの選択またはエイリアス直接指定を行うモーダル画面。"""

    def __init__(
        self,
        current_model: str,
        models: list[ModelInfo] | None = None,
        *,
        fetch_callback: Callable[[bool], list[ModelInfo]] | None = None,
        auto_fetch: bool = False,
    ) -> None:
        super().__init__()
        self.current_model: str = current_model
        self._models: list[ModelInfo] = (
            list(models) if models is not None else get_fallback_model_catalog()
        )
        self._fetch_callback = fetch_callback
        self._auto_fetch: bool = auto_fetch
        self._last_avail_cells: int = 0

    def _compute_available_option_cells(self, screen_width: int) -> int:
        dlg_width = min(max(int(screen_width * 0.96), 48), _DIALOG_MAX_WIDTH)
        # ダイアログ枠線(2) + 左右padding(4) + OptionList枠線(2) + 項目左右padding(2) + スクロールバー余白(2) = 12
        return max(_MIN_OPTION_CELL_WIDTH, dlg_width - 12)

    def _compute_dialog_height(self, screen_height: int) -> int:
        desired_rows = _DIALOG_FIXED_CHROME_ROWS + max(len(self._models), 3)
        max_rows = max(int(screen_height * 0.94), 16)
        return min(desired_rows, max_rows)

    def _build_option(
        self,
        idx: int,
        info: ModelInfo,
        max_cells: int | None = None,
    ) -> Option:
        line = Text(no_wrap=True)
        marker = "* " if info.model_id == self.current_model else "  "
        line.append(marker, style="bold green")
        if idx <= 9:
            line.append(f"[{idx}] ", style="bold cyan")
        else:
            line.append("    ")
        line.append(info.model_id, style="bold")

        if info.supports_thinking:
            line.append(" [Think]", style="bold magenta")
        ctx_str = info.format_context_limit()
        if ctx_str:
            line.append(f" [{ctx_str}]", style="cyan")

        desc = info.description or info.display_name
        if desc:
            first_line = desc.splitlines()[0].strip()
            if first_line:
                line.append(f" - {first_line}", style="dim")

        if max_cells is not None and max_cells > 0:
            line.truncate(max_cells, overflow="ellipsis")

        return Option(line, id=f"model-idx-{idx - 1}")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-large", id="model-select-dialog"):
            yield Static(
                f"AIモデル選択 (現在: {self.current_model})",
                markup=False,
                id="model-modal-title",
                classes="modal-title",
            )

            options = [
                self._build_option(idx, info)
                for idx, info in enumerate(self._models, 1)
            ]
            yield OptionList(
                *options,
                id="model-option-list",
                classes="model-select-option-list",
            )
            yield Input(
                placeholder="またはモデル名・エイリアス (neon, carbon, argon, hydrogen, lite 等) を直接入力して Enter...",
                id="model-custom-input",
            )
            hint = (
                "数字(1-9)/Enter:選択 / R:一覧更新 / Tab:直接入力 / Esc:キャンセル"
                if self._fetch_callback is not None
                else "数字(1-9)/Enter:選択 / Tab:直接入力欄へ移動 / Esc:キャンセル"
            )
            yield Static(
                hint,
                markup=False,
                id="model-modal-hint",
                classes="modal-hint",
            )
            with Horizontal(classes="modal-buttons"):
                if self._fetch_callback is not None:
                    yield Button(
                        "一覧更新 (R)",
                        id="btn-refresh",
                        variant="default",
                        compact=True,
                    )
                yield Button(
                    "直接指定を適用",
                    id="btn-apply-custom",
                    variant="primary",
                    compact=True,
                )
                yield Button(
                    "キャンセル (Esc)",
                    id="btn-cancel",
                    variant="default",
                    compact=True,
                )

    def _sync_layout_and_options(self, *, rebuild_options: bool = False) -> None:
        if not self.is_mounted or self.app is None:
            return
        screen_w, screen_h = self.app.size
        dlg = self.query_one("#model-select-dialog", Vertical)
        dlg.styles.height = self._compute_dialog_height(screen_h)

        avail_cells = self._compute_available_option_cells(screen_w)
        if rebuild_options or avail_cells != self._last_avail_cells:
            self._last_avail_cells = avail_cells
            opt_list = self.query_one("#model-option-list", OptionList)
            prev_highlight = opt_list.highlighted
            opt_list.clear_options()
            opt_list.add_options(
                [
                    self._build_option(idx, info, max_cells=avail_cells)
                    for idx, info in enumerate(self._models, 1)
                ]
            )
            if (
                prev_highlight is not None
                and 0 <= prev_highlight < len(self._models)
                and not rebuild_options
            ):
                opt_list.highlighted = prev_highlight
            else:
                self._highlight_current_model()

    def on_mount(self) -> None:
        self._sync_layout_and_options(rebuild_options=True)
        self.query_one("#model-option-list", OptionList).focus()
        if self._auto_fetch and self._fetch_callback is not None:
            self._refresh_models_in_thread(force_refresh=False)

    def on_resize(self, event: events.Resize) -> None:
        self._sync_layout_and_options(rebuild_options=False)

    def _highlight_current_model(self) -> None:
        opt_list = self.query_one("#model-option-list", OptionList)
        for idx, info in enumerate(self._models):
            if info.model_id == self.current_model:
                opt_list.highlighted = idx
                return
        if self._models:
            opt_list.highlighted = 0

    @work(thread=True, exclusive=True, group="model_catalog_fetch")
    def _refresh_models_in_thread(self, force_refresh: bool = True) -> None:
        if self._fetch_callback is None:
            return
        try:
            fetched = self._fetch_callback(force_refresh)
        except Exception:
            return
        if self.app is not None and self.is_mounted:
            try:
                self.app.call_from_thread(self._apply_fetched_models, fetched)
            except Exception:
                pass

    def _apply_fetched_models(self, models: list[ModelInfo]) -> None:
        if not self.is_mounted or not models:
            return
        self._models = list(models)
        self._sync_layout_and_options(rebuild_options=True)
        title_w = self.query_one("#model-modal-title", Static)
        title_w.update(
            f"AIモデル選択 (現在: {self.current_model} / 利用可能: {len(self._models)}件)"
        )

    def on_option_list_option_selected(
        self,
        event: OptionList.OptionSelected,
    ) -> None:
        event.stop()
        opt_id = event.option.id or ""
        if opt_id.startswith("model-idx-"):
            idx = int(opt_id.split("model-idx-", 1)[1])
            if 0 <= idx < len(self._models):
                self.dismiss(self._models[idx].model_id)

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

        lowered = event.key.lower()
        if lowered == "r" and self._fetch_callback is not None:
            event.stop()
            self._refresh_models_in_thread(force_refresh=True)
            return

        if event.key.isdigit():
            idx = int(event.key) - 1
            if 0 <= idx < len(self._models):
                event.stop()
                self.dismiss(self._models[idx].model_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-refresh":
            self._refresh_models_in_thread(force_refresh=True)
        elif event.button.id == "btn-apply-custom":
            raw = self.query_one("#model-custom-input", Input).value.strip()
            if raw:
                self.dismiss(resolve_model_name(raw))
            else:
                self.query_one("#model-custom-input", Input).focus()


__all__ = [
    "ModelSelectModal",
]
