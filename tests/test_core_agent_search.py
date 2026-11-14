from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from google.genai import types

from gemio.core.agent import ChatAgent
from gemio.core.client import GenAIClient
from gemio.core.context import ChatSession, ChatTurn
from gemio.core.search import SearchAgent


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


def test_search_agent_uses_dedicated_config_and_returns_structured_result():
    """
    SearchAgent が google_search 専用設定と検索用システムインストラクションを適用し、
    構造化された SearchResult を返すこと（内部に資料を溜め込まないこと）
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_candidate = MagicMock()
        mock_candidate.grounding_metadata = None
        mock_response = MagicMock(
            text="Python 3.13の最新機能についての解説です。",
            candidates=[mock_candidate],
        )
        mock_sdk.models.generate_content.return_value = mock_response

        client = GenAIClient()
        search_agent = SearchAgent(client=client)
        logs: list[str] = []
        result = search_agent.search("Python 3.13 新機能", on_log_append=logs.append)

        assert result.succeeded is True
        assert result.query == "Python 3.13 新機能"
        assert result.response_text == "Python 3.13の最新機能についての解説です。"
        assert result.sources == []
        assert len(logs) == 2

        call_kwargs = mock_sdk.models.generate_content.call_args.kwargs
        config = call_kwargs["config"]
        assert config["tools"] == [{"google_search": {}}]
        assert "Google検索ツールを活用し" in config["system_instruction"]
        assert not hasattr(search_agent, "materials")


def test_search_agent_parses_and_deduplicates_grounding_sources():
    """grounding_metadata から出典タイトルとURLを抽出し、同一URLの重複および空URLを排除すること"""
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        chunk1 = MagicMock()
        chunk1.web.title = "Python 3.13 Release Notes"
        chunk1.web.uri = "https://docs.python.org/3.13/whatsnew/3.13.html"

        chunk2 = MagicMock()
        chunk2.web.title = "Python 3.13 Documentation (Duplicate URL)"
        chunk2.web.uri = "https://docs.python.org/3.13/whatsnew/3.13.html"

        chunk3 = MagicMock()
        chunk3.web.title = "Real Python Guide"
        chunk3.web.uri = "https://realpython.com/python313-new-features/"

        chunk4 = MagicMock()
        chunk4.web.title = "Empty URI Source"
        chunk4.web.uri = ""

        mock_candidate = MagicMock()
        mock_candidate.grounding_metadata.grounding_chunks = [chunk1, chunk2, chunk3, chunk4]
        mock_response = MagicMock(text="最新情報のまとめ", candidates=[mock_candidate])
        mock_sdk.models.generate_content.return_value = mock_response

        search_agent = SearchAgent(client=GenAIClient())
        result = search_agent.search("最新情報")

        assert result.succeeded is True
        assert result.sources == [
            {
                "title": "Python 3.13 Release Notes",
                "url": "https://docs.python.org/3.13/whatsnew/3.13.html",
            },
            {
                "title": "Real Python Guide",
                "url": "https://realpython.com/python313-new-features/",
            },
        ]


def test_search_agent_isolates_chat_history_and_user_materials():
    """
    ChatSession の対話履歴に含まれる機密情報および MaterialStore 内のユーザー追加資料が
    SearchAgent から完全に隔離され、検索APIリクエストに一切含まれないこと
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_candidate = MagicMock(grounding_metadata=None)
        mock_sdk.models.generate_content.return_value = MagicMock(
            text="検索結果テキスト",
            candidates=[mock_candidate],
        )

        client = GenAIClient()
        session = ChatSession()
        session.history.append_turn(
            ChatTurn(
                turn_id="t1",
                user_prompt="内部コード確認",
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="SECRET_API_KEY = 'DUMMY_CONFIDENTIAL_TOKEN_12345'")],
                    ),
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text="確認しました。")],
                    ),
                ],
                assistant_response="確認しました。",
            )
        )
        session.materials.add_user_document(
            title="社内非公開設計メモ",
            content="INTERNAL_PRIVATE_SPEC_XYZ",
        )

        search_agent = SearchAgent(client=client)
        search_agent.search("FastAPI チュートリアル")

        sent_contents = mock_sdk.models.generate_content.call_args.kwargs["contents"]
        assert len(sent_contents) == 1
        sent_text = sent_contents[0].parts[0].text
        assert sent_text == "/search FastAPI チュートリアル"
        assert "SECRET_API_KEY" not in sent_text
        assert "INTERNAL_PRIVATE_SPEC_XYZ" not in sent_text


