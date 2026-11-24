"""
Gemio Textual TUI メインアプリケーション（GemioApp）モジュール。

4ゾーンレイアウト（StatusHeader / ChatTimeline / ActivityBar + PromptComposer / Footer）、
ModalScreen 群、コマンドパレット (Ctrl+P)、ワーカースレッド同期ブリッジ、
およびアプリを終了させない安全なターン中断（Esc）を統合管理します。
"""

from typing import Any, Callable

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
    MaterialsCommand,
    ModelCommand,
    RewindCommand,
    SandboxCommand,
    TokensCommand,
    TrustModeCommand,
    parse_slash_command,
)
from gemio.core.agent import ChatTurnResult
from gemio.core.models import get_now_time_text
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_TRUST,
)
from gemio.tui.bridge import ThreadModalBridge
from gemio.tui.commands_palette import GemioCommandProvider
from gemio.tui.messages import (
    CancelTurnRequested,
    CopyTextRequested,
    HeaderBadgeClicked,
    PromptSubmitted,
)
from gemio.tui.screens import (
    ApiKeyModal,
    ApprovalModal,
    AskUserModal,
    HelpModal,
    IterationLimitModal,
    MaterialsModal,
    ModelSelectModal,
    SandboxStatusModal,
    TokenUsageModal,
    TrustConsentModal,
)
from gemio.tui.widgets import (
    ActivityBar,
    ChatTimeline,
    PromptComposer,
    StatusHeader,
    TurnWidget,
    WelcomeBanner,
)


