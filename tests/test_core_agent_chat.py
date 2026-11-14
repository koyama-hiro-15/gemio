from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from google.genai import types

from gemio.core.agent import ChatAgent
from gemio.core.client import GenAIClient
from gemio.core.context import ChatSession
from gemio.core.models import AgentLogCategory
from gemio.core.tools import agent_tools as core_tools
from gemio.project.base import Project


def _make_usage(
    prompt: int = 0,
    candidates: int = 0,
    thoughts: int = 0,
    cached: int = 0,
    tool_use: int = 0,
    total: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
        cached_content_token_count=cached,
        tool_use_prompt_token_count=tool_use,
        total_token_count=total,
    )


def test_chat_agent_separation_and_prompt_snapshot_and_log_categories():
    """
    ChatAgent から検索機能が分離され、MaterialStore 操作ツールもAIへ公開されていないこと、
    および明示的な AgentLogCategory 通知と ChatTurnResult の返却が正しく動作すること
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_response = MagicMock()
        mock_response.function_calls = []
        mock_response.text = "通常の応答です。"
        mock_sdk.models.generate_content.return_value = mock_response

        def dummy_tool():
            return 42

        started = False
        ended = False

        def on_start():
            nonlocal started
            started = True

        def on_end():
            nonlocal ended
            ended = True

        client = GenAIClient(model_name="gemini-flash-latest")
        session = ChatSession()
        agent = ChatAgent(
            client=client,
            tools=[dummy_tool] + core_tools,
            session=session,
            on_prompt_start=on_start,
            on_prompt_end=on_end,
        )

        # 検索関連メソッドが存在せず、ツール群にも MaterialStore 操作ツールが含まれないこと
        assert not hasattr(agent, "process_search_prompt")
        assert not hasattr(agent, "search_history")
        tool_names = {t.__name__ for t in agent.tools}
        assert not any("material" in name.lower() for name in tool_names)

        categorized_logs: list[tuple[str, str | None]] = []

        def ui_like_logger(msg: str, category: str | None = None) -> None:
            categorized_logs.append((msg, category))

        result = agent.process_prompt("計算して", on_log_append=ui_like_logger)

        assert started is True and ended is True
        assert result.response_text == "通常の応答です。"
        assert result.aborted is False
        assert result.turn.user_prompt == "計算して"
        assert result.turn.assistant_response == "通常の応答です。"

        assert [cat for _, cat in categorized_logs] == [
            AgentLogCategory.USER,
            AgentLogCategory.ASSISTANT,
        ]

        call_kwargs = mock_sdk.models.generate_content.call_args.kwargs
        assert call_kwargs["config"]["tools"] == [dummy_tool] + core_tools
        assert session.history.turn_count == 1
        assert session.history.message_count == 2


def test_format_tool_call_log_and_unknown_tool_graceful_handling():
    """
    ツール呼び出しログの巨大引数省略が機能し、
    万一AIが未登録のツール名を呼び出しても StopIteration を起こさずエラー結果を返して回復すること
    """
    huge_text = "A" * 5000 + "\n" + "B" * 5000
    log_str = ChatAgent._format_tool_call_log(
        "write_file",
        {"file_path": "main.py", "content": huge_text, "items": [1, 2, 3, 4, 5, 6, 7]},
    )
    assert "Tool call: write_file" in log_str
    assert "file_path='main.py'" in log_str
    assert "10001文字/2行" in log_str
    assert "7件:" in log_str
    assert len(log_str) < 350

    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        bad_fc = MagicMock()
        bad_fc.name = "non_existent_tool"
        bad_fc.args = {}
        cand1 = MagicMock()
        cand1.content = types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="non_existent_tool", args={})],
        )
        resp1 = MagicMock(function_calls=[bad_fc], candidates=[cand1])
        resp2 = MagicMock(function_calls=None, text="利用可能なツールを確認して回答します。")
        mock_sdk.models.generate_content.side_effect = [resp1, resp2]

        agent = ChatAgent(client=GenAIClient(), tools=[])
        res = agent.process_prompt("テスト", on_log_append=lambda _: None)
        assert res.response_text == "利用可能なツールを確認して回答します。"
        func_resp = res.turn.contents[2].parts[0].function_response.response["result"]
        assert "[ERROR]" in func_resp and "non_existent_tool" in func_resp


def test_agent_loop_with_ask_user_and_abort_preserves_pair_integrity(tmp_path):
    """
    エージェントループ内での ask_user 正常対話フローと、
    Abort発生時（複数function_callがあるケースを含む）にペア整合性を保って安全に停止すること
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        # 1. 正常な ask_user -> 最終回答フロー
        mock_tool_call = MagicMock()
        mock_tool_call.name = "ask_user"
        mock_tool_call.args = {
            "question": "pytestが見つかりません。どうしますか？",
            "options": ["仮想環境を探す", "スキップする"],
            "context": "サンドボックス内で pytest が未インストールでした。",
        }
        mock_call_candidate = MagicMock()
        mock_call_candidate.content = types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="ask_user", args=mock_tool_call.args)],
        )
        mock_response_1 = MagicMock(function_calls=[mock_tool_call], candidates=[mock_call_candidate])
        mock_response_2 = MagicMock(
            function_calls=None,
            text="ご指示通りテスト実行をスキップして完了とします。",
        )
        mock_sdk.models.generate_content.side_effect = [mock_response_1, mock_response_2]

        proj = Project(str(tmp_path), ask_user_callback=lambda q, opts, ctx: "スキップする")
        agent = ChatAgent(
            client=GenAIClient(),
            tools=proj.agent_tools(),
            on_prompt_start=proj.on_prompt_start,
            on_prompt_end=proj.on_prompt_end,
        )
        res1 = agent.process_prompt("テストを実行して", on_log_append=lambda _: None)

        flat_contents = agent.session.history.to_contents()
        assert len(flat_contents) == 4
        assert flat_contents[2].parts[0].function_response.name == "ask_user"
        assert flat_contents[2].parts[0].function_response.response == {"result": "スキップする"}
        assert res1.response_text == "ご指示通りテスト実行をスキップして完了とします。"

        # 2. 複数ツール呼び出しの1つ目で Abort が発生した場合、後続呼び出しにもスキップ応答が記録され末尾が model で閉じられること
        call_a = MagicMock()
        call_a.name = "write_file"
        call_a.args = {"file_path": "a.py", "content": "1"}
        call_b = MagicMock()
        call_b.name = "write_file"
        call_b.args = {"file_path": "b.py", "content": "2"}

        cand_multi = MagicMock()
        cand_multi.content = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(name="write_file", args=call_a.args),
                types.Part.from_function_call(name="write_file", args=call_b.args),
            ],
        )
        resp_multi = MagicMock(function_calls=[call_a, call_b], candidates=[cand_multi])

        proj_abort = Project(str(tmp_path), require_approval=True, approval_callback=lambda _: "a")
        agent_abort = ChatAgent(client=GenAIClient(), tools=proj_abort.agent_tools())
        mock_sdk.models.generate_content.side_effect = [resp_multi]

        res_abort = agent_abort.process_prompt("a.pyとb.pyを作って", on_log_append=lambda _: None)
        assert res_abort.aborted is True
        abort_contents = agent_abort.session.history.to_contents()
        assert len(abort_contents) == 5
        assert "[ABORTED]" in abort_contents[2].parts[0].function_response.response["result"]
        assert "[ABORTED]" in abort_contents[3].parts[0].function_response.response["result"]
        assert abort_contents[4].role == "model"
        assert "作業の強制中止" in abort_contents[4].parts[0].text


