"""
人間介在型 Web 検索支援用の専用ダイアログ（UserSearchModal）モジュール。

AIが提示した調査目的・確認観点・推奨検索クエリ（[Copy] / Google検索 [Open] ボタン付き）を表示し、
ユーザーがブラウザで検索して貼り付けたテキストをリアルタイム診断（機密情報ブロック・パス注意喚起・
不可視文字無害化・IPI/サイズ警告）した上で、「通常応答」または「作業前提資料」として送信できます。
"""

from urllib.parse import quote_plus
import webbrowser

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

from gemio.app.callbacks import UserSearchReply, UserSearchRequest
from gemio.core.search import (
    InputTextDiagnosis,
    diagnose_user_pasted_text,
    sanitize_pasted_text,
)


class _PasteTextArea(TextArea):
    """Ctrl+J で通常送信トリガーを発火できる複数行貼り付け用 TextArea。"""

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(
            "",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=False,
            highlight_cursor_line=False,
            id=id,
            classes=classes,
        )


class UserSearchModal(ModalScreen[UserSearchReply]):
    """
    AIからの外部情報検索依頼を提示し、ユーザーによる検索結果貼り付け・診断・返却方式選択を行うモーダル画面。
    """

    def __init__(
        self,
        request: UserSearchRequest,
        *,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.request: UserSearchRequest = request
        self.forbidden_paths: list[str] = list(forbidden_paths or [])
        self._diagnosis: InputTextDiagnosis = diagnose_user_pasted_text(
            "",
            forbidden_paths=self.forbidden_paths,
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-large user-search-dialog"):
            yield Static(
                "外部情報の検索協力依頼 (User Search Assist)",
                markup=False,
                classes="modal-title",
            )
            with VerticalScroll(classes="user-search-guide"):
                yield Static(
                    f"【目的】 {self.request.purpose}",
                    markup=False,
                    classes="user-search-meta",
                )
                yield Static(
                    f"【取得してほしい情報】 {self.request.what_to_check}",
                    markup=False,
                    classes="user-search-meta",
                )
                yield Static("【推奨検索クエリ】", markup=False, classes="user-search-label")
                for idx, query in enumerate(self.request.queries, 1):
                    with Horizontal(classes="user-search-query-row"):
                        yield Static(
                            f"{idx}. {query}",
                            markup=False,
                            classes="user-search-query-text",
                        )
                        yield Button(
                            "Copy",
                            id=f"btn-copy-q-{idx}",
                            classes="query-op-btn",
                            compact=True,
                        )
                        if self.request.allow_browser_open:
                            yield Button(
                                "Open",
                                id=f"btn-open-q-{idx}",
                                classes="query-op-btn",
                                compact=True,
                            )

            yield Static(
                f"前提資料登録時タイトル: {self.request.suggested_title}",
                markup=False,
                id="user-search-title-preview",
                classes="user-search-title-preview",
            )
            yield _PasteTextArea(
                id="user-search-textarea",
                classes="user-search-textarea",
            )
            with Horizontal(classes="user-search-diag-row"):
                yield Static(
                    "",
                    id="user-search-diagnosis",
                    classes="user-search-diagnosis",
                )
                yield Button(
                    "無害化",
                    id="btn-sanitize",
                    variant="warning",
                    compact=True,
                    classes="user-search-aux-btn",
                )
                yield Button(
                    "クリア",
                    id="btn-clear-text",
                    compact=True,
                    classes="user-search-aux-btn",
                )
            with Horizontal(classes="modal-buttons"):
                yield Button(
                    "通常応答で送信",
                    id="btn-send-once",
                    variant="primary",
                    compact=True,
                )
                yield Button(
                    "前提資料に追加し送信",
                    id="btn-save-material",
                    variant="success",
                    compact=True,
                )
                yield Button(
                    "検索せず続行",
                    id="btn-skip",
                    variant="warning",
                    compact=True,
                )
                yield Button(
                    "中止 (Esc)",
                    id="btn-abort",
                    variant="error",
                    compact=True,
                )

    def on_mount(self) -> None:
        self._refresh_diagnosis("")
        self.query_one("#user-search-textarea", TextArea).focus()

    def _refresh_diagnosis(self, text: str) -> None:
        diag = diagnose_user_pasted_text(
            text,
            forbidden_paths=self.forbidden_paths,
        )
        self._diagnosis = diag

        diag_w = self.query_one("#user-search-diagnosis", Static)
        msg = Text()
        if diag.char_count == 0:
            msg.append("ブラウザで検索した結果を上の欄に貼り付けてください（Enter: 改行 / Ctrl+J: 通常送信）", style="dim")
        else:
            size_style = "green"
            if diag.size_level == "warning":
                size_style = "bold red"
            elif diag.size_level == "caution":
                size_style = "yellow"
            msg.append(
                f"{diag.char_count:,}文字 / {diag.line_count}行 (~{diag.estimated_tokens:,} tokens)",
                style=size_style,
            )

            if diag.has_blocking_secrets:
                msg.append(
                    f" | [送信ブロック] 機密検出: {', '.join(diag.blocking_secrets)}（削除してください）",
                    style="bold red",
                )
            else:
                notes: list[str] = []
                if diag.size_level == "warning":
                    notes.append("長文のため必要箇所の抜粋を推奨")
                elif diag.size_level == "caution":
                    notes.append("やや長文です")
                if diag.path_warnings:
                    notes.append(f"注意: {', '.join(diag.path_warnings)}を含む")
                if diag.needs_sanitization:
                    notes.append(
                        f"不可視/制御文字({diag.invisible_char_count + len(diag.control_token_hits)}件)あり"
                    )
                if diag.injection_warnings:
                    notes.append(f"外部命令表現疑い({len(diag.injection_warnings)}件)")

                if notes:
                    msg.append(f" | {' / '.join(notes)}", style="yellow")
                else:
                    msg.append(" | 診断OK", style="green")

        diag_w.update(msg)

        has_text = bool(text.strip())
        can_send = has_text and not diag.has_blocking_secrets
        self.query_one("#btn-send-once", Button).disabled = not can_send
        self.query_one("#btn-save-material", Button).disabled = not can_send

        sanitize_btn = self.query_one("#btn-sanitize", Button)
        sanitize_btn.disabled = not diag.needs_sanitization
        self.query_one("#btn-clear-text", Button).disabled = not bool(text)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "user-search-textarea":
            self._refresh_diagnosis(event.text_area.text)

    def _open_query_in_browser(self, query: str) -> None:
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        try:
            opened = webbrowser.open(url)
            if opened:
                self.app.notify("ブラウザで Google 検索を開きました。")
            else:
                self.app.notify(
                    "ブラウザを開けませんでした。[Copy] ボタンでクエリをコピーして検索してください。",
                    severity="warning",
                )
        except Exception:
            self.app.notify(
                "ブラウザの起動に失敗しました。[Copy] ボタンをご利用ください。",
                severity="warning",
            )

    def _submit_text(self, action: str) -> None:
        editor = self.query_one("#user-search-textarea", TextArea)
        text = editor.text.strip()
        if not text:
            self.app.notify("検索結果のテキストを入力してください。", severity="warning")
            editor.focus()
            return
        self._refresh_diagnosis(editor.text)
        if self._diagnosis.has_blocking_secrets:
            self.app.notify(
                "機密情報が含まれているため送信できません。該当箇所を削除してください。",
                severity="error",
            )
            editor.focus()
            return
        self.dismiss(UserSearchReply(action=action, content=text))  # type: ignore[arg-type]

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(UserSearchReply(action="abort"))
            return
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            self._submit_text("send_once")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        btn_id = event.button.id or ""

        if btn_id.startswith("btn-copy-q-"):
            idx = int(btn_id.removeprefix("btn-copy-q-")) - 1
            if 0 <= idx < len(self.request.queries):
                self.app.copy_to_clipboard(self.request.queries[idx])
                self.app.notify(f"検索クエリ {idx + 1} をクリップボードにコピーしました。")
            return

        if btn_id.startswith("btn-open-q-"):
            idx = int(btn_id.removeprefix("btn-open-q-")) - 1
            if 0 <= idx < len(self.request.queries):
                self._open_query_in_browser(self.request.queries[idx])
            return

        if btn_id == "btn-sanitize":
            editor = self.query_one("#user-search-textarea", TextArea)
            sanitized = sanitize_pasted_text(editor.text)
            editor.load_text(sanitized)
            self._refresh_diagnosis(sanitized)
            self.app.notify("不可視文字・制御トークンを除去しました。")
            editor.focus()
            return

        if btn_id == "btn-clear-text":
            editor = self.query_one("#user-search-textarea", TextArea)
            editor.load_text("")
            self._refresh_diagnosis("")
            editor.focus()
            return

        if btn_id == "btn-send-once":
            self._submit_text("send_once")
            return

        if btn_id == "btn-save-material":
            self._submit_text("save_material")
            return

        if btn_id == "btn-skip":
            self.dismiss(UserSearchReply(action="skip"))
            return

        if btn_id == "btn-abort":
            self.dismiss(UserSearchReply(action="abort"))


__all__ = [
    "UserSearchModal",
]
