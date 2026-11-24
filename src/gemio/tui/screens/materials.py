"""
作業前提資料（MaterialStore）の一覧閲覧・手動追加・個別削除・全クリアを行うモーダル（MaterialsModal）モジュール。
"""

from typing import Callable

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Static,
    TabPane,
    TabbedContent,
    TextArea,
)

from gemio.core.context.materials import (
    ReferenceMaterial,
    diagnose_user_pasted_text,
    sanitize_pasted_text,
)


class MaterialsModal(ModalScreen[bool]):
    """
    セッション内で保持している作業前提資料を管理（一覧・プレビュー・手動追加・削除）するモーダル画面。
    """

    def __init__(
        self,
        *,
        list_callback: Callable[[], list[ReferenceMaterial]],
        add_callback: Callable[[str, str], tuple[ReferenceMaterial, str]],
        remove_callback: Callable[[str | int], ReferenceMaterial | None],
        clear_callback: Callable[[], int],
        forbidden_paths: list[str] | None = None,
        initial_tab: str = "tab-mat-list",
    ) -> None:
        super().__init__()
        self._list_callback = list_callback
        self._add_callback = add_callback
        self._remove_callback = remove_callback
        self._clear_callback = clear_callback
        self._forbidden_paths = list(forbidden_paths or [])
        self._initial_tab = initial_tab
        self._selected_id: str | None = None
        self._changed: bool = False
        self._has_blocking: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-large materials-dialog"):
            yield Static(
                "作業前提資料の管理 (/materials)",
                markup=False,
                classes="modal-title",
            )
            with TabbedContent(initial=self._initial_tab, id="materials-tabs"):
                with TabPane("登録済み一覧", id="tab-mat-list"):
                    yield DataTable(
                        show_row_labels=False,
                        zebra_stripes=True,
                        cursor_type="row",
                        id="materials-table",
                        classes="materials-table",
                    )
                    with VerticalScroll(classes="materials-preview-scroll"):
                        yield Static(
                            "登録されている作業前提資料はありません。",
                            markup=False,
                            id="materials-preview",
                        )
                    with Horizontal(classes="materials-list-actions"):
                        yield Button(
                            "選択を削除",
                            id="btn-mat-remove",
                            variant="warning",
                            compact=True,
                        )
                        yield Button(
                            "全クリア",
                            id="btn-mat-clear",
                            variant="error",
                            compact=True,
                        )
                with TabPane("手動で追加", id="tab-mat-add"):
                    yield Input(
                        placeholder="資料タイトル（例: API仕様メモ）",
                        id="mat-add-title",
                    )
                    yield TextArea(
                        "",
                        soft_wrap=True,
                        tab_behavior="focus",
                        show_line_numbers=False,
                        highlight_cursor_line=False,
                        id="mat-add-content",
                        classes="materials-add-textarea",
                    )
                    with Horizontal(classes="materials-diag-row"):
                        yield Static(
                            "",
                            id="mat-add-diagnosis",
                            classes="materials-diagnosis",
                        )
                        yield Button(
                            "無害化",
                            id="btn-mat-sanitize",
                            variant="warning",
                            compact=True,
                            classes="materials-aux-btn",
                        )
                        yield Button(
                            "資料に追加",
                            id="btn-mat-add-submit",
                            variant="success",
                            compact=True,
                        )
            with Horizontal(classes="modal-buttons"):
                yield Button(
                    "閉じる (Esc)",
                    id="btn-close",
                    variant="primary",
                    compact=True,
                )

    def on_mount(self) -> None:
        table = self.query_one("#materials-table", DataTable)
        table.add_columns("ID", "タイトル", "文字数", "追加時刻")
        self._reload_table()
        self._refresh_add_diagnosis("")
        self.query_one("#btn-close", Button).focus()

    def _reload_table(self) -> None:
        items = self._list_callback()
        table = self.query_one("#materials-table", DataTable)
        table.clear()
        for mat in items:
            table.add_row(
                mat.material_id,
                mat.title,
                f"{mat.char_count:,}",
                mat.created_at or "-",
                key=mat.material_id,
            )

        if items:
            first_mat = items[0]
            self._selected_id = first_mat.material_id
            self._show_preview(first_mat)
        else:
            self._selected_id = None
            self.query_one("#materials-preview", Static).update(
                "登録されている作業前提資料はありません。"
            )

        has_items = bool(items)
        self.query_one("#btn-mat-remove", Button).disabled = not has_items
        self.query_one("#btn-mat-clear", Button).disabled = not has_items

    def _show_preview(self, mat: ReferenceMaterial) -> None:
        preview_w = self.query_one("#materials-preview", Static)
        preview_w.update(
            f"[{mat.material_id}] {mat.title} ({mat.char_count:,}文字)\n"
            f"---\n{mat.content}"
        )

    def _refresh_add_diagnosis(self, text: str) -> None:
        diag = diagnose_user_pasted_text(
            text,
            forbidden_paths=self._forbidden_paths,
        )
        self._has_blocking = diag.has_blocking_secrets
        diag_w = self.query_one("#mat-add-diagnosis", Static)
        msg = Text()
        if diag.char_count == 0:
            msg.append("プロンプト先頭に常時注入したい参照資料を入力してください", style="dim")
        else:
            msg.append(f"{diag.char_count:,}文字 (~{diag.estimated_tokens:,} tokens)", style="cyan")
            if diag.has_blocking_secrets:
                msg.append(
                    f" | [登録不可] 機密検出: {', '.join(diag.blocking_secrets)}",
                    style="bold red",
                )
            elif diag.path_warnings:
                msg.append(f" | 注意: {', '.join(diag.path_warnings)}", style="yellow")
            elif diag.needs_sanitization:
                msg.append(" | 不可視/制御文字あり", style="yellow")
            else:
                msg.append(" | 診断OK", style="green")
        diag_w.update(msg)

        can_add = bool(text.strip()) and not diag.has_blocking_secrets
        self.query_one("#btn-mat-add-submit", Button).disabled = not can_add
        self.query_one("#btn-mat-sanitize", Button).disabled = not diag.needs_sanitization

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        if event.row_key and event.row_key.value is not None:
            mat_id = str(event.row_key.value)
            self._selected_id = mat_id
            for mat in self._list_callback():
                if mat.material_id == mat_id:
                    self._show_preview(mat)
                    break

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "mat-add-content":
            self._refresh_add_diagnosis(event.text_area.text)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(self._changed)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""

        if btn_id == "btn-close":
            self.dismiss(self._changed)
            return

        if btn_id == "btn-mat-remove":
            if self._selected_id:
                removed = self._remove_callback(self._selected_id)
                if removed is not None:
                    self._changed = True
                    self.app.notify(f"資料 '{removed.material_id}' を削除しました。")
                    self._reload_table()
            return

        if btn_id == "btn-mat-clear":
            cnt = self._clear_callback()
            if cnt > 0:
                self._changed = True
                self.app.notify(f"作業前提資料（{cnt}件）をすべて破棄しました。")
                self._reload_table()
            return

        if btn_id == "btn-mat-sanitize":
            ta = self.query_one("#mat-add-content", TextArea)
            sanitized = sanitize_pasted_text(ta.text)
            ta.load_text(sanitized)
            self._refresh_add_diagnosis(sanitized)
            self.app.notify("不可視文字・制御トークンを除去しました。")
            return

        if btn_id == "btn-mat-add-submit":
            title_in = self.query_one("#mat-add-title", Input)
            content_ta = self.query_one("#mat-add-content", TextArea)
            title = title_in.value.strip() or "ユーザー参照資料"
            content = content_ta.text.strip()
            if not content:
                self.app.notify("資料本文を入力してください。", severity="warning")
                return
            try:
                mat, status = self._add_callback(title, content)
            except ValueError as err:
                self.app.notify(str(err), severity="error")
                return
            self._changed = True
            title_in.value = ""
            content_ta.load_text("")
            self._refresh_add_diagnosis("")
            self._reload_table()
            self.query_one("#materials-tabs", TabbedContent).active = "tab-mat-list"
            if status == "duplicate":
                self.app.notify("同一内容の資料がすでに登録されています。")
            else:
                self.app.notify(f"作業前提資料 '{mat.material_id}: {mat.title}' を追加しました。")


__all__ = [
    "MaterialsModal",
]
