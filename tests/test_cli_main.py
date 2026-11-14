import ast
from pathlib import Path
from unittest.mock import MagicMock, patch
from google.genai import types
import pytest

from gemio.cli.main import GemioCLI, parse_args
from gemio.cli.ui import GemioUI
from gemio.core.context import ChatTurn
from gemio.core.search import SearchResult
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
    DeveloperProject,
    SandboxSecurityError,
)


def _create_mock_ui() -> MagicMock:
    """GemioUI の契約に準拠したモックUIバックエンドを生成します。"""
    mock_ui = MagicMock(spec=GemioUI)
    mock_ui.format_dev_mode_label.return_value = "Mock Mode Label"
    return mock_ui


def test_main_py_has_no_direct_prompt_toolkit_or_rich_imports():
    """main.py が prompt_toolkit や rich に直接依存せず、UIパッケージを介して疎結合に保たれていること"""
    main_file = Path(__file__).resolve().parent.parent / "src" / "gemio" / "cli" / "main.py"
    tree = ast.parse(main_file.read_text(encoding="utf-8"))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "prompt_toolkit" not in imported_roots
    assert "rich" not in imported_roots


def test_parse_args_options(tmp_path):
    """コマンドライン引数の解析（デフォルト値、--model、--dev、--container、--edit、--trust/--auto）が正しく行われること"""
    default_args = parse_args([])
    assert default_args.project_dir == "."
    assert default_args.dev is False
    assert default_args.edit is False
    assert default_args.trust is False

    custom_args = parse_args(
        [str(tmp_path), "-m", "argon", "--dev", "--container", "my-box", "--edit", "--auto", "--dynamic-tools"]
    )
    assert custom_args.project_dir == str(tmp_path)
    assert custom_args.model == "argon"
    assert custom_args.dev is True
    assert custom_args.container == "my-box"
    assert custom_args.edit is True
    assert custom_args.trust is True
    assert custom_args.dynamic_tools is True


def test_gemio_cli_startup_approval_modes_and_path_validation(tmp_path):
    """
    起動時の承認モード決定（--trust同意・拒否時のフォールバック・--edit）および
    存在しないディレクトリ指定時のエラー出力と終了が正しく動作すること
    """
    # 1. 存在しないパス指定時は ui.print_cli_error を呼んで SystemExit(1) となること
    mock_ui_err = _create_mock_ui()
    bad_args = parse_args([str(tmp_path / "non_existent_dir")])
    with pytest.raises(SystemExit) as exc_info:
        GemioCLI(bad_args, ui_backend=mock_ui_err)
    assert exc_info.value.code == 1
    mock_ui_err.print_cli_error.assert_called_once()

    # 2. --trust 指定かつ同意された場合 -> APPROVAL_MODE_TRUST で初期化
    mock_ui_trust = _create_mock_ui()
    mock_ui_trust.request_trust_mode_consent.return_value = True
    with patch("google.genai.Client"):
        cli_trust = GemioCLI(parse_args([str(tmp_path), "--trust"]), ui_backend=mock_ui_trust)
        assert cli_trust.project.approval_mode == APPROVAL_MODE_TRUST
        mock_ui_trust.print_initial_approval_mode_notice.assert_called_once_with(APPROVAL_MODE_TRUST)

    # 3. --trust と --edit 両方指定かつ --trust 同意を拒否した場合 -> APPROVAL_MODE_EDIT にフォールバック
    mock_ui_fallback = _create_mock_ui()
    mock_ui_fallback.request_trust_mode_consent.return_value = False
    with patch("google.genai.Client"):
        cli_fb = GemioCLI(parse_args([str(tmp_path), "--trust", "--edit"]), ui_backend=mock_ui_fallback)
        assert cli_fb.project.approval_mode == APPROVAL_MODE_EDIT
        mock_ui_fallback.print_initial_approval_mode_notice.assert_called_once_with(
            APPROVAL_MODE_EDIT, fallback_from_trust=True
        )


