from unittest.mock import MagicMock, patch
import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.keys import Keys
from rich.console import Console

from gemio.cli.ui.prompts import (
    clear_prompt_buffer,
    create_prompt_session,
    prompt_api_key_for_container,
    prompt_approval_choice,
    prompt_ask_user_response,
    prompt_iteration_limit_continue,
    prompt_model_selection,
    prompt_user_input,
    request_trust_mode_consent,
)
from gemio.core.exceptions import AgentExecutionAborted
from gemio.project import Project


def test_clear_prompt_buffer_and_session_key_bindings(tmp_path):
    """
    clear_prompt_buffer が複数行テキストを Undo 可能な状態で全消去し、
    create_prompt_session に Esc+Enter（送信）と Esc+Backspace（クリア）が登録されること、
    および prompt_user_input が multiline=True で入力を受け取り前後の空白を除去すること
    """
    buf = Buffer()
    buf.text = "1行目の入力\n2行目の入力\n3行目の入力"
    buf.cursor_position = len(buf.text)

    clear_prompt_buffer(buf)
    assert buf.text == ""
    assert buf.cursor_position == 0

    # Undo でクリア前のテキストが復元できること
    buf.undo()
    assert buf.text == "1行目の入力\n2行目の入力\n3行目の入力"

    with patch("gemio.cli.ui.prompts.PromptSession") as mock_session_cls:
        create_prompt_session()
        kb = mock_session_cls.call_args.kwargs["key_bindings"]
        registered_keys = [b.keys for b in kb.bindings]
        assert (Keys.Escape, Keys.ControlM) in registered_keys
        assert (Keys.Escape, Keys.ControlH) in registered_keys

        clear_binding = next(b for b in kb.bindings if b.keys == (Keys.Escape, Keys.ControlH))
        mock_event = MagicMock()
        mock_event.current_buffer = buf
        clear_binding.handler(mock_event)
        assert buf.text == ""

    mock_session = MagicMock()
    mock_session.prompt.return_value = "  こんにちは  \n"
    project = Project(str(tmp_path), require_approval=True)
    result = prompt_user_input(mock_session, project)
    assert result == "こんにちは"
    mock_session.prompt.assert_called_once()
    assert mock_session.prompt.call_args.kwargs.get("multiline") is True


def test_prompt_api_key_for_container_masks_password_and_retries_on_empty():
    """
    コンテナ内起動時のAPIキー入力が is_password=True で呼ばれ、
    空入力時は再入力を促し、有効な入力から前後の空白を除去して返すこと
    """
    test_console = Console(width=100, record=True)
    mock_prompt_fn = MagicMock(side_effect=["   ", "  DUMMY_GEMINI_TEST_KEY_123  "])

    result = prompt_api_key_for_container(prompt_fn=mock_prompt_fn, console=test_console)
    assert result == "DUMMY_GEMINI_TEST_KEY_123"
    assert mock_prompt_fn.call_count == 2
    assert mock_prompt_fn.call_args.kwargs.get("is_password") is True
    assert "APIキーを受け付けました" in test_console.export_text()


def test_consent_approval_and_iteration_limit_prompts():
    """
    request_trust_mode_consent, prompt_approval_choice, prompt_iteration_limit_continue が
    ユーザーの選択結果に応じた正しい戻り値を返すこと
    """
    test_console = Console(width=100, record=True)

    with patch("gemio.cli.ui.prompts.Confirm.ask", return_value=True):
        assert request_trust_mode_consent(console=test_console) is True

    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="y"):
        assert prompt_approval_choice("write_file: a.py", console=test_console) == "y"

    with patch("gemio.cli.ui.prompts.Confirm.ask", return_value=True), \
         patch("gemio.cli.ui.prompts.Prompt.ask", return_value="15"):
        assert prompt_iteration_limit_continue(console=test_console) == (True, 15)

    with patch("gemio.cli.ui.prompts.Confirm.ask", return_value=False):
        assert prompt_iteration_limit_continue(console=test_console) == (False, 0)


def test_prompt_ask_user_response_choices_custom_and_abort():
    """
    prompt_ask_user_response が番号選択・'0'(その他自由入力)・直接テキスト入力を処理し、
    'a'/'abort' 入力時は AgentExecutionAborted を送出すること
    """
    test_console = Console(width=100, record=True)
    options = ["方式A (`fast`)", "方式B (**safe**)"]

    # 1. 番号選択 ("1" -> "方式A (`fast`)")
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="1"):
        res1 = prompt_ask_user_response(
            question="どちらの方式にしますか？",
            options=options,
            context="設計確認",
            console=test_console,
        )
        assert res1 == "方式A (`fast`)"

    # 2. "0" (その他) を選択して自由記述を入力
    with patch("gemio.cli.ui.prompts.Prompt.ask", side_effect=["0", "方式Cで実装して"]):
        res2 = prompt_ask_user_response("質問", options=options, console=test_console)
        assert res2 == "方式Cで実装して"

    # 3. 直接テキストを入力
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="直接の指示テキスト"):
        res3 = prompt_ask_user_response("質問", options=options, console=test_console)
        assert res3 == "直接の指示テキスト"

    # 4. 'a' (Abort) の選択
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="a"):
        with pytest.raises(AgentExecutionAborted):
            prompt_ask_user_response("質問", options=options, console=test_console)


def test_prompt_model_selection_number_alias_and_cancel():
    """prompt_model_selection がプリセット番号・エイリアス名・未入力キャンセルを正しく処理すること"""
    test_console = Console(width=100, record=True)

    # 番号 "2" -> gemini-4-argon
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="2"):
        assert prompt_model_selection("gemini-flash-latest", console=test_console) == "gemini-4-argon"

    # エイリアス "3.8" -> gemini-3.8-flash
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value="3.8"):
        assert prompt_model_selection("gemini-flash-latest", console=test_console) == "gemini-3.8-flash"

    # 空入力 -> None (キャンセル)
    with patch("gemio.cli.ui.prompts.Prompt.ask", return_value=""):
        assert prompt_model_selection("gemini-flash-latest", console=test_console) is None
