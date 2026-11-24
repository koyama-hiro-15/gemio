"""
Gemio アプリケーション制御層の中核コントローラ（GemioController）モジュール。

UI フレームワーク（Textual 等）に一切依存せず、Project / DeveloperProject、
ToolRegistry / ToolProvider、SkillManager、SystemPromptComposer、GenAIClient、
ChatSession、ChatAgent の結線および型付きユースケースAPIを提供します。
"""

import os
from pathlib import Path
import threading
from typing import Any, Callable

from gemio.app.callbacks import InteractionHandler
from gemio.app.config import AppConfig
from gemio.app.prompts import APP_MATERIALS_GUIDANCE_INSTRUCTION
from gemio.app.state import SessionSnapshot
from gemio.core.agent import ChatAgent, ChatTurnResult
from gemio.core.client import GenAIClient, TokenUsageTracker
from gemio.core.context import (
    ChatSession,
    ChatTurn,
    CompactionResult,
    ReferenceMaterial,
    diagnose_user_pasted_text,
)
from gemio.core.models import (
    ModelInfo,
    get_fallback_model_catalog,
    resolve_model_name,
)
from gemio.core.prompts import SystemPromptComposer
from gemio.core.skills import SkillLoader, SkillManager, SkillSpec
from gemio.core.tools import (
    DynamicToolsetProvider,
    StaticToolProvider,
    ToolProvider,
    ToolRegistry,
    get_builtin_toolset,
    load_project_toolsets,
)
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    DeveloperProject,
    Project,
)


def _has_env_gemini_api_key() -> bool:
    """環境変数に GEMINI_API_KEY または GOOGLE_API_KEY が非空で設定されているか判定します。"""
    return any(
        bool(os.environ.get(key, "").strip())
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )


class GemioController:
    """
    Gemio セッション全体の状態・エージェント実行・中断制御を統括するコントローラクラス。
    スラッシュコマンド実行と TUI メニュー/ショートカット操作の双方から共通利用されます。
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        interaction_handler: InteractionHandler | None = None,
        approval_callback: Callable[[str], str | bool] | None = None,
        ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None,
        iteration_limit_callback: Callable[[], tuple[bool, int]] | None = None,
        api_key: str | None = None,
    ) -> None:
        self.config: AppConfig = config
        self.project_path = config.project_path
        self.current_model: str = config.resolved_initial_model

        self._interaction_handler: InteractionHandler | None = interaction_handler
        self._approval_callback = approval_callback
        self._ask_user_callback = ask_user_callback
        self._iteration_limit_callback = iteration_limit_callback

        self.startup_warnings: list[str] = []
        self._trust_consent_resolved: bool = not config.requested_trust

        self._exec_lock = threading.Lock()
        self._active_cancel_event: threading.Event | None = None

        # --trust 指定時は UI 上でユーザーの同意を得るまで安全側モードで初期化する
        safe_initial_mode = (
            APPROVAL_MODE_EDIT if config.requested_edit else APPROVAL_MODE_NORMAL
        )
        self.project: Project = self._create_project(safe_initial_mode)

        self.token_tracker = TokenUsageTracker()
        self.chat_session = ChatSession()
        self._setup_tools_skills_and_prompts()

        self.ai_client: GenAIClient | None = None
        self.chat_agent: ChatAgent | None = None

        in_container_without_key = (
            isinstance(self.project, DeveloperProject)
            and self.project.execution_mode == "in_container"
            and not _has_env_gemini_api_key()
            and not (api_key and api_key.strip())
        )
        self._needs_api_key_input: bool = in_container_without_key
        if not self._needs_api_key_input:
            self._init_ai_agents(api_key=api_key)

    # =====================================================================
    # 1. 初期化・対話ハンドラバインド・APIキー設定
    # =====================================================================

    def _create_project(self, initial_approval_mode: str) -> Project:
        if self.config.dev_mode:
            return DeveloperProject(
                self.project_path,
                require_approval=(initial_approval_mode == APPROVAL_MODE_NORMAL),
                approval_callback=self.on_approval,
                ask_user_callback=self.on_ask_user,
                container_name=self.config.container_name,
                approval_mode=initial_approval_mode,
            )
        return Project(
            self.project_path,
            require_approval=(initial_approval_mode == APPROVAL_MODE_NORMAL),
            approval_callback=self.on_approval,
            ask_user_callback=self.on_ask_user,
            approval_mode=initial_approval_mode,
        )

    def get_forbidden_paths_for_diagnosis(self) -> list[str]:
        """作業前提資料の入力テキスト診断で使用するローカル環境パス一覧を返します。"""
        paths = [str(self.project_path)]
        try:
            home_str = str(Path.home())
            if home_str:
                paths.append(home_str)
        except Exception:
            pass
        return paths

    def _setup_tools_skills_and_prompts(self) -> None:
        self.tool_registry = ToolRegistry()
        for ts in self.project.agent_toolsets():
            self.tool_registry.register_toolset(ts)
        self.tool_registry.register_toolset(get_builtin_toolset())

        for ext_ts in load_project_toolsets(
            self.project_path,
            on_warning=self.startup_warnings.append,
        ):
            self.tool_registry.register_toolset(ext_ts)

        if self.config.dynamic_tools:
            self.tool_provider: ToolProvider = DynamicToolsetProvider(
                registry=self.tool_registry
            )
        else:
            self.tool_provider = StaticToolProvider(registry=self.tool_registry)

        loaded_skills = SkillLoader.load_all(
            project_root=self.project_path,
            on_warning=self.startup_warnings.append,
        )

        def _on_skill_activated(spec: SkillSpec) -> None:
            if (
                isinstance(self.tool_provider, DynamicToolsetProvider)
                and spec.recommended_toolsets
            ):
                self.tool_provider.activate_toolsets(spec.recommended_toolsets)

        self.skill_manager = SkillManager(
            skills=loaded_skills,
            on_skill_activated=_on_skill_activated,
        )
        self.tool_provider.register_extra_tools(self.skill_manager.get_meta_tools())

        self.prompt_composer = SystemPromptComposer(
            tool_provider=self.tool_provider,
            skill_manager=self.skill_manager,
            extra_sections=[
                self.project.get_system_instruction(),
                APP_MATERIALS_GUIDANCE_INSTRUCTION,
            ],
        )

    def _init_ai_agents(self, api_key: str | None = None) -> None:
        if self.ai_client is not None:
            try:
                self.ai_client.close()
            except Exception:
                pass

        self.ai_client = GenAIClient(
            model_name=self.current_model,
            api_key=api_key,
            token_tracker=self.token_tracker,
        )
        self.current_model = self.ai_client.model_name
        self.chat_agent = ChatAgent(
            client=self.ai_client,
            session=self.chat_session,
            tool_provider=self.tool_provider,
            prompt_composer=self.prompt_composer,
            max_iterations=self.config.max_iterations,
            on_iteration_limit=self.on_iteration_limit,
            on_prompt_start=self.project.on_prompt_start,
            on_prompt_end=self.project.on_prompt_end,
        )

    def bind_interaction_handler(self, handler: InteractionHandler | None) -> None:
        """TUI ブリッジ等の対話確認ハンドラを動的にバインドします。"""
        self._interaction_handler = handler

    @property
    def needs_api_key_input(self) -> bool:
        """コンテナ内起動時にユーザーからのAPIキー直接入力が未完了かどうかを返します。"""
        return self._needs_api_key_input

    @property
    def is_client_ready(self) -> bool:
        """GenAIClient および ChatAgent が初期化完了しているかを返します。"""
        return self.ai_client is not None and self.chat_agent is not None

    def set_api_key(self, api_key: str) -> None:
        """ユーザーから入力された API キーを用いてクライアントとエージェントを初期化します。"""
        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError("APIキーが空です。")
        self._init_ai_agents(api_key=cleaned)
        self._needs_api_key_input = False

    @property
    def needs_initial_trust_consent(self) -> bool:
        """起動オプション --trust に対する初期同意確認が未解決かどうかを返します。"""
        return not self._trust_consent_resolved

    def apply_initial_trust_consent(self, consented: bool) -> tuple[str, bool]:
        """
        起動時のトラストモード同意確認結果を反映し、`(確定承認モード, fallback_from_trust)` を返します。
        """
        mode, fallback = self.config.resolve_initial_approval_mode(
            trust_consented=consented
        )
        self.project.set_approval_mode(mode)
        self._trust_consent_resolved = True
        return mode, fallback

    def _ensure_client_ready(self) -> None:
        if not self.is_client_ready:
            raise RuntimeError(
                "AIクライアントが初期化されていません。先にAPIキーを設定してください。"
            )

    # =====================================================================
    # 2. Project / ChatAgent からの同期コールバック中継
    # =====================================================================

    def on_approval(self, action_description: str) -> str | bool:
        """破壊的操作前の Human-in-the-loop 承認確認コールバック。"""
        if self._interaction_handler is not None:
            return self._interaction_handler.request_approval(action_description)
        if self._approval_callback is not None:
            return self._approval_callback(action_description)
        return "n"

    def on_ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """ask_user ツールによるユーザー確認・質問コールバック。"""
        if self._interaction_handler is not None:
            return self._interaction_handler.ask_user(question, options, context)
        if self._ask_user_callback is not None:
            return self._ask_user_callback(question, options, context)
        return ""

    def on_iteration_limit(self) -> tuple[bool, int]:
        """連続ツール呼び出し上限到達時の継続確認コールバック。"""
        if self._interaction_handler is not None:
            return self._interaction_handler.confirm_iteration_limit()
        if self._iteration_limit_callback is not None:
            return self._iteration_limit_callback()
        return False, 0

    # =====================================================================
    # 3. 状態スナップショット・診断情報取得
    # =====================================================================

    def get_token_usage(self) -> dict[str, int]:
        """現在のコンテキスト量・累積トークン量・履歴メッセージ数・前提資料件数を返します。"""
        return self.token_tracker.get_snapshot(
            history_messages=self.chat_session.history.message_count,
            materials_count=len(self.chat_session.materials),
        )

    def get_snapshot(self) -> SessionSnapshot:
        """UI のステータスバーや診断表示向けの不変状態スナップショットを返します。"""
        is_dev = isinstance(self.project, DeveloperProject)
        exec_mode = self.project.execution_mode if is_dev else ""
        container = self.project.container_name if is_dev else None
        usage = self.get_token_usage()

        return SessionSnapshot(
            project_name=self.project_path.name or str(self.project_path),
            project_path=self.project_path,
            is_dev_mode=is_dev,
            execution_mode=exec_mode,
            container_name=container,
            approval_mode=self.project.approval_mode,
            current_model=self.current_model,
            context_tokens=int(usage.get("last_prompt_tokens", 0)),
            cumulative_tokens=int(usage.get("cumulative_total_tokens", 0)),
            total_api_calls=int(usage.get("total_api_calls", 0)),
            materials_count=len(self.chat_session.materials),
            turn_count=self.chat_session.history.turn_count,
            has_summary=bool(self.chat_session.history.summary_text),
            active_skills=tuple(self.skill_manager.active_skill_names),
            dynamic_tools_enabled=isinstance(
                self.tool_provider, DynamicToolsetProvider
            ),
        )

    def inspect_sandbox(self) -> dict[str, Any]:
        """
        ワークスペース保護・サンドボックス診断情報を辞書として返します。
        基本モードと開発者モードの双方で一貫して呼び出し可能です。
        """
        if isinstance(self.project, DeveloperProject):
            status = dict(self.project.inspect_sandbox_status())
            status["is_dev_mode"] = True
            status["project_path"] = self.project.path
            status["approval_mode"] = self.project.approval_mode
            return status

        return {
            "is_dev_mode": False,
            "project_path": self.project.path,
            "approval_mode": self.project.approval_mode,
            "entries": [],
            "warnings": [],
            "sensitive_env_keys": [],
        }

    # =====================================================================
    # 4. 設定変更・セッション・作業前提資料操作API
    # =====================================================================

    def set_approval_mode(self, mode: str) -> tuple[str, bool]:
        """
        承認モード ('normal' | 'edit' | 'trust') を設定し、`(現在モード, 変更されたか)` を返します。
        """
        prev = self.project.approval_mode
        self.project.set_approval_mode(mode)
        curr = self.project.approval_mode
        return curr, (prev != curr)

    def set_edit_mode(self, enable: bool | None = None) -> tuple[bool, bool]:
        """
        エディットモードを設定（enable=None 時はトグル）し、`(現在edit_modeか, 変更されたか)` を返します。
        """
        target = (not self.project.edit_mode) if enable is None else enable
        if target and not self.project.edit_mode:
            self.project.set_edit_mode(True)
            return True, True
        if not target and self.project.edit_mode:
            self.project.set_edit_mode(False)
            return False, True
        return self.project.edit_mode, False

    def set_trust_mode(self, enable: bool) -> tuple[bool, bool]:
        """
        トラストモードを設定し、`(現在trust_modeか, 変更されたか)` を返します。
        ※有効化前のユーザー同意確認は UI 層で行ってから enable=True を渡してください。
        """
        if enable and not self.project.trust_mode:
            self.project.set_trust_mode(True)
            return True, True
        if not enable and self.project.trust_mode:
            self.project.set_trust_mode(False)
            return False, True
        return self.project.trust_mode, False

    @property
    def has_cached_models(self) -> bool:
        """APIからモデル一覧を一度でも取得・キャッシュ済みかどうかを返します。"""
        return (
            self.ai_client is not None
            and self.ai_client.get_cached_models() is not None
        )

    def get_cached_models(self) -> list[ModelInfo]:
        """
        通信を行わず即座に利用可能なモデル一覧（キャッシュ未取得時はフォールバック一覧）を返します。
        """
        if self.ai_client is not None:
            cached = self.ai_client.get_cached_models()
            if cached is not None:
                return cached
        return get_fallback_model_catalog()

    def fetch_available_models(self, *, force_refresh: bool = False) -> list[ModelInfo]:
        """
        API からテキスト生成対応モデルの一覧とスペックを取得し、キャッシュを更新して返します。
        """
        if self.ai_client is not None:
            return self.ai_client.list_available_models(force_refresh=force_refresh)
        return get_fallback_model_catalog()

    def get_current_model_info(self) -> ModelInfo | None:
        """現在のAIモデルに関するキャッシュ済みスペック情報があれば返します。"""
        if self.ai_client is not None:
            return self.ai_client.get_cached_model_info(self.current_model)
        return None

    def set_model(self, model_name_or_alias: str) -> str:
        """使用するAIモデルを切り替え、解決後のモデル名を返します。"""
        resolved = resolve_model_name(model_name_or_alias)
        if self.ai_client is not None:
            self.ai_client.set_model(resolved)
            self.current_model = self.ai_client.model_name
        else:
            self.current_model = resolved
        return self.current_model

    def list_materials(self) -> list[ReferenceMaterial]:
        """現在保持している作業前提資料のリスト（コピー）を返します。"""
        return self.chat_session.materials.list_all()

    def add_user_material(
        self,
        title: str,
        content: str,
    ) -> tuple[ReferenceMaterial, str]:
        """
        ユーザー入力テキストを機密診断した上で作業前提資料ストアへ追加します。
        機密情報が含まれている場合は ValueError を送出します。
        """
        cleaned_content = (content or "").strip()
        if not cleaned_content:
            raise ValueError("資料本文が空です。")

        diag = diagnose_user_pasted_text(
            cleaned_content,
            forbidden_paths=self.get_forbidden_paths_for_diagnosis(),
        )
        if diag.has_blocking_secrets:
            reasons = ", ".join(diag.blocking_secrets)
            raise ValueError(
                f"機密情報（{reasons}）が含まれているため登録できません。該当部分を削除してください。"
            )
        return self.chat_session.materials.add_user_document(
            title=title,
            content=cleaned_content,
        )

    def remove_material(self, identifier: str | int) -> ReferenceMaterial | None:
        """番号または material_id ('m1' 等) を指定して作業前提資料を1件削除します。"""
        return self.chat_session.materials.remove(identifier)

    def clear_materials(self) -> int:
        """保持しているすべての作業前提資料を破棄し、破棄件数を返します。"""
        return self.chat_session.materials.clear()

    def clear_session(self, clear_materials: bool = False) -> dict[str, int]:
        """
        対話履歴・トークン統計・有効スキル・動的ツール状態・拒否保留状態を一括リセットします。
        """
        reset_summary = self.chat_session.reset(
            clear_materials=clear_materials,
            token_tracker=self.token_tracker,
        )
        self.skill_manager.clear_active()
        if isinstance(self.tool_provider, DynamicToolsetProvider):
            self.tool_provider.reset_active_tools()
        self.project.reset_rejection_state()
        return reset_summary

    def rewind_turns(self, steps: int = 1) -> list[ChatTurn]:
        """直近から指定ターン数だけ対話履歴を巻き戻し、取り消された ChatTurn リストを返します。"""
        self.project.reset_rejection_state()
        return self.chat_session.rewind(steps=steps)

    def _refresh_context_tokens_after_compaction(self) -> None:
        """
        コンテキスト圧縮後の履歴・前提資料・システム指示のトークン数を再計測し、
        成功した場合のみ token_tracker の直近コンテキスト量（last_prompt_tokens）を更新します。
        失敗時は既存の値を維持します。
        """
        if self.ai_client is None:
            return
        request_contents = self.chat_session.build_request_contents()
        system_instruction = self.prompt_composer.compose()
        measured_tokens = self.ai_client.count_tokens(
            request_contents,
            system_instruction=system_instruction,
        )
        if measured_tokens is not None:
            self.token_tracker.set_context_tokens(measured_tokens)

    def compact_context(
        self,
        keep_recent_turns: int = 2,
        on_retry_warning: Callable[[str], None] | None = None,
    ) -> CompactionResult:
        """古い対話ターン群を構造化要約へ圧縮し、成功時は直近コンテキスト量を再計測して更新します。"""
        self._ensure_client_ready()
        assert self.ai_client is not None
        result = self.chat_session.compact(
            client=self.ai_client,
            keep_recent_turns=keep_recent_turns,
            on_retry_warning=on_retry_warning,
        )
        if result.compacted:
            self._refresh_context_tokens_after_compaction()
        return result

    def get_last_assistant_response(self) -> str:
        """直近ターンのAI回答テキストを返します（存在しない場合は空文字列）。"""
        return self.chat_session.history.get_last_response_text()

    def get_last_user_prompt(self) -> str:
        """直近ターンのユーザー入力プロンプト文字列を返します（存在しない場合は空文字列）。"""
        return self.chat_session.history.get_last_prompt_text()

    # =====================================================================
    # 5. AI対話実行および安全なターン中断制御
    # =====================================================================

    @property
    def is_busy(self) -> bool:
        """現在AI対話を実行中かどうかを返します。"""
        with self._exec_lock:
            return self._active_cancel_event is not None

    def _begin_execution(self) -> threading.Event:
        with self._exec_lock:
            if self._active_cancel_event is not None:
                raise RuntimeError("別のAI処理が現在実行中です。")
            cancel_event = threading.Event()
            self._active_cancel_event = cancel_event
            return cancel_event

    def _end_execution(self, cancel_event: threading.Event) -> None:
        with self._exec_lock:
            if self._active_cancel_event is cancel_event:
                self._active_cancel_event = None

    def cancel_active_execution(self) -> bool:
        """
        現在実行中のターン（対話ループ）および稼働中のサンドボックス子プロセスを中断します。
        アプリケーション自体は終了せず、安全に次の入力待ちへ復帰させます。

        Returns:
            実行中の処理に対してキャンセルシグナルを送出した場合は True、アイドル時は False。
        """
        with self._exec_lock:
            cancel_event = self._active_cancel_event
        if cancel_event is None:
            return False

        cancel_event.set()
        self.project.terminate_active_process()
        return True

    def run_chat_turn(
        self,
        prompt: str,
        on_log: Callable | None = None,
    ) -> ChatTurnResult:
        """
        通常のプロンプト入力に対するAI思考・ツール実行ループを1ターン実行します。
        """
        self._ensure_client_ready()
        assert self.chat_agent is not None

        cancel_event = self._begin_execution()
        try:
            return self.chat_agent.process_prompt(
                prompt,
                on_log_append=on_log,
                cancel_event=cancel_event,
            )
        finally:
            self._end_execution(cancel_event)

    def close(self) -> None:
        """実行中の子プロセスがあれば停止し、APIクライアントのリソースを解放します。"""
        self.cancel_active_execution()
        self.project.terminate_active_process()
        if self.ai_client is not None:
            try:
                self.ai_client.close()
            except Exception:
                pass


__all__ = [
    "GemioController",
]