def test_agent_iteration_limit_continue_and_abort():
    """
    連続ツール呼び出しが max_iterations に達した際、
    on_iteration_limit が True を返せば追加ループを継続し、False を返せば未完ループをロールバックして閉じること
    """
    noop_tool = lambda: "ok"
    noop_tool.__name__ = "noop_tool"

    mock_fc = MagicMock()
    mock_fc.name = "noop_tool"
    mock_fc.args = {}
    cand_fc = MagicMock()
    cand_fc.content = types.Content(
        role="model",
        parts=[types.Part.from_function_call(name="noop_tool", args={})],
    )
    resp_fc = MagicMock(function_calls=[mock_fc], candidates=[cand_fc])
    resp_final = MagicMock(function_calls=None, text="追加ループで完了しました。")

    # 1. 継続 (True, 2)
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk
        mock_sdk.models.generate_content.side_effect = [resp_fc, resp_final]

        agent_cont = ChatAgent(
            client=GenAIClient(),
            tools=[noop_tool],
            max_iterations=1,
            on_iteration_limit=lambda: (True, 2),
        )
        res_cont = agent_cont.process_prompt("継続テスト", on_log_append=lambda _: None)
        assert res_cont.aborted is False
        assert res_cont.response_text == "追加ループで完了しました。"

    # 2. 中断 (False, 0) -> 途中のツール履歴がロールバックされ [user, model(中断)] の2メッセージとなること
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk
        mock_sdk.models.generate_content.side_effect = [resp_fc]

        agent_stop = ChatAgent(
            client=GenAIClient(),
            tools=[noop_tool],
            max_iterations=1,
            on_iteration_limit=lambda: (False, 0),
        )
        res_stop = agent_stop.process_prompt("中断テスト", on_log_append=lambda _: None)
        assert res_stop.aborted is True
        stop_contents = agent_stop.session.history.to_contents()
        assert len(stop_contents) == 2
        assert stop_contents[0].role == "user"
        assert stop_contents[1].role == "model"
        assert "今回の処理を中断しました" in stop_contents[1].parts[0].text


