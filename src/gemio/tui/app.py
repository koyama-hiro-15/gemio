"""
Gemio Textual TUI メインアプリケーション（GemioApp）モジュール。

4ゾーンレイアウト（StatusHeader / ChatTimeline / ActivityBar + PromptComposer / Footer）を構築し、
ワーカースレッドでの非同期AI実行・検索、ThreadModalBridge による同期確認モーダル連携、
およびアプリを終了させない安全なターン中断（Esc）を統括します。
"""

from typing import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer

from gemio.app import (
    ChatPromptInput,
    ClearCommand,
    CompactCommand,
    CopyCommand,
    EditModeCommand,
    ExitCommand,
    GemioController,
    HelpCommand,
    ModelCommand,
    RewindCommand,
    SandboxCommand,
    SearchCommand,
    SearchOutcome,
    TokensCommand,
    TrustModeCommand,
    parse_slash_command,
)
from gemio.core.agent import ChatTurnResult
from gemio.core.models import get_now_time_text
from gemio.project import APPROVAL_MODE_EDIT, APPROVAL_MODE_NORMAL
from gemio.tui.bridge import ThreadModalBridge
from gemio.tui.messages import (
    CancelTurnRequested,
    CopyTextRequested,
    HeaderBadgeClicked,
    PromptSubmitted,
)
from gemio.tui.widgets import (
    ActivityBar,
    ChatTimeline,
    PromptComposer,
    SearchTurnWidget,
    StatusHeader,
    TurnWidget,
)