def test_search_session_continuity_and_explicit_reset():
    """
    連続検索時は直前の検索文脈（search_history）を引き継いで深掘り検索でき、
    new_session=True または reset_history() 呼び出し時に検索文脈がリセットされること
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_candidate = MagicMock(grounding_metadata=None)
        mock_sdk.models.generate_content.side_effect = [
            MagicMock(text="1回目の検索回答", candidates=[mock_candidate]),
            MagicMock(text="2回目の深掘り回答", candidates=[mock_candidate]),
            MagicMock(text="新規検索の回答", candidates=[mock_candidate]),
            MagicMock(text="リセット後の検索回答", candidates=[mock_candidate]),
        ]

        search_agent = SearchAgent(client=GenAIClient())
        assert search_agent.has_active_session is False

        # 1. 初回検索 + 連続深掘り検索
        search_agent.search("FastAPI OAuth2 使い方")
        assert search_agent.has_active_session is True
        search_agent.search("リフレッシュトークンの設定方法は？")

        sent_second = mock_sdk.models.generate_content.call_args_list[1].kwargs["contents"]
        assert len(sent_second) == 3
        assert sent_second[0].parts[0].text == "/search FastAPI OAuth2 使い方"
        assert sent_second[1].parts[0].text == "1回目の検索回答"
        assert sent_second[2].parts[0].text == "/search リフレッシュトークンの設定方法は？"

        # 2. new_session=True による文脈リセット
        search_agent.search("まったく別の検索", new_session=True)
        sent_third = mock_sdk.models.generate_content.call_args_list[2].kwargs["contents"]
        assert len(sent_third) == 1
        assert sent_third[0].parts[0].text == "/search まったく別の検索"

        # 3. reset_history() による明示的リセット
        search_agent.reset_history()
        assert search_agent.has_active_session is False
        search_agent.search("作業後の検索")
        sent_fourth = mock_sdk.models.generate_content.call_args_list[3].kwargs["contents"]
        assert len(sent_fourth) == 1
        assert sent_fourth[0].parts[0].text == "/search 作業後の検索"


def test_search_error_recovery_and_shared_token_tracker():
    """
    Web検索エラー時に search_history をロールバックして succeeded=False を返すこと、
    および GenAIClient 共有時に通常対話コンテキスト量を上書きせず累積トークンが加算されること
    """
    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep"):
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_sdk.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
        ]

        client = GenAIClient()
        search_agent = SearchAgent(client=client)
        err_res = search_agent.search("Python 3.14")
        assert err_res.succeeded is False
        assert "Web検索中にサーバー混雑または通信エラーが発生しました" in err_res.response_text
        assert err_res.sources == []
        assert len(search_agent.search_history) == 0

        # 通常対話 + Web検索の共有クライアント集計検証
        resp_chat = MagicMock(
            function_calls=None,
            text="通常回答",
            usage_metadata=_make_usage(prompt=2000, candidates=100, total=2100),
        )
        resp_search = MagicMock(
            text="検索回答",
            candidates=[],
            usage_metadata=_make_usage(prompt=400, candidates=300, thoughts=50, tool_use=150, total=900),
        )
        mock_sdk.models.generate_content.side_effect = [resp_chat, resp_search]

        session = ChatSession()
        chat_agent = ChatAgent(client=client, session=session)
        chat_agent.process_prompt("質問", on_log_append=lambda _: None)
        ok_search = search_agent.search("検索クエリ")
        assert ok_search.succeeded is True
        session.materials.add_search_result(ok_search.query, ok_search.response_text, ok_search.sources)

        usage = chat_agent.get_token_usage()
        assert usage["last_prompt_tokens"] == 2000
        assert usage["cumulative_total_tokens"] == 2100 + 900
        assert usage["chat_api_calls"] == 1
        assert usage["search_api_calls"] == 1
        assert usage["materials_count"] == 1
