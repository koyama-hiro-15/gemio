import argparse
import os
import sys
from pathlib import Path

from gemio.cli.ui import GemioUI, RichConsoleUI
from gemio.core.agent import ChatAgent
from gemio.core.client import GenAIClient
from gemio.core.context import ChatSession, MaterialKind
from gemio.core.models import (
    DEFAULT_AI_MODEL_NAME,
    resolve_model_name,
)
from gemio.core.prompts import (
    DEFAULT_SEARCH_INSTRUCTION,
    SystemPromptComposer,
)
from gemio.core.search import SearchAgent
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
    APPROVAL_MODE_TRUST,
    DeveloperProject,
    Project,
    SandboxSecurityError,
)

APP_MAX_ITERATIONS = 20  # 連続ツール呼び出しの最大回数


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析します。"""
    parser = argparse.ArgumentParser(description="Gemio (CLI)")
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="対象とするプロジェクトディレクトリのパス（省略した場合はカレントディレクトリを使用）",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=DEFAULT_AI_MODEL_NAME,
        help=f"使用するAIモデル名またはエイリアス（デフォルト: {DEFAULT_AI_MODEL_NAME}）",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="開発者モード（Dev Container / Dockerサンドボックス内でのシェルコマンド実行機能）を有効化します",
    )
    parser.add_argument(
        "--container",
        default=None,
        help="開発者モードをホストOSから起動する際、接続先のDockerコンテナ名またはIDを指定します",
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="エディットモード（ファイル作成・編集・移動・コピーは自動承認、削除とコマンド実行は都度確認）で起動します",
    )
    parser.add_argument(
        "--trust",
        "--auto",
        "--autonomous",
        dest="trust",
        action="store_true",
        help="トラストモード（信頼済みプロジェクトで事前同意の上、変更操作を自動承認）で起動します",
    )
    parser.add_argument(
        "--dynamic-tools",
        action="store_true",
        help="[Experimental] 動的ツールセット機能（AIからのツール要求によるオンデマンド有効化）を使用します",
    )
    return parser.parse_args(argv)


class GemioCLI:
    """
    Gemio の対話型CLIセッションの状態・コールバック・コマンド処理を管理するクラス。
    UI入出力はすべて抽象インターフェース GemioUI (デフォルトは RichConsoleUI) を介して行います。
    """

    def __init__(
        self,
        args: argparse.Namespace,
        ui_backend: GemioUI | None = None,
    ):
        self.args = args
        self.ui: GemioUI = ui_backend or RichConsoleUI()
        self.project_path = self._resolve_project_path(args.project_dir)
        self.current_model = resolve_model_name(args.model)

        initial_approval_mode = self._determine_initial_approval_mode()
        self._init_project_and_agents(initial_approval_mode)

    def _resolve_project_path(self, raw_dir: str) -> Path:
        """プロジェクトパスを絶対パスに解決し、存在とディレクトリであることを検証します。"""
        project_path = Path(raw_dir).resolve()
        if not project_path.exists():
            self.ui.print_cli_error(f"エラー: 指定されたディレクトリが存在しません: {project_path}")
            sys.exit(1)
        if not project_path.is_dir():
            self.ui.print_cli_error(f"エラー: 指定されたパスはディレクトリではありません: {project_path}")
            sys.exit(1)
        return project_path

    def _determine_initial_approval_mode(self) -> str:
        """起動オプション（--trust / --edit）に基づき初期承認モードを決定します。"""
        if self.args.trust:
            try:
                consented = self.ui.request_trust_mode_consent()
            except (KeyboardInterrupt, EOFError):
                self.ui.print_interrupt_exit_message()
                sys.exit(1)
            if consented:
                self.ui.print_initial_approval_mode_notice(APPROVAL_MODE_TRUST)
                return APPROVAL_MODE_TRUST
            if self.args.edit:
                self.ui.print_initial_approval_mode_notice(APPROVAL_MODE_EDIT, fallback_from_trust=True)
                return APPROVAL_MODE_EDIT
            self.ui.print_initial_approval_mode_notice(APPROVAL_MODE_NORMAL, fallback_from_trust=True)
            return APPROVAL_MODE_NORMAL

        if self.args.edit:
            self.ui.print_initial_approval_mode_notice(APPROVAL_MODE_EDIT)
            return APPROVAL_MODE_EDIT

        return APPROVAL_MODE_NORMAL

    def _init_project_and_agents(self, initial_approval_mode: str) -> None:
        """動作モードに応じた Project および GenAIClient / ChatSession / ChatAgent / SearchAgent を初期化します。"""
        container_api_key: str | None = None
        if self.args.dev:
            try:
                self.project: Project = DeveloperProject(
                    self.project_path,
                    require_approval=(initial_approval_mode == APPROVAL_MODE_NORMAL),
                    approval_callback=self.on_approval,
                    ask_user_callback=self.on_ask_user,
                    container_name=self.args.container,
                    approval_mode=initial_approval_mode,
                )
                if self.project.execution_mode == "in_container":
                    has_env_gemini_key = any(
                        bool(os.environ.get(k, "").strip())
                        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")
                    )
                    if not has_env_gemini_key:
                        container_api_key = self.ui.prompt_api_key_for_container()
                self.dev_mode_label = self.ui.format_dev_mode_label(self.project)
            except SandboxSecurityError as sec_err:
                self.ui.print_sandbox_security_error_panel(sec_err)
                sys.exit(1)
        else:
            self.project = Project(
                self.project_path,
                require_approval=(initial_approval_mode == APPROVAL_MODE_NORMAL),
                approval_callback=self.on_approval,
                ask_user_callback=self.on_ask_user,
                approval_mode=initial_approval_mode,
            )
            self.dev_mode_label = self.ui.format_dev_mode_label(self.project)

        self.tool_registry = ToolRegistry()
        for ts in self.project.agent_toolsets():
            self.tool_registry.register_toolset(ts)
        self.tool_registry.register_toolset(get_builtin_toolset())
        for ext_ts in load_project_toolsets(
            self.project_path,
            on_warning=self.ui.print_dim_message,
        ):
            self.tool_registry.register_toolset(ext_ts)

        if getattr(self.args, "dynamic_tools", False):
            self.tool_provider: ToolProvider = DynamicToolsetProvider(
                registry=self.tool_registry
            )
        else:
            self.tool_provider = StaticToolProvider(registry=self.tool_registry)

        loaded_skills = SkillLoader.load_all(
            project_root=self.project_path,
            on_warning=self.ui.print_dim_message,
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
            dev_mode=bool(self.args.dev),
            tool_provider=self.tool_provider,
            skill_manager=self.skill_manager,
        )

        self.ai_client = GenAIClient(
            model_name=self.current_model,
            api_key=container_api_key,
        )
        self.chat_session = ChatSession()
        self.chat_agent = ChatAgent(
            client=self.ai_client,
            session=self.chat_session,
            tool_provider=self.tool_provider,
            prompt_composer=self.prompt_composer,
            max_iterations=APP_MAX_ITERATIONS,
            on_iteration_limit=self.on_iteration_limit,
            on_prompt_start=self.project.on_prompt_start,
            on_prompt_end=self.project.on_prompt_end,
        )
        self.search_agent = SearchAgent(
            client=self.ai_client,
            search_instruction=DEFAULT_SEARCH_INSTRUCTION,
        )

    def on_approval(self, action_description: str) -> str:
        """破壊的操作実行前のHuman-in-the-loop承認要求コールバック。"""
        return self.ui.prompt_approval_choice(action_description)

    def on_iteration_limit(self) -> tuple[bool, int]:
        """エージェントの連続ツール呼び出し上限到達時の継続確認コールバック。"""
        return self.ui.prompt_iteration_limit_continue()

    def on_ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """AIからの質問・確認要求（ask_userツール）コールバック。"""
        return self.ui.prompt_ask_user_response(question, options, context)

    def handle_tokens_command(self) -> None:
        """/tokens（または /usage）コマンドによるトークン使用量・コンテキスト診断パネルを表示します。"""
        self.ui.print_token_usage_panel(self.chat_agent.get_token_usage())

    def handle_clear_command(self, user_input: str) -> None:
        """/clear（または /reset, /new）コマンドによる対話セッションリセットを処理します。"""
        parts = user_input.lower().split()
        clear_materials = any(arg in ["-a", "--all"] for arg in parts[1:])
        reset_summary = self.chat_session.reset(
            clear_materials=clear_materials,
            token_tracker=self.ai_client.token_tracker,
        )
        self.search_agent.reset_history()
        self.skill_manager.clear_active()
        if isinstance(self.tool_provider, DynamicToolsetProvider):
            self.tool_provider.reset_active_tools()
        self.project.reset_rejection_state()
        self.ui.print_session_reset_panel(reset_summary, clear_materials=clear_materials)

    def handle_edit_command(self, user_input: str) -> None:
        """/edit コマンドによるエディットモード切替を処理します。"""
        parts = user_input.lower().split()
        target_edit = not self.project.edit_mode
        if len(parts) > 1:
            if parts[1] in ["on", "true", "enable"]:
                target_edit = True
            elif parts[1] in ["off", "false", "disable"]:
                target_edit = False

        if target_edit and not self.project.edit_mode:
            self.project.set_edit_mode(True)
            self.ui.print_edit_mode_changed_panel(True)
        elif not target_edit and self.project.edit_mode:
            self.project.set_edit_mode(False)
            self.ui.print_edit_mode_changed_panel(False)
        else:
            status_str = "エディットモード" if self.project.edit_mode else "通常承認モード"
            self.ui.print_dim_message(f"現在はすでに {status_str} です。")

    def handle_trust_command(self, user_input: str) -> None:
        """/trust（または /auto）コマンドによるトラストモード切替を処理します。"""
        parts = user_input.lower().split()
        target_state = not self.project.trust_mode
        if len(parts) > 1:
            if parts[1] in ["on", "true", "enable"]:
                target_state = True
            elif parts[1] in ["off", "false", "disable"]:
                target_state = False

        if target_state and not self.project.trust_mode:
            try:
                consented = self.ui.request_trust_mode_consent()
            except KeyboardInterrupt:
                consented = False
            if consented:
                self.project.set_trust_mode(True)
                self.ui.print_trust_mode_changed_panel(True)
            else:
                self.ui.print_dim_message("トラストモードへの切り替えをキャンセルしました。")
        elif not target_state and self.project.trust_mode:
            self.project.set_trust_mode(False)
            self.ui.print_trust_mode_changed_panel(False)
        else:
            status_str = "トラストモード" if self.project.trust_mode else "通常承認モード"
            self.ui.print_dim_message(f"現在はすでに {status_str} です。")

    def handle_model_command(self, user_input: str) -> None:
        """/model コマンドによるAIモデルの切替を処理します。"""
        parts = user_input.split(maxsplit=1)
        if len(parts) > 1:
            target_model = resolve_model_name(parts[1])
            self.ai_client.set_model(target_model)
            self.current_model = self.ai_client.model_name
            self.ui.print_model_changed_panel(self.current_model)
            return

        try:
            selected_model = self.ui.prompt_model_selection(self.current_model)
        except KeyboardInterrupt:
            selected_model = None
        if selected_model:
            self.ai_client.set_model(selected_model)
            self.current_model = self.ai_client.model_name
            self.ui.print_model_changed_panel(self.current_model)
        else:
            self.ui.print_dim_message("モデルの変更をキャンセルしました。")

    def handle_search_command(self, user_input: str) -> None:
        """/search（または /s）コマンドによるWeb検索・前提資料管理を処理します。"""
        lower_input = user_input.lower()
        if lower_input in ["/search --clear", "/search -c", "/s --clear", "/s -c"]:
            cleared_count = self.chat_session.materials.clear(kind=MaterialKind.WEB_SEARCH)
            self.ui.print_search_materials_cleared_panel(cleared_count)
            return

        if lower_input in ["/search", "/s", "/search -n", "/search --new", "/s -n", "/s --new"]:
            self.ui.print_search_help_panel()
            return

        raw_query = user_input[8:].strip() if lower_input.startswith("/search ") else user_input[3:].strip()
        new_session = False
        if raw_query.startswith("-n ") or raw_query.startswith("--new "):
            new_session = True
            raw_query = raw_query.split(maxsplit=1)[1].strip()
        elif raw_query in ["-n", "--new"]:
            self.ui.print_dim_message("検索キーワードを入力してください。")
            return

        if not raw_query:
            self.ui.print_dim_message("検索キーワードを入力してください。")
            return

        is_subsequent = self.search_agent.has_active_session and not new_session
        try:
            with self.ui.search_status(is_subsequent=is_subsequent):
                result = self.search_agent.search(
                    raw_query,
                    new_session=new_session,
                )
                if result.succeeded:
                    self.chat_session.materials.add_search_result(
                        query=result.query,
                        response_text=result.response_text,
                        sources=result.sources,
                        created_at=result.created_at,
                    )
                self.ui.print_search_response_panel(
                    response_text=result.response_text,
                    sources=result.sources,
                    now_time_text=result.created_at,
                    total_materials=len(self.chat_session.materials),
                )
        except Exception as e:
            self.ui.print_search_error_panel(e)

    def run_chat_prompt(self, user_input: str) -> None:
        """通常のプロンプト入力に対するAI思考・ツール実行ループを処理します。"""
        self.search_agent.reset_history()
        try:
            with self.ui.thinking_status():
                self.chat_agent.process_prompt(
                    user_input,
                    on_log_append=self.ui.print_agent_log_panel,
                )
                self.ui.print_chat_divider()
        except Exception as e:
            self.ui.print_unexpected_error_panel(e)

    def dispatch_input(self, user_input: str) -> bool:
        """
        ユーザー入力を解析し、終了コマンド・スラッシュコマンド・通常プロンプトへ振り分けます。

        Returns:
            セッションを継続する場合は True、終了する場合は False。
        """
        lower_cmd = user_input.lower()

        if lower_cmd in ["/exit", "exit", "quit"]:
            self.ui.print_exit_message()
            return False

        if lower_cmd in ["/help", "/h", "help", "/info"]:
            self.ui.print_help_panel()
            return True

        if lower_cmd in ["/tokens", "/usage", "/token"]:
            self.handle_tokens_command()
            return True

        if (
            lower_cmd in ["/clear", "/reset", "/new"]
            or lower_cmd.startswith("/clear ")
            or lower_cmd.startswith("/reset ")
            or lower_cmd.startswith("/new ")
        ):
            self.handle_clear_command(user_input)
            return True

        if lower_cmd in ["/sandbox", "/status", "/container"]:
            self.ui.print_sandbox_status_panel(self.project)
            return True

        if lower_cmd == "/edit" or lower_cmd.startswith("/edit "):
            self.handle_edit_command(user_input)
            return True

        if (
            lower_cmd in ["/trust", "/auto"]
            or lower_cmd.startswith("/trust ")
            or lower_cmd.startswith("/auto ")
        ):
            self.handle_trust_command(user_input)
            return True

        if lower_cmd == "/model" or lower_cmd.startswith("/model "):
            self.handle_model_command(user_input)
            return True

        if (
            lower_cmd in ["/search", "/s"]
            or lower_cmd.startswith("/search ")
            or lower_cmd.startswith("/s ")
        ):
            self.handle_search_command(user_input)
            return True

        self.run_chat_prompt(user_input)
        return True

    def run(self) -> None:
        """ウェルカムバナーを表示し、メインの対話（REPL）ループを開始します。"""
        self.ui.print_welcome_banner(
            self.project_path,
            self.current_model,
            trust_mode=self.project.trust_mode,
            dev_mode_label=self.dev_mode_label,
            approval_mode=self.project.approval_mode,
        )

        if self.args.dev:
            self.ui.print_sandbox_status_panel(self.project)

        session = self.ui.create_prompt_session()

        while True:
            try:
                user_input = self.ui.prompt_user_input(
                    session,
                    self.project,
                    self.chat_agent.get_token_usage(),
                )
            except KeyboardInterrupt:
                self.ui.print_dim_message("入力をキャンセルしました（終了するには '/exit' を入力してください）。")
                continue
            except EOFError:
                break

            if not user_input:
                continue

            try:
                if not self.dispatch_input(user_input):
                    break
            except KeyboardInterrupt:
                self.project.terminate_active_process()
                self.ui.print_interrupt_exit_message()
                break


def main(argv: list[str] | None = None) -> None:
    """Gemio CLI エントリポイント。"""
    args = parse_args(argv)
    cli = GemioCLI(args)
    cli.run()


__all__ = [
    "APP_MAX_ITERATIONS",
    "GemioCLI",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    main()
