"""
画面下部の実行状態バー（ActivityBar）および自動伸縮マルチライン入力欄（PromptComposer）。
"""

import time

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Static, TextArea

from gemio.tui.messages import CancelTurnRequested, PromptSubmitted

_SPINNER_FRAMES = ("|", "/", "-", "\\")
_IDLE_GUIDE_TEXT = (
    "Ready — Ctrl+J: 送信 | Enter: 改行 | F7→BS: 全消去 | F4: 入力欄 | PageUp/Down: スクロール"
)
# PromptTextArea は border: tall により上下2行の枠を消費するため、
# height=3 が内寸1行、height=8 が内寸6行に対応する。
COMPOSER_BORDER_HEIGHT = 2
MIN_COMPOSER_LINES = 3
MAX_COMPOSER_LINES = 8


class ActivityBar(Widget):
    """AI実行中・Web検索中の進行状況スピナー、経過秒数、およびターン中断ボタンを表示する1行バー。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._busy: bool = False
        self._base_label: str = ""
        self._detail: str = ""
        self._started_at: float = 0.0
        self._frame_idx: int = 0
        self._timer: Timer | None = None

    @property
    def is_busy(self) -> bool:
        return self._busy

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static(
                _IDLE_GUIDE_TEXT,
                markup=False,
                id="activity-label",
                classes="activity-status-text",
            )
            yield Button(
                "Esc: ターン中断",
                id="cancel-turn-btn",
                classes="cancel-turn-btn",
                compact=True,
            )

    def on_mount(self) -> None:
        self.query_one("#cancel-turn-btn", Button).display = False

    def start_busy(self, label: str = "Gemioが思考・実行中...") -> None:
        """実行中アニメーションと経過時間カウントを開始します。"""
        self._busy = True
        self._base_label = label
        self._detail = ""
        self._started_at = time.monotonic()
        self._frame_idx = 0
        self.add_class("busy")

        if self.is_mounted:
            self.query_one("#cancel-turn-btn", Button).display = True
            self._render_busy_frame()
            if self._timer is not None:
                self._timer.stop()
            self._timer = self.set_interval(0.2, self._tick)

    def update_detail(self, detail: str) -> None:
        """現在実行中のツール名等の補足情報を更新します。"""
        self._detail = detail
        if self._busy and self.is_mounted:
            self._render_busy_frame()

    def stop_busy(self) -> None:
        """実行中表示を終了し、待機ガイド表示へ戻します。"""
        self._busy = False
        self._detail = ""
        self.remove_class("busy")
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self.is_mounted:
            self.query_one("#cancel-turn-btn", Button).display = False
            self.query_one("#activity-label", Static).update(_IDLE_GUIDE_TEXT)

    def _tick(self) -> None:
        if not self._busy:
            return
        self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
        self._render_busy_frame()

    def _render_busy_frame(self) -> None:
        spinner = _SPINNER_FRAMES[self._frame_idx]
        elapsed = max(0.0, time.monotonic() - self._started_at)
        detail_part = f" ({self._detail})" if self._detail else ""
        text = f"[{spinner}] {self._base_label}{detail_part} [{elapsed:.1f}s]"
        self.query_one("#activity-label", Static).update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-turn-btn":
            event.stop()
            self.post_message(CancelTurnRequested())


class PromptTextArea(TextArea):
    """
    Ctrl+J 送信、Enter 改行、および自動高さ伸縮を備えたクロスプラットフォーム対応入力エディタ。
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            "",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=False,
            highlight_cursor_line=False,
            placeholder=" プロンプトまたはコマンド (/help 等) を入力...",
            id=id,
        )

    def clear_undoable(self) -> None:
        """入力中の全テキストを Undo (Ctrl+Z) で復元可能な状態で消去します。"""
        if self.text:
            self.replace("", (0, 0), self.document.end)
            self._sync_auto_height()

    def _sync_auto_height(self) -> None:
        line_cnt = max(1, self.document.line_count)
        visual_lines = line_cnt
        if self.is_mounted and self.size.width > 0:
            visual_lines = max(line_cnt, self.wrapped_document.height)
        new_height = min(
            MAX_COMPOSER_LINES,
            max(MIN_COMPOSER_LINES, visual_lines + COMPOSER_BORDER_HEIGHT),
        )
        self.styles.height = new_height

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        old_height = self.styles.height
        self._sync_auto_height()
        # TextArea.edit() は _refresh_size() より前に scroll_cursor_visible() を呼ぶため、
        # 最大高さ到達後の改行などで virtual_size が増えた直後にスクロールが追従しない。
        # Changed 処理時点では virtual_size が更新済みのため、ここで追従を補完する。
        if self.styles.height == old_height:
            self.scroll_cursor_visible()
        else:
            self.call_after_refresh(self.scroll_cursor_visible)

    def on_resize(self, event: events.Resize) -> None:
        self._sync_auto_height()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            raw = self.text
            if raw.strip():
                self.post_message(PromptSubmitted(raw))
            return

        await super()._on_key(event)


class PromptComposer(Widget):
    """PromptTextArea と Send ボタンを組み合わせた下部プロンプト入力コンポーネント。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PromptTextArea(id="prompt-input")
            yield Button("Send", id="send-btn", classes="send-btn")

    @property
    def editor(self) -> PromptTextArea:
        return self.query_one("#prompt-input", PromptTextArea)

    def focus_editor(self) -> None:
        """プロンプト入力エディタへフォーカスを移動します。"""
        if self.is_mounted:
            self.editor.focus()

    def clear_after_accept(self) -> None:
        """送信がアプリ側に受理された後に入力欄をクリアして高さを初期状態へ戻します。"""
        self.editor.clear()
        self.editor.styles.height = MIN_COMPOSER_LINES

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            event.stop()
            raw = self.editor.text
            if raw.strip():
                self.post_message(PromptSubmitted(raw))


__all__ = [
    "ActivityBar",
    "MAX_COMPOSER_LINES",
    "MIN_COMPOSER_LINES",
    "PromptComposer",
    "PromptTextArea",
]