class GemioApp(App[int]):
    """Gemio のフルスクリーン TUI メインアプリケーション。"""

    TITLE = "Gemio"
    CSS_PATH = "styles/app.tcss"
    COMMANDS = App.COMMANDS | {GemioCommandProvider}

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "終了", priority=True),
        Binding("escape", "cancel_turn", "中断", show=False),
        Binding("pageup", "scroll_timeline_up", "ログ上へ", show=False, priority=True),
        Binding("pagedown", "scroll_timeline_down", "ログ下へ", show=False, priority=True),
        Binding("f1", "show_help", "ヘルプ"),
        Binding("f2", "select_model", "モデル"),
        Binding("f3", "cycle_approval", "承認切替"),
        Binding("f4", "focus_composer", "入力欄"),
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
        run_initial_sandbox_probe: bool = True,
    ) -> None:
        super().__init__()
        self.controller: GemioController = controller
        self._run_initial_sandbox_probe = run_initial_sandbox_probe
        self.bridge = ThreadModalBridge(
            self,
            approval_screen_factory=approval_screen_factory or ApprovalModal,
            ask_user_screen_factory=ask_user_screen_factory or AskUserModal,
            iteration_limit_screen_factory=(
                iteration_limit_screen_factory or IterationLimitModal
            ),
            on_kill_requested=self._handle_emergency_kill,
        )
        if bind_bridge:
            self.controller.bind_interaction_handler(self.bridge)

    # =====================================================================
    # 1. レイアウト構築 & 起動オンボーディングチェーン
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
        """起動直後に必要なモーダル（コンテナAPIキー入力 ➔ トラスト同意 ➔ サンドボックス検査）を順次実行します。"""
        if self.controller.needs_api_key_input:
            self.push_screen(
                ApiKeyModal(),
                callback=self._on_startup_api_key_entered,
            )
            return
        self._continue_onboarding_trust_consent()

    def _on_startup_api_key_entered(self, entered_key: str | None) -> None:
        if not entered_key:
            self.controller.close()
            self.exit(result=1, return_code=1)
            return
        self.controller.set_api_key(entered_key)
        self.refresh_status_and_welcome()
        self.notify("APIキーを受け付けました。")
        self._continue_onboarding_trust_consent()

    def _continue_onboarding_trust_consent(self) -> None:
        if self.controller.needs_initial_trust_consent:
            self.push_screen(
                TrustConsentModal(),
                callback=self._on_startup_trust_consented,
            )
            return
        self._continue_onboarding_sandbox_probe()

    def _on_startup_trust_consented(self, consented: bool | None) -> None:
        mode, fallback = self.controller.apply_initial_trust_consent(
            bool(consented)
        )
        self.refresh_status_and_welcome()
        if mode == APPROVAL_MODE_TRUST:
            self.notify(
                "トラストモード（自動承認）を有効化しました。",
                severity="warning",
            )
        elif fallback and mode == APPROVAL_MODE_EDIT:
            self.notify(
                "トラストモードをキャンセルし、エディットモードで起動しました。"
            )
        elif fallback:
            self.notify(
                "トラストモードをキャンセルし、通常承認モードで起動しました。"
            )
        self._continue_onboarding_sandbox_probe()

    def _continue_onboarding_sandbox_probe(self) -> None:
        self.composer.focus_editor()
        if self.controller.config.dev_mode and self._run_initial_sandbox_probe:
            self._probe_startup_sandbox_in_thread()

    @work(thread=True, group="sandbox_probe")
    def _probe_startup_sandbox_in_thread(self) -> None:
        try:
            status = self.controller.inspect_sandbox()
            self.call_from_thread(self._on_startup_sandbox_probed, status)
        except Exception:
            pass

    def _on_startup_sandbox_probed(self, status: dict[str, Any]) -> None:
        warnings = status.get("warnings") or []
        danger_cnt = sum(1 for w in warnings if w.get("level") == "danger")
        warn_cnt = sum(1 for w in warnings if w.get("level") == "warning")

        if danger_cnt > 0:
            # 機密キー露出や未マスク機密ファイルがある場合は即座に診断モーダルを開いて警告
            self.push_screen(SandboxStatusModal(status))
        elif warn_cnt > 0:
            self.notify(
                f"サンドボックス注意項目が {warn_cnt} 件あります（詳細: /sandbox）。",
                severity="warning",
            )

    def refresh_status(self) -> None:
        """コントローラの最新スナップショットで StatusHeader を更新します。"""
        if self.is_mounted:
            self.status_header.update_snapshot(self.controller.get_snapshot())

    def refresh_status_and_welcome(self) -> None:
        """StatusHeader とタイムライン先頭の WelcomeBanner をともに最新スナップショットで更新します。"""
        if not self.is_mounted:
            return
        snap = self.controller.get_snapshot()
        self.status_header.update_snapshot(snap)
        for wb in self.timeline.query(WelcomeBanner):
            wb.update_snapshot(snap)

    def _handle_emergency_kill(self) -> None:
        """承認モーダル等で 'k' (Kill) が選択された際の緊急停止・終了処理。"""
        self.bridge.cancel_all_pending()
        self.controller.close()
        self.exit(result=1, return_code=1)

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
                self._switch_model_directly(parsed.target_model)
            else:
                self.action_select_model()
        elif isinstance(parsed, RewindCommand):
            self._execute_rewind(parsed.steps)
        elif isinstance(parsed, CompactCommand):
            self._start_compact_worker(parsed.keep_recent_turns)
        elif isinstance(parsed, MaterialsCommand):
            if parsed.action == "clear":
                self._clear_materials_action()
            elif parsed.action == "remove":
                self._remove_material_action(parsed.target)
            else:
                self.action_show_materials()
        elif isinstance(parsed, ChatPromptInput):
            self._start_chat_worker(parsed.text)

    # =====================================================================
    # 3. 状態変更・セッション・作業前提資料操作ヘルパー
    # =====================================================================

    def _switch_model_directly(self, target_model: str) -> None:
        new_model = self.controller.set_model(target_model)
        self.refresh_status_and_welcome()
        self.notify(f"AIモデルを切り替えました: {new_model}")

    def _clear_materials_action(self) -> None:
        if self.controller.is_busy:
            self.notify("AI処理中は資料をクリアできません。", severity="warning")
            return
        cleared = self.controller.clear_materials()
        self.refresh_status()
        self.notify(f"保持していた作業前提資料（{cleared}件）を破棄しました。")

    def _remove_material_action(self, target: str) -> None:
        if self.controller.is_busy:
            self.notify("AI処理中は資料を削除できません。", severity="warning")
            return
        removed = self.controller.remove_material(target)
        if removed is None:
            self.notify(
                f"指定された資料 '{target}' が見つかりませんでした。",
                severity="warning",
            )
            return
        self.refresh_status()
        self.notify(f"作業前提資料 '{removed.material_id}: {removed.title}' を削除しました。")

    def _set_normal_approval_mode(self) -> None:
        _, changed = self.controller.set_approval_mode(APPROVAL_MODE_NORMAL)
        self.refresh_status_and_welcome()
        if changed:
            self.notify("通常承認モード（都度確認）に戻しました。")
        else:
            self.notify("現在はすでに通常承認モードです。")

    def _execute_clear_session(self, clear_materials: bool = False) -> None:
        if self.controller.is_busy:
            self.notify("AI処理中はセッションをクリアできません。", severity="warning")
            return
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
        self.refresh_status_and_welcome()
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
            self.refresh_status_and_welcome()
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
        self.push_screen(
            TrustConsentModal(),
            callback=self._on_runtime_trust_consent,
        )

    def _on_runtime_trust_consent(self, consented: bool | None) -> None:
        if consented:
            self.controller.set_trust_mode(True)
            self.refresh_status_and_welcome()
            self.notify(
                "トラストモード（自動承認）を有効化しました。",
                severity="warning",
            )
        else:
            self.notify("トラストモードへの切り替えをキャンセルしました。")
        self.composer.focus_editor()

    def _execute_rewind(self, steps: int = 1) -> None:
        if self.controller.is_busy:
            self.notify("AI処理中は巻き戻しできません。", severity="warning")
            return
        removed_turns = self.controller.rewind_turns(steps=steps)
        if not removed_turns:
            self.notify("巻き戻せる対話ターンがありません。", severity="warning")
            return
        self.timeline.remove_last_turns(len(removed_turns))
        self.refresh_status()
        self.notify(f"直近 {len(removed_turns)} ターンを巻き戻しました。")

    # =====================================================================
    # 4. 非同期ワーカー実行（通常対話 / コンテキスト圧縮 / 診断）
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

    def _start_compact_worker(self, keep_recent_turns: int) -> None:
        if self.controller.is_busy:
            self.notify("AI処理中はコンテキスト圧縮を実行できません。", severity="warning")
            return
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
        self.notify(
            f"コンテキスト圧縮中にエラーが発生しました: {error}",
            severity="error",
        )

    @work(thread=True, exclusive=True, group="sandbox_inspect")
    def _inspect_sandbox_for_modal_in_thread(self) -> None:
        try:
            status = self.controller.inspect_sandbox()
            self.call_from_thread(
                lambda st: self.push_screen(SandboxStatusModal(st)),
                status,
            )
        except Exception as err:
            self.call_from_thread(
                self.notify,
                f"サンドボックス診断エラー: {err}",
                severity="error",
            )

    # =====================================================================
    # 5. キーバインド・モーダルアクション群
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
        if len(self.screen_stack) > 1:
            return
        last_resp = self.controller.get_last_assistant_response()
        if not last_resp:
            self.notify("コピー可能な直近のAI回答がありません。", severity="warning")
            return
        self.copy_to_clipboard(last_resp)
        self.notify("直近のAI回答をクリップボードにコピーしました。")

    def action_focus_composer(self) -> None:
        """プロンプト入力エディタへフォーカスを移動します。"""
        if len(self.screen_stack) > 1:
            return
        self.composer.focus_editor()

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
            self._set_normal_approval_mode()

    def action_show_help(self) -> None:
        """ヘルプモーダルを開きます。"""
        self.push_screen(HelpModal())

    def action_show_materials(self) -> None:
        """作業前提資料管理モーダルを開きます。"""
        if self.controller.is_busy:
            self.notify("AI処理中は資料管理を開けません。", severity="warning")
            return
        self.push_screen(
            MaterialsModal(
                list_callback=self.controller.list_materials,
                add_callback=self.controller.add_user_material,
                remove_callback=self.controller.remove_material,
                clear_callback=self.controller.clear_materials,
                forbidden_paths=self.controller.get_forbidden_paths_for_diagnosis(),
            ),
            callback=self._on_materials_modal_closed,
        )

    def _on_materials_modal_closed(self, _changed: bool | None) -> None:
        self.refresh_status()
        self.composer.focus_editor()

    def action_select_model(self) -> None:
        """モデル選択モーダルを開きます。"""
        self.push_screen(
            ModelSelectModal(
                self.controller.current_model,
                models=self.controller.get_cached_models(),
                fetch_callback=lambda force: self.controller.fetch_available_models(
                    force_refresh=force
                ),
                auto_fetch=(
                    self.controller.is_client_ready
                    and not self.controller.has_cached_models
                ),
            ),
            callback=self._on_model_selected,
        )

    def _on_model_selected(self, selected: str | None) -> None:
        if selected:
            self._switch_model_directly(selected)
        self.composer.focus_editor()

    def action_show_tokens(self) -> None:
        """トークン使用量・コンテキスト診断モーダルを開きます。"""
        self.push_screen(
            TokenUsageModal(self.controller.get_token_usage()),
            callback=self._on_token_modal_closed,
        )

    def _on_token_modal_closed(self, action: str | None) -> None:
        if action == "materials":
            self.action_show_materials()
            return
        if action == "clear":
            self._execute_clear_session(clear_materials=False)
        elif action == "clear_all":
            self._execute_clear_session(clear_materials=True)
        self.composer.focus_editor()

    def action_show_sandbox(self) -> None:
        """サンドボックス・ワークスペース保護診断モーダルを開きます。"""
        if not self.controller.config.dev_mode:
            self.push_screen(
                SandboxStatusModal(self.controller.inspect_sandbox())
            )
            return
        self._inspect_sandbox_for_modal_in_thread()

    def action_quit_app(self) -> None:
        """稼働中の子プロセスや待機モーダルをクリーンアップして TUI を終了します。"""
        self.bridge.cancel_all_pending()
        self.controller.close()
        self.exit(result=0, return_code=0)


__all__ = [
    "GemioApp",
]
