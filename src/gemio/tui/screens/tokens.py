"""
トークン使用量・コンテキスト健全性診断モーダル（TokenUsageModal）モジュール。
"""

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from gemio.core.models import (
    CONTEXT_TOKEN_DANGER_THRESHOLD,
    CONTEXT_TOKEN_WARNING_THRESHOLD,
)


class TokenUsageModal(ModalScreen[str | None]):
    """
    現在のコンテキスト健全性とセッション累積トークン内訳を DataTable で表示し、
    その場でのセッションリセット (/clear) も行えるモーダル画面。
    """

    def __init__(self, token_usage: dict[str, int]) -> None:
        super().__init__()
        self.token_usage = dict(token_usage)

    def compose(self) -> ComposeResult:
        ctx_tokens = int(self.token_usage.get("last_prompt_tokens", 0))
        dialog_cls = "modal-dialog-large"
        if ctx_tokens >= CONTEXT_TOKEN_DANGER_THRESHOLD:
            dialog_cls += " modal-dialog-danger"
        elif ctx_tokens >= CONTEXT_TOKEN_WARNING_THRESHOLD:
            dialog_cls += " modal-dialog-warn"

        with Vertical(classes=dialog_cls):
            yield Static(
                "トークン使用量 & コンテキスト診断 (/tokens)",
                markup=False,
                classes="modal-title",
            )
            yield Static("", id="token-summary-box")
            yield DataTable(
                show_row_labels=False,
                zebra_stripes=True,
                cursor_type="row",
                id="token-table",
                classes="modal-data-table",
            )
            with Horizontal(classes="modal-buttons"):
                yield Button("資料管理 (/materials)", id="btn-materials", variant="default", compact=True)
                yield Button("履歴クリア (/clear)", id="btn-clear", variant="warning", compact=True)
                yield Button("資料含め全クリア", id="btn-clear-all", variant="error", compact=True)
                yield Button("閉じる (Esc)", id="btn-close", variant="primary", compact=True)

    def on_mount(self) -> None:
        u = self.token_usage
        ctx_tokens = int(u.get("last_prompt_tokens", 0))
        history_msgs = int(u.get("chat_history_messages", 0))
        materials_cnt = int(
            u.get("materials_count", u.get("search_materials_count", 0))
        )

        summary = Text()
        summary.append("直近コンテキスト量: ", style="bold cyan")
        if ctx_tokens >= CONTEXT_TOKEN_DANGER_THRESHOLD:
            summary.append(
                f"{ctx_tokens:,} tokens [肥大化: 新セッション切替推奨]\n",
                style="bold red",
            )
            summary.append(
                "  150,000 tokens 以上に達しています。Context Rot 防止のため /clear を推奨します。\n",
                style="red",
            )
        elif ctx_tokens >= CONTEXT_TOKEN_WARNING_THRESHOLD:
            summary.append(
                f"{ctx_tokens:,} tokens [やや大: 区切りでの切替を検討]\n",
                style="bold yellow",
            )
            summary.append(
                "  50,000 tokens 以上蓄積しています。作業の区切りで /clear または /compact を検討してください。\n",
                style="yellow",
            )
        else:
            summary.append(
                f"{ctx_tokens:,} tokens [快適・余裕あり]\n",
                style="bold green",
            )

        summary.append(
            f"会話履歴要素数: {history_msgs} 件  |  保持中の作業前提資料: {materials_cnt} 件",
            style="dim",
        )
        self.query_one("#token-summary-box", Static).update(summary)

        table = self.query_one("#token-table", DataTable)
        table.add_columns("内訳項目", "累計値", "説明")

        cum_prompt = int(u.get("cumulative_prompt_tokens", 0))
        cum_cand = int(u.get("cumulative_candidates_tokens", 0))
        cum_thought = int(u.get("cumulative_thoughts_tokens", 0))
        cum_cached = int(u.get("cumulative_cached_tokens", 0))
        cum_tool = int(u.get("cumulative_tool_use_tokens", 0))
        cum_total = int(u.get("cumulative_total_tokens", 0))

        chat_calls = int(u.get("chat_api_calls", 0))
        search_calls = int(u.get("search_api_calls", 0))
        comp_calls = int(u.get("compaction_api_calls", 0))
        total_calls = int(u.get("total_api_calls", 0))

        table.add_row("入力トークン累計 (Prompt)", f"{cum_prompt:,}", "全APIコールで送信された入力総計")
        table.add_row("出力トークン累計 (Candidates)", f"{cum_cand:,}", "AI回答およびツール引数の出力総計")
        table.add_row("思考トークン累計 (Thoughts)", f"{cum_thought:,}", "Thinking推論プロセスで消費された総計")
        table.add_row("キャッシュヒット累計 (Cached)", f"{cum_cached:,}", "コンテキストキャッシュヒット総計")
        if cum_tool > 0:
            table.add_row("ツール利用プロンプト累計", f"{cum_tool:,}", "Google検索等の組み込みツール総計")
        table.add_row(
            Text("累積合計トークン (Total)", style="bold cyan"),
            Text(f"{cum_total:,}", style="bold cyan"),
            "このセッションで消費された全トークン総計",
        )
        table.add_row(
            "APIリクエスト回数 (Calls)",
            f"{total_calls:,} 回",
            f"対話: {chat_calls} / 検索: {search_calls} / 要約圧縮: {comp_calls}",
        )

        self.query_one("#btn-close", Button).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in {"escape", "q"}:
            event.stop()
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "btn-materials":
            self.dismiss("materials")
        elif event.button.id == "btn-clear":
            self.dismiss("clear")
        elif event.button.id == "btn-clear-all":
            self.dismiss("clear_all")
        else:
            self.dismiss(None)


__all__ = [
    "TokenUsageModal",
]
