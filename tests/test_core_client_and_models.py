from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gemio.core.client import GenAIClient, TokenUsageTracker
from gemio.core.models import (
    AgentLogCategory,
    DEFAULT_AI_MODEL_NAME,
    resolve_model_name,
)


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


def test_resolve_model_name_aliases_case_and_passthrough():
    """モデル名のエイリアス解決、大文字小文字・空白の正規化、および未知モデルのパススルーが正しく動作すること"""
    assert DEFAULT_AI_MODEL_NAME == "gemini-flash-latest"
    assert resolve_model_name("flash") == "gemini-flash-latest"
    assert resolve_model_name("latest") == "gemini-flash-latest"
    assert resolve_model_name("default") == "gemini-flash-latest"
    assert resolve_model_name("argon") == "gemini-4-argon"
    assert resolve_model_name("gemini-4") == "gemini-4-argon"
    assert resolve_model_name("4") == "gemini-4-argon"
    assert resolve_model_name("flash-3.8") == "gemini-3.8-flash"
    assert resolve_model_name("3.8") == "gemini-3.8-flash"
    assert resolve_model_name("flash-3.6") == "gemini-3.6-flash"
    assert resolve_model_name("3.6") == "gemini-3.6-flash"
    assert resolve_model_name("lite") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.5-lite") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.5") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.1-lite") == "gemini-3.1-flash-lite"
    assert resolve_model_name("3.1") == "gemini-3.1-flash-lite"

    assert resolve_model_name("  ARGON  ") == "gemini-4-argon"
    assert resolve_model_name("Flash-3.8") == "gemini-3.8-flash"
    assert resolve_model_name("  custom-fine-tuned-model  ") == "custom-fine-tuned-model"

    assert AgentLogCategory.TOOL == "tool"
    assert AgentLogCategory.SEARCH == "search"


def test_genai_client_initialization_model_switching_and_retry():
    """GenAIClient のAPIキー初期化、モデル動的切替、および一時的エラーの自動リトライ回復が機能すること"""
    assert GenAIClient.is_retryable_error(Exception("503 Service Unavailable")) is True
    assert GenAIClient.is_retryable_error(Exception("429 Resource Exhausted")) is True
    assert GenAIClient.is_retryable_error(TimeoutError("Request timed out")) is True
    assert GenAIClient.is_retryable_error(ConnectionError("Connection reset")) is True
    assert GenAIClient.is_retryable_error(Exception("400 Bad Request")) is False
    assert GenAIClient.is_retryable_error(Exception("403 Forbidden")) is False

    with patch("google.genai.Client") as mock_sdk_cls, patch("time.sleep"):
        mock_sdk = MagicMock()
        mock_sdk_cls.return_value = mock_sdk

        client = GenAIClient(model_name="flash")
        mock_sdk_cls.assert_called_once_with()
        assert client.model_name == "gemini-flash-latest"

        client.set_model("argon")
        assert client.model_name == "gemini-4-argon"

        ok_resp = MagicMock(
            text="回復しました",
            usage_metadata=_make_usage(prompt=500, candidates=50, total=550),
        )
        mock_sdk.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            ok_resp,
        ]

        warnings: list[str] = []
        res = client.generate_content(
            contents=["hello"],
            on_retry_warning=warnings.append,
            usage_source="chat",
        )
        assert res is ok_resp
        assert len(warnings) == 1
        assert "サーバー混雑または通信エラーを検知しました" in warnings[0]
        assert client.token_tracker.last_prompt_tokens == 500
        assert client.token_tracker.cumulative_total_tokens == 550

        client.close()
        mock_sdk.close.assert_called_once()

    with patch("google.genai.Client") as mock_sdk_cls:
        GenAIClient(model_name="3.8", api_key="DUMMY_GEMINI_CONTAINER_INPUT_KEY")
        mock_sdk_cls.assert_called_once_with(api_key="DUMMY_GEMINI_CONTAINER_INPUT_KEY")


def test_token_usage_tracker_separates_chat_search_and_compaction_sources():
    """
    TokenUsageTracker が chat / search / compaction のAPI呼び出し回数を区別し、
    search や compaction の入力トークン数で直近対話コンテキスト量（last_prompt_tokens）を上書きしないこと
    """
    tracker = TokenUsageTracker()

    # 1. 通常対話 (chat) -> last_prompt_tokens が更新される (total=0 でも内訳自動合算)
    resp_chat = SimpleNamespace(
        usage_metadata=_make_usage(prompt=2500, candidates=200, thoughts=100, cached=1000, total=0)
    )
    tracker.record(resp_chat, source="chat")
    snap1 = tracker.get_snapshot(history_messages=2, materials_count=1)
    assert snap1["last_prompt_tokens"] == 2500
    assert snap1["last_total_tokens"] == 2800
    assert snap1["cumulative_total_tokens"] == 2800
    assert snap1["chat_api_calls"] == 1
    assert snap1["chat_history_messages"] == 2
    assert snap1["materials_count"] == 1

    # 2. Web検索 (search) -> last_prompt_tokens(2500) は維持され、累積に加算される
    resp_search = SimpleNamespace(
        usage_metadata=_make_usage(prompt=300, candidates=250, tool_use=150, total=700)
    )
    tracker.record(resp_search, source="search")
    snap2 = tracker.get_snapshot()
    assert snap2["last_prompt_tokens"] == 2500
    assert snap2["cumulative_total_tokens"] == 2800 + 700
    assert snap2["cumulative_tool_use_tokens"] == 150
    assert snap2["search_api_calls"] == 1
    assert snap2["total_api_calls"] == 2

    # 3. 要約圧縮 (compaction) -> compaction_api_calls に記録され累積加算される
    resp_compact = SimpleNamespace(
        usage_metadata=_make_usage(prompt=1800, candidates=300, total=2100)
    )
    tracker.record(resp_compact, source="compaction")
    snap3 = tracker.get_snapshot()
    assert snap3["last_prompt_tokens"] == 2500
    assert snap3["compaction_api_calls"] == 1
    assert snap3["total_api_calls"] == 3
    assert snap3["cumulative_total_tokens"] == 2800 + 700 + 2100