def test_gemio_cli_dev_mode_in_container_api_key_and_security_error(tmp_path):
    """
    --dev 起動時:
    1. in_container かつ環境変数にAPIキーが未設定なら ui.prompt_api_key_for_container で取得して GenAIClient に渡すこと
    2. 環境変数にAPIキーが設定済みならプロンプトをスキップすること
    3. SandboxSecurityError 発生時は ui.print_sandbox_security_error_panel を表示して終了すること
    """
    # 1. APIキー未設定時
    mock_ui_1 = _create_mock_ui()
    mock_ui_1.prompt_api_key_for_container.return_value = "DUMMY_PROMPT_KEY"
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"), \
         patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True), \
         patch("gemio.cli.main.GenAIClient") as mock_client_cls:
        GemioCLI(parse_args([str(tmp_path), "--dev"]), ui_backend=mock_ui_1)
        mock_ui_1.prompt_api_key_for_container.assert_called_once()
        assert mock_client_cls.call_args.kwargs["api_key"] == "DUMMY_PROMPT_KEY"

    # 2. APIキー設定済み時
    mock_ui_2 = _create_mock_ui()
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "DUMMY_ENV_KEY", "PATH": "/usr/bin"}, clear=True), \
         patch("gemio.cli.main.GenAIClient") as mock_client_cls:
        GemioCLI(parse_args([str(tmp_path), "--dev"]), ui_backend=mock_ui_2)
        mock_ui_2.prompt_api_key_for_container.assert_not_called()
        assert mock_client_cls.call_args.kwargs["api_key"] is None

    # 3. サンドボックス安全性エラー時
    mock_ui_3 = _create_mock_ui()
    with patch.object(
        DeveloperProject,
        "_is_running_in_container",
        side_effect=SandboxSecurityError("docker.sock detected"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            GemioCLI(parse_args([str(tmp_path), "--dev"]), ui_backend=mock_ui_3)
        assert exc_info.value.code == 1
        mock_ui_3.print_sandbox_security_error_panel.assert_called_once()


def test_gemio_cli_slash_commands_and_callbacks(tmp_path):
    """
    GemioCLI の全スラッシュコマンド（/help, /tokens, /clear, /sandbox, /edit, /trust, /model, /exit）と
    3種のコールバック（on_approval, on_iteration_limit, on_ask_user）が新コア構造と GemioUI を介して正しく動作すること
    """
    mock_ui = _create_mock_ui()
    args = parse_args([str(tmp_path), "--model", "argon", "--edit"])

    with patch("google.genai.Client"):
        cli = GemioCLI(args, ui_backend=mock_ui)
        assert cli.current_model == "gemini-4-argon"
        assert cli.project.edit_mode is True
        assert cli.chat_agent.client is cli.ai_client
        assert cli.search_agent.client is cli.ai_client

        # 1. /help と /sandbox
        assert cli.dispatch_input("/help") is True
        mock_ui.print_help_panel.assert_called_once()

        assert cli.dispatch_input("/sandbox") is True
        mock_ui.print_sandbox_status_panel.assert_called_once_with(cli.project)

        # 2. /edit の切り替え
        assert cli.dispatch_input("/edit off") is True
        assert cli.project.edit_mode is False
        mock_ui.print_edit_mode_changed_panel.assert_called_with(False)

        assert cli.dispatch_input("/edit") is True
        assert cli.project.edit_mode is True
        mock_ui.print_edit_mode_changed_panel.assert_called_with(True)

        # 3. /trust の切り替え
        mock_ui.request_trust_mode_consent.return_value = True
        assert cli.dispatch_input("/trust on") is True
        assert cli.project.trust_mode is True
        mock_ui.print_trust_mode_changed_panel.assert_called_with(True)

        assert cli.dispatch_input("/trust off") is True
        assert cli.project.trust_mode is False

        # 4. /model（共有 GenAIClient のモデルが一括更新されること）
        assert cli.dispatch_input("/model 3.8") is True
        assert cli.current_model == "gemini-3.8-flash"
        assert cli.ai_client.model_name == "gemini-3.8-flash"

        mock_ui.prompt_model_selection.return_value = "gemini-3.5-flash-lite"
        assert cli.dispatch_input("/model") is True
        assert cli.current_model == "gemini-3.5-flash-lite"

        # 5. /tokens と /clear (--all 含む)
        cli.chat_session.history.append_turn(
            ChatTurn(
                turn_id="t1",
                user_prompt="hi",
                contents=[types.Content(role="user", parts=[types.Part.from_text(text="hi")])],
                assistant_response="hello",
            )
        )
        cli.chat_session.materials.add_search_result("q1", "body1", [])
        cli.search_agent.search_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text="/search q1")])
        )
        cli.project._rejection_pending = True

        assert cli.dispatch_input("/tokens") is True
        mock_ui.print_token_usage_panel.assert_called_once()

        assert cli.dispatch_input("/clear") is True
        assert cli.chat_session.history.turn_count == 0
        assert cli.search_agent.has_active_session is False
        assert cli.project._rejection_pending is False
        assert len(cli.chat_session.materials) == 1
        mock_ui.print_session_reset_panel.assert_called_once()

        assert cli.dispatch_input("/clear --all") is True
        assert len(cli.chat_session.materials) == 0

        # 6. コールバックの委譲検証
        mock_ui.prompt_approval_choice.return_value = "y"
        assert cli.on_approval("write test.py") == "y"
        mock_ui.prompt_approval_choice.assert_called_once_with("write test.py")

        mock_ui.prompt_iteration_limit_continue.return_value = (True, 15)
        assert cli.on_iteration_limit() == (True, 15)

        mock_ui.prompt_ask_user_response.return_value = "選択肢A"
        assert cli.on_ask_user("質問", ["選択肢A"], "背景") == "選択肢A"

        # 7. /exit
        assert cli.dispatch_input("/exit") is False
        mock_ui.print_exit_message.assert_called_once()