def test_process_prompt_server_error_preserves_executed_tools_and_turn_integrity():
    """
    ツール実行後にサーバーエラーが継続して中断した場合、
    実行済みツール結果を保持し、末尾に model の中断メッセージを補完して次回プロンプトも正常継続できること
    """
    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep"):
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        def sample_tool(file_name: str):
            return f"Created {file_name}"

        mock_tool_call = MagicMock()
        mock_tool_call.name = "sample_tool"
        mock_tool_call.args = {"file_name": "example.txt"}
        mock_call_candidate = MagicMock()
        mock_call_candidate.content = types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="sample_tool", args={"file_name": "example.txt"})],
        )
        mock_response_1 = MagicMock(function_calls=[mock_tool_call], candidates=[mock_call_candidate])

        mock_sdk.models.generate_content.side_effect = [
            mock_response_1,
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
        ]

        agent = ChatAgent(client=GenAIClient(), tools=[sample_tool])
        res_err = agent.process_prompt("example.txt を作成して", on_log_append=lambda _: None)
        assert res_err.aborted is True

        flat1 = agent.session.history.to_contents()
        assert len(flat1) == 4
        assert flat1[2].parts[0].function_response.response == {"result": "Created example.txt"}
        assert flat1[3].role == "model"
        assert "一時中断しました" in flat1[3].parts[0].text

        # 次ターンで「続けてください」と送信した際にロール不整合を起こさず継続できること
        mock_response_continue = MagicMock(text="作業を完了しました！", function_calls=None)
        mock_sdk.models.generate_content.side_effect = [mock_response_continue]

        res_ok = agent.process_prompt("続けてください", on_log_append=lambda _: None)
        assert res_ok.response_text == "作業を完了しました！"
        assert agent.session.history.message_count == 6