class GemioApp(App[int]):
    """Gemio のフルスクリーン TUI メインアプリケーション。"""

    TITLE = "Gemio"
    CSS_PATH = "styles/app.tcss"

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "終了", priority=True),
        Binding("escape", "cancel_turn", "中断", show=False),
        Binding("ctrl+y", "copy_last_response", "回答コピー"),
        Binding("pageup", "scroll_timeline_up", "ログ上へ", show=False, priority=True),
        Binding("pagedown", "scroll_timeline_down", "ログ下へ", show=False, priority=True),
        Binding("f1", "show_help", "ヘルプ"),
        Binding("f2", "select_model", "モデル"),
        Binding("f3", "cycle_approval", "承認切替"),
    ]

    def __init__(
        self,
        controller: GemioController,
        *,
        approval_screen_factory: Callable[[str], ModalScreen[str]] | None = None,
        ask_user_screen_factory: Callable[
            [str, list[str] | None, str], ModalScreen[str]
        ]
        | None = None,
        iteration_limit_screen_factory: Callable[
            [], ModalScreen[tuple[bool, int]]
        ]
        | None = None,
        bind_bridge: bool = True,
    ) -> None:
        super().__init__()
        self.controller: GemioController = controller
        self.bridge = ThreadModalBridge(
            self,
            approval_screen_factory=approval_screen_factory,
            ask_user_screen_factory=ask_user_screen_factory,
            iteration_limit_screen_factory=iteration_limit_screen_factory,
            on_kill_requested=self._handle_emergency_kill,
        )
        if bind_bridge:
            self.controller.bind_interaction_handler(self.bridge)

    # =====================================================================
    # 1. レイアウト構築 & ライフサイクル
    # =====================================================================

    def compose(self) -> ComposeResult:
        initial_snap = self.controller.get_snapshot()
        yield StatusHeader(initial_snap, id="status-header")
        yield ChatTimeline(initial_snap, id="chat-timeline")
        yield ActivityBar(id="activity-bar")
        yield PromptComposer(id="prompt-composer")
        yield Footer()

    @property
    def status_header(self) -> StatusHeader:
        return self.query_one("#status-header", StatusHeader)

    @property
    def timeline(self) -> ChatTimeline:
        return self.query_one("#chat-timeline", ChatTimeline)

    @property
    def activity_bar(self) -> ActivityBar:
        return self.query_one("#activity-bar", ActivityBar)

    @property
    def composer(self) -> PromptComposer:
        return self.query_one("#prompt-composer", PromptComposer)

    def on_mount(self) -> None:
        self.composer.focus_editor()
        for warn in self.controller.startup_warnings:
            self.notify(warn, title="拡張読み込み警告", severity="warning")
        self._run_startup_onboarding()

    def _run_startup_onboarding(self) -> None:
        """起動直後に必要なモーダル（コンテナAPIキー入力・トラスト同意）を順次処理します（Phase 3で拡張）。"""
        pass

    def refresh_status(self) -> None:
        """コントローラの最新スナップショットで StatusHeader を更新します。"""
        if self.is_mounted:
            self.status_header.update_snapshot(self.controller.get_snapshot())

    def _handle_emergency_kill(self) -> None:
        """承認モーダル等で 'k' (Kill) が選択された際の緊急停止・終了処理。"""
        self.bridge.cancel_all_pending()
        self.controller.close()
        self.exit(1)

    # =====================================================================
    # 2. カスタムメッセージハンドラ
    # =====================================================================

    def on_copy_text_requested(self, message: CopyTextRequested) -> None:
        if not message.text:
            self.notify("コピー対象のテキストが空です。", severity="warning")
            return
        self.copy_to_clipboard(message.text)
        self.notify(f"{message.label}をクリップボードにコピーしました。")

    def on_cancel_turn_requested(self, message: CancelTurnRequested) -> None:
        self.action_cancel_turn()

    def on_header_badge_clicked(self, message: HeaderBadgeClicked) -> None:
        if message.target == "sandbox":
            self.action_show_sandbox()
        elif message.target == "approval":
            self.action_cycle_approval()
        elif message.target == "model":
            self.action_select_model()
        elif message.target == "tokens":
            self.action_show_tokens()

    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        parsed = parse_slash_command(message.text)
        if parsed is None:
            return

        if isinstance(parsed, ExitCommand):
            self.composer.clear_after_accept()
            self.action_quit_app()
            return

        # 実行中でも安全に参照できる読み取り専用コマンド
        if isinstance(parsed, HelpCommand):
            self.composer.clear_after_accept()
            self.action_show_help()
            return
        if isinstance(parsed, TokensCommand):
            self.composer.clear_after_accept()
            self.action_show_tokens()
            return
        if isinstance(parsed, CopyCommand):
            self.composer.clear_after_accept()
            self.action_copy_last_response()
            return

        if self.controller.is_busy:
            self.notify(
                "現在AIが処理中です（現在のターンを中断するには Esc を押してください）。",
                severity="warning",
            )
            return

        self.composer.clear_after_accept()

        if isinstance(parsed, SandboxCommand):
            self.action_show_sandbox()
        elif isinstance(parsed, ClearCommand):
            self._execute_clear_session(clear_materials=parsed.clear_materials)
        elif isinstance(parsed, EditModeCommand):
            self._execute_edit_mode(parsed.enable)
        elif isinstance(parsed, TrustModeCommand):
            self._execute_trust_mode(parsed.enable)
        elif isinstance(parsed, ModelCommand):
            if parsed.target_model:
                new_model = self.controller.set_model(parsed.target_model)
                self.refresh_status()
                self.notify(f"AIモデルを切り替えました: {new_model}")
            else:
                self.action_select_model()
        elif isinstance(parsed, RewindCommand):
            self._execute_rewind(parsed.steps)
        elif isinstance(parsed, CompactCommand):
            self._start_compact_worker(parsed.keep_recent_turns)
        elif isinstance(parsed, SearchCommand):
            if parsed.clear_materials:
                cleared = self.controller.clear_search_materials()
                self.refresh_status()
                self.notify(
                    f"保持していた作業前提資料（{cleared}件）を破棄しました。"
                )
            elif parsed.show_help:
                self.notify(
                    "使い方: /search <調べたい内容> (-n: 新規検索 / -c: 資料破棄)",
                    title="Web検索 (/search)",
                )
            else:
                self._start_search_worker(
                    parsed.query,
                    new_session=parsed.new_session,
                )
        elif isinstance(parsed, ChatPromptInput):
            self._start_chat_worker(parsed.text)

    # =====================================================================
    # 3. 状態変更・セッション操作ヘルパー
    # =====================================================================

    def _execute_clear_session(self, clear_materials: bool = False) -> None:
        summary = self.controller.clear_session(clear_materials=clear_materials)
        snap = self.controller.get_snapshot()
        self.status_header.update_snapshot(snap)
        self.timeline.reset_timeline(snap)
        mat_note = (
            f"前提資料 {summary['cleared_materials']}件も破棄"
            if clear_materials
            else f"前提資料 {summary['retained_materials']}件を維持"
        )
        self.notify(
            f"対話セッションをリセットしました（消去履歴: {summary['cleared_messages']}件 / {mat_note}）。"
        )

    def _execute_edit_mode(self, enable: bool | None = None) -> None:
        enabled, changed = self.controller.set_edit_mode(enable)
        self.refresh_status()
        if not changed:
            label = "エディットモード" if enabled else "通常承認モード"
            self.notify(f"現在はすでに {label} です。")
            return
        if enabled:
            self.notify(
                "エディットモードを有効化しました（作成・編集は自動承認 / 削除・コマンドは都度確認）。",
                severity="warning",
            )
        else:
            self.notify("通常承認モード（都度確認）に戻しました。")

    def _execute_trust_mode(self, enable: bool | None = None) -> None:
        target = (not self.controller.project.trust_mode) if enable is None else enable
        if not target:
            _, changed = self.controller.set_trust_mode(False)
            self.refresh_status()
            if changed:
                self.notify("通常承認モード（都度確認）に戻しました。")
            else:
                self.notify("現在はすでに通常承認モードです。")
            return

        if self.controller.project.trust_mode:
            self.notify("現在はすでにトラストモードです。")
            return

        self._request_trust_mode_activation()

    def _request_trust_mode_activation(self) -> None:
        """トラストモード有効化前の同意確認を行います（Phase 3 で TrustConsentModal を接続）。"""
        self.notify(
            "トラストモード同意確認モーダルは Phase 3 で接続されます。",
            severity="warning",
        )

    def _execute_rewind(self, steps: int = 1) -> None:
        removed_turns = self.controller.rewind_turns(steps=steps)
        if not removed_turns:
            self.notify("巻き戻せる対話ターンがありません。", severity="warning")
            return
        self.timeline.remove_last_turns(len(removed_turns))
        self.refresh_status()
        self.notify(f"直近 {len(removed_turns)} ターンを巻き戻しました。")

    # =====================================================================
    # 4. 非同期ワーカー実行（通常対話 / Web検索 / コンテキスト圧縮）
    # =====================================================================

    def _start_chat_worker(self, prompt_text: str) -> None:
        if not self.controller.is_client_ready:
            self.notify(
                "APIキーが設定されていません。先にAPIキーを入力してください。",
                severity="error",
            )
            return

        turn_widget = TurnWidget(prompt_text)
        self.timeline.mount_entry(turn_widget, force_scroll=True)
        self.activity_bar.start_busy("Gemioが思考・実行中...")
        self._run_chat_in_thread(prompt_text, turn_widget)

    @work(thread=True, exclusive=True, group="ai_execution")
    def _run_chat_in_thread(
        self,
        prompt_text: str,
        turn_widget: TurnWidget,
    ) -> None:
        def _on_log(message: str, category: str = "assistant") -> None:
            self.call_from_thread(
                self._apply_turn_log_on_main,
                turn_widget,
                message,
                category,
            )

        try:
            turn_result = self.controller.run_chat_turn(
                prompt_text,
                on_log=_on_log,
            )
            self.call_from_thread(
                self._finalize_turn_on_main,
                turn_widget,
                turn_result,
            )
        except Exception as err:
            self.call_from_thread(
                self._handle_worker_error_on_main,
                turn_widget,
                err,
            )

    def _apply_turn_log_on_main(
        self,
        turn_widget: TurnWidget,
        message: str,
        category: str,
    ) -> None:
        tool_name = turn_widget.append_agent_log(message, category)
        if tool_name:
            self.activity_bar.update_detail(f"Tool: {tool_name}")
        self.refresh_status()
        self.timeline.follow_if_near_bottom()

    def _finalize_turn_on_main(
        self,
        turn_widget: TurnWidget,
        turn_result: ChatTurnResult,
    ) -> None:
        turn_widget.finalize_turn(
            turn_id=turn_result.turn.turn_id,
            response_text=turn_result.response_text,
            thoughts=turn_result.turn.thoughts,
            aborted=turn_result.aborted,
        )
        self.activity_bar.stop_busy()
        self.refresh_status()
        self.timeline.follow_if_near_bottom()

    def _handle_worker_error_on_main(
        self,
        turn_widget: TurnWidget,
        error: Exception,
    ) -> None:
        err_msg = (
            f"**予期せぬエラーが発生しました:** `{error}`\n\n"
            "直前までのツール実行結果などの作業状態は維持されています。"
        )
        turn_widget.assistant_card.set_response(
            err_msg,
            timestamp=get_now_time_text(),
            aborted=True,
        )
        self.activity_bar.stop_busy()
        self.refresh_status()
        self.timeline.follow_if_near_bottom()
        self.notify(f"エラーが発生しました: {error}", severity="error")

    def _start_search_worker(self, query: str, *, new_session: bool = False) -> None:
        if not self.controller.is_client_ready:
            self.notify(
                "APIキーが設定されていません。先にAPIキーを入力してください。",
                severity="error",
            )
            return

        is_subsequent = self.controller.has_active_search_session and not new_session
        search_widget = SearchTurnWidget(query, is_subsequent=is_subsequent)
        self.timeline.mount_entry(search_widget, force_scroll=True)
        busy_label = (
            "直前の検索を踏まえて深掘り検索中..."
            if is_subsequent
            else "Webを検索して最新情報を取得中..."
        )
        self.activity_bar.start_busy(busy_label)
        self._run_search_in_thread(query, new_session, search_widget)

    @work(thread=True, exclusive=True, group="ai_execution")
    def _run_search_in_thread(
        self,
        query: str,
        new_session: bool,
        search_widget: SearchTurnWidget,
    ) -> None:
        try:
            outcome = self.controller.run_search(query, new_session=new_session)
            self.call_from_thread(
                self._finalize_search_on_main,
                search_widget,
                outcome,
            )
        except Exception as err:
            self.call_from_thread(
                self._handle_search_exception_on_main,
                search_widget,
                err,
            )

    def _finalize_search_on_main(
        self,
        search_widget: SearchTurnWidget,
        outcome: SearchOutcome,
    ) -> None:
        search_widget.set_outcome(outcome)
        self.activity_bar.stop_busy()
        self.refresh_status()
        self.timeline.follow_if_near_bottom()

    def _handle_search_exception_on_main(
        self,
        search_widget: SearchTurnWidget,
        error: Exception,
    ) -> None:
        from gemio.core.search import SearchResult

        fallback = SearchOutcome(
            result=SearchResult(
                query=search_widget.query,
                response_text=f"Web検索中に予期せぬエラーが発生しました: {error}",
                sources=[],
                created_at=get_now_time_text(),
                succeeded=False,
            ),
            material_status="failed",
            total_materials=len(self.controller.chat_session.materials),
        )
        search_widget.set_outcome(fallback)
        self.activity_bar.stop_busy()
        self.refresh_status()
        self.timeline.follow_if_near_bottom()

    def _start_compact_worker(self, keep_recent_turns: int) -> None:
        if not self.controller.is_client_ready:
            self.notify("APIキーが設定されていません。", severity="error")
            return
        self.activity_bar.start_busy("古い対話履歴を構造化要約へ圧縮中...")
        self._run_compact_in_thread(keep_recent_turns)

    @work(thread=True, exclusive=True, group="ai_execution")
    def _run_compact_in_thread(self, keep_recent_turns: int) -> None:
        try:
            comp_res = self.controller.compact_context(
                keep_recent_turns=keep_recent_turns
            )
            self.call_from_thread(self._finalize_compact_on_main, comp_res)
        except Exception as err:
            self.call_from_thread(self._handle_compact_error_on_main, err)

    def _finalize_compact_on_main(self, comp_res) -> None:
        self.activity_bar.stop_busy()
        self.refresh_status()
        if not comp_res.compacted:
            self.notify(comp_res.reason or "圧縮対象のターンがありません。")
            return
        self.notify(
            f"古い {comp_res.summarized_turns} ターンを要約圧縮しました（直近 {comp_res.retained_turns} ターンを保持）。"
        )

    def _handle_compact_error_on_main(self, error: Exception) -> None:
        self.activity_bar.stop_busy()
        self.notify(f"コンテキスト圧縮中にエラーが発生しました: {error}", severity="error")

    # =====================================================================
    # 5. キーバインドアクション群
    # =====================================================================

    def action_cancel_turn(self) -> None:
        """AI実行中のみ、アプリを終了させずに現在のターンと子プロセスを中断します。"""
        if len(self.screen_stack) > 1:
            return
        if self.controller.is_busy:
            self.bridge.cancel_all_pending()
            self.controller.cancel_active_execution()
            self.notify(
                "現在のターンを中断しています...",
                severity="warning",
            )

    def action_copy_last_response(self) -> None:
        """直近のAI回答をクリップボードへコピーします。"""
        last_resp = self.controller.get_last_assistant_response()
        if not last_resp:
            self.notify("コピー可能な直近のAI回答がありません。", severity="warning")
            return
        self.copy_to_clipboard(last_resp)
        self.notify("直近のAI回答をクリップボードにコピーしました。")

    def action_scroll_timeline_up(self) -> None:
        """入力欄にフォーカスしたままタイムラインを1ページ上へスクロールします。"""
        self.timeline.scroll_page_up(animate=False)

    def action_scroll_timeline_down(self) -> None:
        """入力欄にフォーカスしたままタイムラインを1ページ下へスクロールします。"""
        self.timeline.scroll_page_down(animate=False)

    def action_cycle_approval(self) -> None:
        """承認モードを切り替えます（通常 ⇄ エディット。トラスト中なら通常へ戻す）。"""
        curr = self.controller.project.approval_mode
        if curr == APPROVAL_MODE_NORMAL:
            self._execute_edit_mode(True)
        else:
            self.controller.set_approval_mode(APPROVAL_MODE_NORMAL)
            self.refresh_status()
            self.notify("通常承認モード（都度確認）に切り替えました。")

    def action_show_help(self) -> None:
        """ヘルプ画面を開きます（Phase 3 で HelpModal を接続）。"""
        self.notify("ヘルプ: Ctrl+J/Alt+Enter=送信, Alt+Backspace=全消去, Esc=中断, Ctrl+Q=終了")

    def action_select_model(self) -> None:
        """モデル選択画面を開きます（Phase 3 で ModelSelectModal を接続）。"""
        self.notify(f"現在のモデル: {self.controller.current_model} (/model <名前> で変更可)")

    def action_show_tokens(self) -> None:
        """トークン診断画面を開きます（Phase 3 で TokenUsageModal を接続）。"""
        usage = self.controller.get_token_usage()
        ctx = usage.get("last_prompt_tokens", 0)
        cum = usage.get("cumulative_total_tokens", 0)
        self.notify(f"Tokens — Context: {ctx:,} / Cumulative: {cum:,}")

    def action_show_sandbox(self) -> None:
        """サンドボックス診断画面を開きます（Phase 3 で SandboxStatusModal を接続）。"""
        snap = self.controller.get_snapshot()
        self.notify(f"動作モード: {snap.mode_display_label}")

    def action_quit_app(self) -> None:
        """稼働中の子プロセスや待機モーダルをクリーンアップして TUI を終了します。"""
        self.bridge.cancel_all_pending()
        self.controller.close()
        self.exit(0)


__all__ = [
    "GemioApp",
]