def test_gemio_cli_normal_chat_and_search_execution(tmp_path):
    """
    通常対話プロンプト（検索履歴の自動リセット連携を含む）と /search コマンド
    （SearchAgent 実行 -> 成功時のみ MaterialStore 登録、新規検索、クリア、エラーハンドリング）が正しく動作すること
    """
    mock_ui = _create_mock_ui()
    with patch("google.genai.Client"):
        cli = GemioCLI(parse_args([str(tmp_path)]), ui_backend=mock_ui)

        # 1. 通常対話プロンプトの実行（直前の検索履歴がリセットされること）
        cli.search_agent.search_history.append(
            types.Content(role="user", parts=[types.Part.from_text(text="/search old")])
        )
        with patch.object(cli.chat_agent, "process_prompt") as mock_proc_prompt:
            assert cli.dispatch_input("リファクタリングして") is True
            assert cli.search_agent.has_active_session is False
            mock_ui.thinking_status.assert_called_once()
            mock_proc_prompt.assert_called_once_with(
                "リファクタリングして",
                on_log_append=mock_ui.print_agent_log_panel,
            )
            mock_ui.print_chat_divider.assert_called_once()

        with patch.object(cli.chat_agent, "process_prompt", side_effect=RuntimeError("unexpected crash")):
            assert cli.dispatch_input("エラーテスト") is True
            mock_ui.print_unexpected_error_panel.assert_called_once()

        # 2. /search コマンド（ヘルプ・クエリ実行・成功時のみ資料蓄積・--clear・例外ハンドリング）
        assert cli.dispatch_input("/search") is True
        mock_ui.print_search_help_panel.assert_called_once()

        ok_result = SearchResult(
            query="Python 3.15",
            response_text="検索結果本文",
            sources=[{"title": "Doc", "url": "https://example.com"}],
            created_at="12:00:00",
            succeeded=True,
        )
        with patch.object(cli.search_agent, "search", return_value=ok_result) as mock_search:
            assert cli.dispatch_input("/search -n Python 3.15") is True
            mock_ui.search_status.assert_called_once_with(is_subsequent=False)
            mock_search.assert_called_once_with("Python 3.15", new_session=True)
            assert len(cli.chat_session.materials) == 1
            mock_ui.print_search_response_panel.assert_called_once()

        # 検索通信エラー時（succeeded=False）はエラー文が作業前提資料に混入しないこと
        fail_result = SearchResult(
            query="Failed Query",
            response_text="通信エラーが発生しました",
            sources=[],
            created_at="12:01:00",
            succeeded=False,
        )
        with patch.object(cli.search_agent, "search", return_value=fail_result):
            assert cli.dispatch_input("/search Failed Query") is True
            assert len(cli.chat_session.materials) == 1

        assert cli.dispatch_input("/search --clear") is True
        assert len(cli.chat_session.materials) == 0
        mock_ui.print_search_materials_cleared_panel.assert_called_once_with(1)

        with patch.object(cli.search_agent, "search", side_effect=RuntimeError("search failed")):
            assert cli.dispatch_input("/search エラー検索") is True
            mock_ui.print_search_error_panel.assert_called_once()