def test_chat_agent_token_usage_integration():
    """ツールループ実行時のコンテキスト量・累積トークン量集計と ChatSession.reset 連携が正しく機能すること"""
    def dummy_tool(x: int) -> int:
        return x + 1

    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_fc = MagicMock()
        mock_fc.name = "dummy_tool"
        mock_fc.args = {"x": 10}
        cand_1 = MagicMock()
        cand_1.content = types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="dummy_tool", args={"x": 10})],
        )
        resp_1 = MagicMock(
            function_calls=[mock_fc],
            candidates=[cand_1],
            usage_metadata=_make_usage(prompt=1200, candidates=80, thoughts=200, cached=500, total=1480),
        )
        resp_2 = MagicMock(
            function_calls=None,
            text="結果は11です。",
            usage_metadata=_make_usage(prompt=1500, candidates=120, thoughts=100, cached=500, total=0),
        )
        mock_sdk.models.generate_content.side_effect = [resp_1, resp_2]

        client = GenAIClient()
        session = ChatSession()
        agent = ChatAgent(client=client, tools=[dummy_tool], session=session)
        agent.process_prompt("10に1を足して", on_log_append=lambda _: None)

        usage = agent.get_token_usage()
        assert usage["last_prompt_tokens"] == 1500
        assert usage["cumulative_total_tokens"] == 1480 + 1720
        assert usage["chat_api_calls"] == 2
        assert usage["chat_history_messages"] == 4

        summary = session.reset(clear_materials=False, token_tracker=client.token_tracker)
        assert summary["cleared_messages"] == 4
        assert summary["previous_context_tokens"] == 1500
        assert agent.get_token_usage()["last_prompt_tokens"] == 0


def test_chat_agent_evaluates_dynamic_tools_and_prompts_mid_loop():
    """
    1回の process_prompt ループの途中でツール追加やシステムプロンプト変更が発生した際、
    直後のイテレーションのリクエスト config（tools / system_instruction）に即時反映されること
    """
    from gemio.core.prompts import SystemPromptComposer
    from gemio.core.tools import StaticToolProvider, ToolRegistry

    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        registry = ToolRegistry()
        provider = StaticToolProvider(registry=registry)
        dynamic_rule = {"text": ""}

        def target_work_tool() -> str:
            return "work_done"

        def enable_special_mode() -> str:
            registry.register_functions([target_work_tool], toolset_name="special")
            dynamic_rule["text"] = "【動的追加ルール】target_work_tool を使用可能です。"
            return "activated"

        provider.register_extra_tools([enable_special_mode])
        composer = SystemPromptComposer(
            base_instruction="初期ルール",
            tool_provider=provider,
            extra_sections=[lambda: dynamic_rule["text"]],
        )

        # イテレーション1: enable_special_mode を呼ぶ -> イテレーション2: 追加された target_work_tool を呼ぶ -> イテレーション3: 完了
        fc1 = MagicMock()
        fc1.name = "enable_special_mode"
        fc1.args = {}
        c1 = MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="enable_special_mode", args={})],
            )
        )

        fc2 = MagicMock()
        fc2.name = "target_work_tool"
        fc2.args = {}
        c2 = MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="target_work_tool", args={})],
            )
        )

        mock_sdk.models.generate_content.side_effect = [
            MagicMock(function_calls=[fc1], candidates=[c1]),
            MagicMock(function_calls=[fc2], candidates=[c2]),
            MagicMock(function_calls=None, text="動的ツールを用いて完了しました。"),
        ]

        agent = ChatAgent(
            client=GenAIClient(),
            tool_provider=provider,
            prompt_composer=composer,
        )
        res = agent.process_prompt("動的モードで作業して", on_log_append=lambda _: None)
        assert res.response_text == "動的ツールを用いて完了しました。"

        calls_list = mock_sdk.models.generate_content.call_args_list
        # 1回目のAPIコール時点では enable_special_mode のみ
        cfg1 = calls_list[0].kwargs["config"]
        assert [f.__name__ for f in cfg1["tools"]] == ["enable_special_mode"]
        assert "【動的追加ルール】" not in cfg1["system_instruction"]

        # 2回目のAPIコール時点では target_work_tool と動的ルールが即時反映されていること
        cfg2 = calls_list[1].kwargs["config"]
        assert "target_work_tool" in [f.__name__ for f in cfg2["tools"]]
        assert "【動的追加ルール】" in cfg2["system_instruction"]
