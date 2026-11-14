import contextlib
import sys
from pathlib import Path
from typing import Callable, Iterator

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.markup import escape
from rich.status import Status

from gemio.cli.ui.base import GemioUI
from gemio.cli.ui.prompts import (
    create_prompt_session,
    prompt_api_key_for_container,
    prompt_approval_choice,
    prompt_ask_user_response,
    prompt_iteration_limit_continue,
    prompt_model_selection,
    prompt_user_input,
    request_trust_mode_consent,
)
from gemio.cli.ui.renderables import (
    build_agent_log_panel,
    build_edit_mode_changed_panel,
    build_help_panel,
    build_model_changed_panel,
    build_sandbox_security_error_panel,
    build_sandbox_status_panel,
    build_search_error_panel,
    build_search_help_panel,
    build_search_materials_cleared_panel,
    build_search_response_panel,
    build_session_reset_panel,
    build_token_usage_panel,
    build_trust_mode_changed_panel,
    build_unexpected_error_panel,
    build_welcome_panel,
    classify_agent_log,
    format_dev_mode_label,
)
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
    Project,
)


class RichConsoleUI(GemioUI):
    """
    Rich および prompt_toolkit を使用した標準コンソール向けUI実装クラス。

    renderables.py の純粋パネル生成関数と prompts.py の対話入力関数を組み合わせ、
    スピナー（_active_status）のライフサイクル管理・一時停止をUI層内部で完結させます。
    """

    def __init__(self, console: Console | None = None):
        self.console: Console = console or Console()
        self._active_status: Status | None = None
        self.last_log_category: str | None = None

    # =====================================================================
    # 1. ステータス・スピナー制御
    # =====================================================================

    def create_thinking_spinner(self) -> Status:
        """AI思考・ツール実行中のスピナー（Statusオブジェクト）を生成します。"""
        return self.console.status("[bold green]🤖 Gemioが思考・実行中...[/bold green]", spinner="dots")

    def create_search_spinner(self, is_subsequent: bool = False) -> Status:
        """Web検索中のスピナー（Statusオブジェクト）を生成します。"""
        status_msg = (
            "[bold green]🔍 直前の検索結果を踏まえて深掘り検索中...[/bold green]"
            if is_subsequent
            else "[bold green]🔍 Webを検索して最新情報を取得中...[/bold green]"
        )
        return self.console.status(status_msg, spinner="dots")

    @contextlib.contextmanager
    def thinking_status(self) -> Iterator[Status]:
        """AI思考・ツール実行中のスピナーを開始し、終了時に自動でクリアします。"""
        spinner = self.create_thinking_spinner()
        self._active_status = spinner
        try:
            with spinner:
                yield spinner
        finally:
            self._active_status = None

    @contextlib.contextmanager
    def search_status(self, is_subsequent: bool = False) -> Iterator[Status]:
        """Web検索中のスピナーを開始し、終了時に自動でクリアします。"""
        spinner = self.create_search_spinner(is_subsequent=is_subsequent)
        self._active_status = spinner
        try:
            with spinner:
                yield spinner
        finally:
            self._active_status = None

    @contextlib.contextmanager
    def pause_status(self) -> Iterator[None]:
        """対話プロンプト表示中にスピナー（_active_status）を一時停止・再開します。"""
        if self._active_status:
            self._active_status.stop()
        interrupted = False
        try:
            yield
        except KeyboardInterrupt:
            interrupted = True
            raise
        finally:
            if not interrupted and self._active_status:
                self._active_status.start()

    # =====================================================================
    # 2. ラベル整形・単発メッセージ出力
    # =====================================================================

    def format_dev_mode_label(self, project: Project) -> str:
        return format_dev_mode_label(project)

    def print_cli_error(self, message: str) -> None:
        self.console.print(f"[bold red]{escape(message)}[/bold red]", file=sys.stderr)

    def print_dim_message(self, message: str) -> None:
        self.console.print(f"[dim]{escape(message)}[/dim]")

    def print_exit_message(self) -> None:
        self.console.print("[bold yellow]終了します。またのご利用をお待ちしております！[/bold yellow]")

    def print_interrupt_exit_message(self) -> None:
        self.console.print("\n[bold yellow]⚡ 中断シグナル (Ctrl+C) を検知したため、処理を停止して終了します。[/bold yellow]")

    def print_chat_divider(self) -> None:
        self.console.print("[dim]──────────────────────────────────────────────────[/dim]")

    def print_initial_approval_mode_notice(
        self,
        mode: str,
        fallback_from_trust: bool = False,
    ) -> None:
        if mode == APPROVAL_MODE_TRUST:
            self.console.print("[bold green]✓ トラストモードを有効化しました。[/bold green]\n")
        elif mode == APPROVAL_MODE_EDIT:
            if fallback_from_trust:
                self.console.print(
                    "[bold yellow]トラストモードの有効化をキャンセルし、エディットモードで起動します"
                    "（ファイル作成・編集は自動実行 / ファイル削除・コマンド実行は都度確認）。[/bold yellow]\n"
                )
            else:
                self.console.print(
                    "[bold yellow]✓ エディットモードを有効化しました"
                    "（ファイル作成・編集は自動実行 / ファイル削除・コマンド実行は都度確認）。[/bold yellow]\n"
                )
        elif fallback_from_trust:
            self.console.print("[yellow]トラストモードの有効化をキャンセルし、通常承認モードで起動します。[/yellow]\n")

    # =====================================================================
    # 3. 情報・設定変更パネル表示
    # =====================================================================

    def print_welcome_banner(
        self,
        project_path: Path,
        current_model: str,
        trust_mode: bool = False,
        dev_mode_label: str = "基本モード (Standard)",
        approval_mode: str | None = None,
    ) -> None:
        self.console.print(
            build_welcome_panel(
                project_path=project_path,
                current_model=current_model,
                trust_mode=trust_mode,
                dev_mode_label=dev_mode_label,
                approval_mode=approval_mode,
            )
        )

    def print_help_panel(self) -> None:
        self.console.print(build_help_panel())

    def print_sandbox_status_panel(self, project: Project) -> None:
        self.console.print(build_sandbox_status_panel(project))

    def print_token_usage_panel(self, token_usage: dict[str, int]) -> None:
        self.console.print(build_token_usage_panel(token_usage))

    def print_session_reset_panel(
        self,
        reset_summary: dict[str, int],
        clear_materials: bool = False,
    ) -> None:
        self.console.print(build_session_reset_panel(reset_summary, clear_materials=clear_materials))

    def print_edit_mode_changed_panel(self, enabled: bool) -> None:
        self.console.print(build_edit_mode_changed_panel(enabled))

    def print_trust_mode_changed_panel(self, enabled: bool) -> None:
        self.console.print(build_trust_mode_changed_panel(enabled))

    def print_model_changed_panel(self, model_name: str) -> None:
        self.console.print(build_model_changed_panel(model_name))

    # =====================================================================
    # 4. 通常対話ログ & エラー表示
    # =====================================================================

    def print_agent_log_panel(
        self,
        log_message: str,
        category: str | None = None,
    ) -> None:
        resolved_category = category or classify_agent_log(log_message)
        self.last_log_category = resolved_category
        self.console.print(build_agent_log_panel(log_message, category=resolved_category))

    def print_sandbox_security_error_panel(self, error: Exception) -> None:
        self.console.print(build_sandbox_security_error_panel(error))

    def print_unexpected_error_panel(self, error: Exception) -> None:
        self.console.print(build_unexpected_error_panel(error))

    # =====================================================================
    # 5. 検索対話表示 & エラー表示
    # =====================================================================

    def print_search_materials_cleared_panel(self, cleared_count: int) -> None:
        self.console.print(build_search_materials_cleared_panel(cleared_count))

    def print_search_help_panel(self) -> None:
        self.console.print(build_search_help_panel())

    def print_search_response_panel(
        self,
        response_text: str,
        sources: list[dict[str, str]],
        now_time_text: str,
        total_materials: int,
    ) -> None:
        panel = build_search_response_panel(
            response_text=response_text,
            sources=sources,
            now_time_text=now_time_text,
        )
        if panel is not None:
            self.console.print(panel)

        self.console.print("[dim]──────────────────────────────────────────────────[/dim]")
        self.console.print(
            f"[dim]💡 検索結果を作業前提資料 [第{total_materials}弾] としてセッションに保持しました。"
            f"以降の通常プロンプトの前提資料として自動参照されます。（破棄: /search --clear）[/dim]\n"
        )

    def print_search_error_panel(self, error: Exception) -> None:
        self.console.print(build_search_error_panel(error))

    # =====================================================================
    # 6. 対話的確認・選択・プロンプト入力（スピナー自動一時停止付き）
    # =====================================================================

    def create_prompt_session(self) -> PromptSession:
        return create_prompt_session()

    def prompt_user_input(
        self,
        session: PromptSession,
        project: Project,
        token_usage: dict[str, int] | None = None,
    ) -> str:
        return prompt_user_input(session, project, token_usage)

    def request_trust_mode_consent(self) -> bool:
        with self.pause_status():
            return request_trust_mode_consent(console=self.console)

    def prompt_api_key_for_container(self, prompt_fn: Callable | None = None) -> str:
        with self.pause_status():
            return prompt_api_key_for_container(prompt_fn=prompt_fn, console=self.console)

    def prompt_approval_choice(self, action_description: str) -> str:
        with self.pause_status():
            return prompt_approval_choice(action_description, console=self.console)

    def prompt_iteration_limit_continue(self) -> tuple[bool, int]:
        with self.pause_status():
            return prompt_iteration_limit_continue(console=self.console)

    def prompt_ask_user_response(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        with self.pause_status():
            return prompt_ask_user_response(
                question,
                options,
                context,
                console=self.console,
            )

    def prompt_model_selection(self, current_model: str) -> str | None:
        with self.pause_status():
            return prompt_model_selection(current_model, console=self.console)