def test_gemio_cli_keyboard_interrupt_cancels_input_but_terminates_on_ai_execution(tmp_path):
    """
    Ctrl+C (KeyboardInterrupt) の挙動検証:
    1. プロンプト入力待ち中（および /model, /trust の対話入力中）の Ctrl+C は入力キャンセルとしてセッションを継続すること
    2. 通常AI処理中（ChatAgent.process_prompt 内）の Ctrl+C は子プロセスを停止して即プログラム終了すること
    3. Web検索AI処理中（SearchAgent.search 内）の Ctrl+C も即プログラム終了すること
    4. 起動時の --trust 同意確認中の Ctrl+C はメッセージを表示して終了すること
    """
    mock_ui_input = _create_mock_ui()
    mock_ui_input.prompt_user_input.side_effect = [
        KeyboardInterrupt(),
        "/model",
        "/trust on",
        "/exit",
    ]
    mock_ui_input.prompt_model_selection.side_effect = KeyboardInterrupt()
    mock_ui_input.request_trust_mode_consent.side_effect = KeyboardInterrupt()

    with patch("google.genai.Client"):
        cli_input = GemioCLI(parse_args([str(tmp_path)]), ui_backend=mock_ui_input)
        cli_input.run()

    assert mock_ui_input.prompt_user_input.call_count == 4
    mock_ui_input.print_interrupt_exit_message.assert_not_called()
    mock_ui_input.print_exit_message.assert_called_once()

    mock_ui_ai = _create_mock_ui()
    mock_ui_ai.prompt_user_input.side_effect = [
        "重いテストを実行して",
        "絶対に呼ばれないはずの2回目の入力",
    ]
    with patch("google.genai.Client"):
        cli_ai = GemioCLI(parse_args([str(tmp_path)]), ui_backend=mock_ui_ai)
        with patch.object(cli_ai.project, "terminate_active_process") as mock_term, \
             patch.object(cli_ai.chat_agent, "process_prompt", side_effect=KeyboardInterrupt):
            cli_ai.run()
            mock_term.assert_called_once()

    assert mock_ui_ai.prompt_user_input.call_count == 1
    mock_ui_ai.print_interrupt_exit_message.assert_called_once()

    mock_ui_search = _create_mock_ui()
    mock_ui_search.prompt_user_input.side_effect = [
        "/search Python 最新動向",
        "絶対に呼ばれないはずの2回目の入力",
    ]
    with patch("google.genai.Client"):
        cli_search = GemioCLI(parse_args([str(tmp_path)]), ui_backend=mock_ui_search)
        with patch.object(cli_search.project, "terminate_active_process") as mock_term_s, \
             patch.object(cli_search.search_agent, "search", side_effect=KeyboardInterrupt):
            cli_search.run()
            mock_term_s.assert_called_once()

    assert mock_ui_search.prompt_user_input.call_count == 1
    mock_ui_search.print_interrupt_exit_message.assert_called_once()

    mock_ui_startup = _create_mock_ui()
    mock_ui_startup.request_trust_mode_consent.side_effect = KeyboardInterrupt()
    with pytest.raises(SystemExit) as exc_info:
        GemioCLI(parse_args([str(tmp_path), "--trust"]), ui_backend=mock_ui_startup)
    assert exc_info.value.code == 1
    mock_ui_startup.print_interrupt_exit_message.assert_called_once()


def test_gemio_cli_dynamic_tools_and_project_skills_reset_on_clear(tmp_path):
    """
    --dynamic-tools 指定および .gemio/skills/ 定義が存在する場合に DynamicToolsetProvider と
    SkillManager が有効化され、/clear 実行時に動的ツール状態と有効スキルもクリーンにリセットされること
    """
    from gemio.core.tools import DynamicToolsetProvider

    skill_dir = tmp_path / ".gemio" / "skills" / "demo-workflow"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-workflow\ndescription: デモ手順\nrecommended_toolsets: [project_explore]\n---\n手順本文",
        encoding="utf-8",
    )

    mock_ui = _create_mock_ui()
    with patch("google.genai.Client"):
        cli = GemioCLI(parse_args([str(tmp_path), "--dynamic-tools"]), ui_backend=mock_ui)
        assert isinstance(cli.tool_provider, DynamicToolsetProvider)
        assert cli.skill_manager.has_skills is True

        # 初期状態では get_structure は未有効だが、スキル有効化で連動有効化されること
        assert "get_structure" not in [f.__name__ for f in cli.chat_agent.tools]
        cli.skill_manager.activate("demo-workflow")
        assert cli.skill_manager.is_active("demo-workflow") is True
        assert "get_structure" in [f.__name__ for f in cli.chat_agent.tools]

        # /clear で有効スキルも動的ツール要求状態も初期状態に戻ること
        cli.dispatch_input("/clear")
        assert cli.skill_manager.active_skill_names == []
        assert "get_structure" not in [f.__name__ for f in cli.chat_agent.tools]
