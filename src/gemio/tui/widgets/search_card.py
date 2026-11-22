"""
/search コマンドによる単発 Web 検索結果を表示する SearchTurnWidget モジュール。

検索クエリはプレーンテキストそのまま (markup=False) で表示し、
検索回答は Markdown レンダリング、下部に出典一覧と任意での作業前提資料への追加UIを表示します。
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, Markdown, Static

from gemio.app.state import SearchOutcome
from gemio.core.context.materials import MaterialStore
from gemio.tui.messages import CopyTextRequested, SaveSearchMaterialRequested


class SearchTurnWidget(Widget):
    """1回の単発 Web 検索実行結果と作業前提資料への任意追加UIを表示するカードウィジェット。"""

    def __init__(
        self,
        query: str,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.query: str = query
        self.response_text: str = ""
        self._outcome: SearchOutcome | None = None
        self._dom_ready: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-header-row"):
            yield Static("Web Search", markup=False, classes="card-role-search")
            yield Static(
                "",
                markup=False,
                classes="search-timestamp card-timestamp",
            )
            yield Button(
                "+ 資料に追加",
                id="btn-save-material",
                classes="save-mat-btn",
                compact=True,
            )
            yield Button("Copy", id="btn-copy-search", classes="copy-btn", compact=True)

        # クエリ入力も Markdown 解釈せず生テキストそのまま表示
        yield Static(
            f"/search {self.query}",
            markup=False,
            classes="search-query-body",
        )
        yield Markdown("", classes="search-markdown")
        yield Static("", classes="search-sources")
        yield Static("", classes="search-material-footer")

    def on_mount(self) -> None:
        self._dom_ready = True
        if self._outcome is not None:
            self._apply_outcome_to_dom(self._outcome)
        else:
            self.query_one("#btn-save-material", Button).display = False
            self.query_one(".search-sources", Static).display = False
            self.query_one(".search-material-footer", Static).display = False

    def set_outcome(self, outcome: SearchOutcome) -> None:
        """SearchOutcome を受け取り、回答本文・出典・前提資料追加ボタン/ステータスを描画します。"""
        self._outcome = outcome
        self.response_text = outcome.result.response_text or ""
        is_err = outcome.material_status in {"failed", "aborted"}
        self.set_class(is_err, "search-failed")

        if self._dom_ready:
            self._apply_outcome_to_dom(outcome)

    def _apply_outcome_to_dom(self, outcome: SearchOutcome) -> None:
        res = outcome.result
        self.query_one(".search-timestamp", Static).update(res.created_at)
        self.query_one(".search-markdown", Markdown).update(self.response_text)

        save_btn = self.query_one("#btn-save-material", Button)
        if outcome.material_status == "unretained":
            save_btn.label = "+ 資料に追加"
            save_btn.disabled = False
            save_btn.display = True
        elif outcome.material_status in {"added", "duplicate"}:
            save_btn.label = "資料保持済"
            save_btn.disabled = True
            save_btn.display = True
        else:
            save_btn.display = False

        sources_w = self.query_one(".search-sources", Static)
        if res.sources:
            src_text = Text("Sources: ", style="bold cyan")
            for idx, src in enumerate(res.sources):
                if idx > 0:
                    src_text.append("  |  ", style="dim")
                display_name = MaterialStore.extract_source_display(
                    src.get("title", ""), src.get("url", "")
                )
                src_text.append(display_name, style="underline cyan")
            sources_w.update(src_text)
            sources_w.display = True
        else:
            sources_w.display = False

        footer_w = self.query_one(".search-material-footer", Static)
        footer_text = Text()
        total = outcome.total_materials
        if outcome.material_status == "unretained":
            footer_text.append(
                f"[単発検索] 作業前提資料として保持する場合は [+ 資料に追加] または /search --save を実行（現在 {total} 件）。",
                style="dim",
            )
        elif outcome.material_status == "added":
            footer_text.append(
                f"[資料保持] 作業前提資料 [第{total}弾] としてセッションに保持しました。（破棄: /search --clear）",
                style="green",
            )
        elif outcome.material_status == "duplicate":
            footer_text.append(
                f"[重複スキップ] 同一内容の資料がすでに保持されています（現在 {total} 件）。",
                style="cyan",
            )
        elif outcome.material_status == "aborted":
            footer_text.append(
                f"[中断] Web検索が中断されました（現在保持中の前提資料: {total} 件）。",
                style="yellow",
            )
        else:
            footer_text.append(
                f"[エラー] 検索エラーのため結果を取得できませんでした（現在保持中の前提資料: {total} 件）。",
                style="red",
            )
        footer_w.update(footer_text)
        footer_w.display = True

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-save-material":
            if self._outcome is not None and self._outcome.result.succeeded:
                self.post_message(
                    SaveSearchMaterialRequested(self, self._outcome.result)
                )
            return

        target_text = self.response_text or self.query
        self.post_message(
            CopyTextRequested(target_text, label="検索結果")
        )


__all__ = [
    "SearchTurnWidget",
]
