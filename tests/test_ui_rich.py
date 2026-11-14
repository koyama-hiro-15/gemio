from unittest.mock import MagicMock, patch
from rich.console import Console

from gemio.cli.ui import AgentLogCategory, GemioUI, RichConsoleUI
from gemio.project import Project


def test_rich_console_ui_implements_gemio_ui_and_renders_to_injected_console(tmp_path):
    """
    RichConsoleUI が GemioUI を実装し、注入された Console インスタンスに対して
    各種パネル・メッセージ・検索結果・エージェントログ（カテゴリ分類付き）を出力すること
    """
    rec_console = Console(width=120, record=True)
    ui = RichConsoleUI(console=rec_console)
    assert isinstance(ui, GemioUI)

    proj = Project(str(tmp_path), require_approval=True)

    ui.print_welcome_banner(tmp_path, "gemini-flash-latest", dev_mode_label=ui.format_dev_mode_label(proj))
    ui.print_help_panel()
    ui.print_sandbox_status_panel(proj)
    ui.print_token_usage_panel({"last_prompt_tokens": 1200, "cumulative_total_tokens": 2500, "total_api_calls": 2})
    ui.print_session_reset_panel({"cleared_messages": 4, "retained_materials": 1})
    ui.print_edit_mode_changed_panel(True)
    ui.print_trust_mode_changed_panel(True)
    ui.print_model_changed_panel("gemini-4-argon")
    ui.print_search_materials_cleared_panel(1)
    ui.print_search_help_panel()
    ui.print_search_response_panel(
        response_text="検索回答の本文",
        sources=[{"title": "Official Site", "url": "https://example.com"}],
        now_time_text="12:00:00",
        total_materials=1,
    )
    ui.print_chat_divider()
    ui.print_dim_message("控えめな通知")
    ui.print_exit_message()
    ui.print_interrupt_exit_message()

    # エージェントログ出力とカテゴリ自動分類の検証
    ui.print_agent_log_panel("12:34:56\nTool call: get_structure (target_dir='.')")
    assert ui.last_log_category == AgentLogCategory.TOOL

    ui.print_agent_log_panel("12:34:57\n分析が完了しました。")
    assert ui.last_log_category == AgentLogCategory.ASSISTANT

    rendered = rec_console.export_text()
    assert "Welcome to Gemio!" in rendered
    assert "Gemio 操作ガイド & コマンド一覧" in rendered
    assert "トークン使用量 & コンテキスト診断" in rendered
    assert "検索回答の本文" in rendered
    assert "Tool call: get_structure" in rendered
    assert "分析が完了しました。" in rendered
    assert "中断シグナル (Ctrl+C)" in rendered


def test_rich_console_ui_status_lifecycle_and_auto_pause_on_prompts():
    """
    thinking_status / search_status コンテキストマネージャがスピナーを開始・終了し、
    実行中に承認要求や質問プロンプトが呼ばれた際はスピナーを自動で一時停止・再開すること
    （ただし Ctrl+C 割り込み時は不要なスピナー再開を行わないこと）
    """
    rec_console = Console(width=100, record=True)
    ui = RichConsoleUI(console=rec_console)

    # 1. thinking_status と search_status のライフサイクル
    with ui.thinking_status() as spinner:
        assert spinner is not None
    with ui.search_status(is_subsequent=True) as search_spinner:
        assert search_spinner is not None

    # 2. スピナー稼働中に対話プロンプト（prompt_approval_choice）が呼ばれた際の自動 stop() -> start()
    mock_spinner = MagicMock()
    with patch.object(ui, "create_thinking_spinner", return_value=mock_spinner), \
         patch("gemio.cli.ui.prompts.Prompt.ask", return_value="y"):
        with ui.thinking_status():
            choice = ui.prompt_approval_choice("write_file: sample.py")
            assert choice == "y"
            mock_spinner.stop.assert_called_once()
            mock_spinner.start.assert_called_once()

    # 3. 対話プロンプト中に Ctrl+C (KeyboardInterrupt) が発生した際は stop() のみ呼ばれ start() は呼ばれないこと
    mock_spinner_intr = MagicMock()
    with patch.object(ui, "create_thinking_spinner", return_value=mock_spinner_intr), \
         patch("gemio.cli.ui.prompts.Prompt.ask", side_effect=KeyboardInterrupt):
        try:
            with ui.thinking_status():
                ui.prompt_approval_choice("write_file: sample.py")
        except KeyboardInterrupt:
            pass
        mock_spinner_intr.stop.assert_called_once()
        mock_spinner_intr.start.assert_not_called()
