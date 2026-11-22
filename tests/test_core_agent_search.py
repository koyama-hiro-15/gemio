import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from google.genai import types

from gemio.core.agent import ChatAgent
from gemio.core.client import GenAIClient
from gemio.core.context import ChatSession, ChatTurn
from gemio.core.search import (
    MAX_TOOL_SEARCH_QUERY_CHARS,
    SearchAgent,
    SearchResult,
    format_search_result_for_tool,
    validate_search_query_safety,
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


def test_search_agent_uses_dedicated_config_and_returns_structured_result():
    """
    SearchAgent が google_search 専用設定と検索用システムインストラクションを適用し、
    構造化された SearchResult を返すこと（内部に資料や履歴を溜め込まないこと）
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
        assert not hasattr(search_agent, "search_history")


def test_search_agent_parses_sources_and_formats_tool_output_without_redirect_urls():
    """
    1. grounding_metadata から出典タイトルとURLを抽出し、同一URLの重複および空URLを排除すること
    2. format_search_result_for_tool が暗号化リダイレクトURL (vertexaisearch等) を除去し、
       タイトルと判別可能ドメインのみを整形出力すること
    """
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
        chunk3.web.title = "FastAPI Official Guide"
        chunk3.web.uri = (
            "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
            "AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
        )

        chunk4 = MagicMock()
        chunk4.web.title = "Empty URI Source"
        chunk4.web.uri = ""

        mock_candidate = MagicMock()
        mock_candidate.grounding_metadata.grounding_chunks = [
            chunk1,
            chunk2,
            chunk3,
            chunk4,
        ]
        mock_response = MagicMock(text="最新情報のまとめ", candidates=[mock_candidate])
        mock_sdk.models.generate_content.return_value = mock_response

        search_agent = SearchAgent(client=GenAIClient())
        result = search_agent.search("最新情報")

        assert result.succeeded is True
        assert len(result.sources) == 2
        assert result.sources[0]["title"] == "Python 3.13 Release Notes"
        assert result.sources[1]["title"] == "FastAPI Official Guide"

        formatted = format_search_result_for_tool(result)
        assert "【Web検索結果（外部最新情報）】" in formatted
        assert "最新情報のまとめ" in formatted
        assert "Python 3.13 Release Notes (docs.python.org)" in formatted
        assert "FastAPI Official Guide" in formatted
        assert "vertexaisearch" not in formatted


def test_search_agent_is_stateless_and_isolates_chat_history_and_materials():
    """
    1. ChatSession の対話履歴および MaterialStore 内のユーザー資料が検索APIへ一切送信されないこと
    2. 連続して search() を呼び出しても過去の検索履歴を引き継がず、常に1問1答（ステートレス）であること
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        mock_candidate = MagicMock(grounding_metadata=None)
        mock_sdk.models.generate_content.side_effect = [
            MagicMock(text="1回目の検索回答", candidates=[mock_candidate]),
            MagicMock(text="2回目の検索回答", candidates=[mock_candidate]),
        ]

        client = GenAIClient()
        session = ChatSession()
        session.history.append_turn(
            ChatTurn(
                turn_id="t1",
                user_prompt="内部コード確認",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text="SECRET_VALUE = 'DUMMY_CONFIDENTIAL_DATA'"
                            )
                        ],
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
        search_agent.search("FastAPI OAuth2 使い方")
        search_agent.search("Python 3.14 リリース予定日")

        first_contents = mock_sdk.models.generate_content.call_args_list[0].kwargs[
            "contents"
        ]
        second_contents = mock_sdk.models.generate_content.call_args_list[1].kwargs[
            "contents"
        ]

        assert len(first_contents) == 1
        assert first_contents[0].parts[0].text == "FastAPI OAuth2 使い方"
        assert "DUMMY_CONFIDENTIAL_DATA" not in first_contents[0].parts[0].text
        assert "INTERNAL_PRIVATE_SPEC_XYZ" not in first_contents[0].parts[0].text

        # 2回目の呼び出しでも1回目の質問・回答は一切含まれないこと
        assert len(second_contents) == 1
        assert second_contents[0].parts[0].text == "Python 3.14 リリース予定日"


def test_validate_search_query_safety_blocks_paths_secrets_and_oversized_text(tmp_path):
    """
    validate_search_query_safety が空文字・文字数超過・ローカルパス・機密トークン形式をブロックし、
    一般的な質問を通過させること
    """
    proj_dir = str(tmp_path / "secret_project_repo")

    # 1. 安全な一般質問は None を返す
    assert (
        validate_search_query_safety(
            "FastAPI 0.115 における lifespan イベントの推奨記法",
            forbidden_paths=[proj_dir],
        )
        is None
    )

    # 2. 空クエリ
    err_empty = validate_search_query_safety("   ")
    assert err_empty is not None and "[ERROR]" in err_empty

    # 3. 文字数上限超過（原稿や生コード丸ごと貼り付け防止）
    err_long = validate_search_query_safety("あ" * (MAX_TOOL_SEARCH_QUERY_CHARS + 1))
    assert err_long is not None and "[SECURITY_BLOCK]" in err_long

    # 4. ローカル物理パス混入
    err_path = validate_search_query_safety(
        f"{proj_dir}/src/main.py で発生した ImportError の解決法",
        forbidden_paths=[proj_dir],
    )
    assert err_path is not None and "[SECURITY_BLOCK]" in err_path

    # 5. 秘密鍵ヘッダ・APIキー形式・環境変数代入形式（スキャナ回避のため動的連結で検証）
    pem_header = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    fake_api_token = "AI" + "za" + ("B" * 28)
    env_assign = "DATABASE_PASSWORD=" + "MySuperSecretValue999"

    for unsafe_q in (pem_header, f"key {fake_api_token} error", env_assign):
        err_sec = validate_search_query_safety(unsafe_q)
        assert err_sec is not None and "[SECURITY_BLOCK]" in err_sec


def test_search_error_cancel_and_shared_token_tracker():
    """
    Web検索エラー時およびキャンセル時に succeeded=False / aborted=True を返すこと、
    および GenAIClient 共有時に通常対話コンテキスト量を上書きせず累積トークンが加算されること
    """
    cancel_ev = threading.Event()
    cancel_ev.set()

    with patch("google.genai.Client") as mock_client_cls, patch("time.sleep"):
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        client = GenAIClient()
        search_agent = SearchAgent(client=client)

        aborted_res = search_agent.search("中断テスト", cancel_event=cancel_ev)
        assert aborted_res.aborted is True
        assert aborted_res.succeeded is False

        mock_sdk.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
            Exception("503 Service Unavailable"),
        ]
        err_res = search_agent.search("Python 3.14")
        assert err_res.succeeded is False
        assert "Web検索中にサーバー混雑または通信エラーが発生しました" in err_res.response_text
        assert err_res.sources == []

        # 通常対話 + Web検索の共有クライアント集計検証
        resp_chat = MagicMock(
            function_calls=None,
            text="通常回答",
            usage_metadata=_make_usage(prompt=2000, candidates=100, total=2100),
        )
        resp_search = MagicMock(
            text="検索回答",
            candidates=[],
            usage_metadata=_make_usage(
                prompt=400, candidates=300, thoughts=50, tool_use=150, total=900
            ),
        )
        mock_sdk.models.generate_content.side_effect = [resp_chat, resp_search]

        session = ChatSession()
        chat_agent = ChatAgent(client=client, session=session)
        chat_agent.process_prompt("質問", on_log_append=lambda _: None)
        ok_search = search_agent.search("検索クエリ")
        assert ok_search.succeeded is True
        session.materials.add_search_result(
            ok_search.query, ok_search.response_text, ok_search.sources
        )

        usage = chat_agent.get_token_usage()
        assert usage["last_prompt_tokens"] == 2000
        assert usage["cumulative_total_tokens"] == 2100 + 900
        assert usage["chat_api_calls"] == 1
        assert usage["search_api_calls"] == 1
        assert usage["materials_count"] == 1
