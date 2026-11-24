from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gemio.core.client import GenAIClient, TokenUsageTracker
from gemio.core.models import (
    AgentLogCategory,
    DEFAULT_AI_MODEL_NAME,
    EXCLUDED_MODEL_KEYWORDS,
    MODEL_PRESETS,
    ModelInfo,
    extract_model_version,
    get_fallback_model_catalog,
    is_text_generation_model,
    is_thinking_supported,
    model_info_from_sdk,
    normalize_model_id,
    resolve_model_name,
    sort_model_catalog,
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
    """モデル名のエイリアス解決、Gemini 4 新モデル群、gemini-flash-latest 直接・エイリアス互換が正しく動作すること"""
    assert DEFAULT_AI_MODEL_NAME == "gemini-4-neon"
    assert resolve_model_name("default") == "gemini-4-neon"
    assert resolve_model_name("neon") == "gemini-4-neon"
    assert resolve_model_name("gemini-4") == "gemini-4-neon"
    assert resolve_model_name("4") == "gemini-4-neon"
    assert resolve_model_name("carbon") == "gemini-4-carbon"
    assert resolve_model_name("argon") == "gemini-4-argon"
    assert resolve_model_name("hydrogen") == "gemini-4-hydrogen"

    # gemini-flash-latest はプリセット・エイリアスから削除せず、直接入力時もそのまま処理されること
    assert resolve_model_name("gemini-flash-latest") == "gemini-flash-latest"
    assert resolve_model_name("flash") == "gemini-flash-latest"
    assert resolve_model_name("latest") == "gemini-flash-latest"
    assert resolve_model_name("flash-latest") == "gemini-flash-latest"

    assert resolve_model_name("flash-3.8") == "gemini-3.8-flash"
    assert resolve_model_name("3.8") == "gemini-3.8-flash"
    assert resolve_model_name("flash-3.6") == "gemini-3.6-flash"
    assert resolve_model_name("3.6") == "gemini-3.6-flash"
    assert resolve_model_name("lite") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.5-lite") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.5") == "gemini-3.5-flash-lite"
    assert resolve_model_name("3.1-lite") == "gemini-3.1-flash-lite"
    assert resolve_model_name("3.1") == "gemini-3.1-flash-lite"

    assert resolve_model_name("  CARBON  ") == "gemini-4-carbon"
    assert resolve_model_name("Flash-3.8") == "gemini-3.8-flash"
    assert resolve_model_name("  custom-fine-tuned-model  ") == "custom-fine-tuned-model"

    preset_ids = [m_id for m_id, _ in MODEL_PRESETS]
    for expected_id in (
        "gemini-4-neon",
        "gemini-4-carbon",
        "gemini-4-argon",
        "gemini-4-hydrogen",
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
    ):
        assert expected_id in preset_ids

    assert AgentLogCategory.TOOL == "tool"
    assert not hasattr(AgentLogCategory, "SEARCH")


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

        client = GenAIClient()
        mock_sdk_cls.assert_called_once_with()
        assert client.model_name == "gemini-4-neon"

        client.set_model("flash")
        assert client.model_name == "gemini-flash-latest"

        client.set_model("carbon")
        assert client.model_name == "gemini-4-carbon"

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


def test_token_usage_tracker_separates_chat_and_compaction_sources():
    """
    TokenUsageTracker が chat / compaction のAPI呼び出し回数を区別し、
    compaction の入力トークン数で直近対話コンテキスト量（last_prompt_tokens）を上書きしないこと
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

    # 2. 要約圧縮 (compaction) -> last_prompt_tokens(2500) は維持され、累積に加算される
    resp_compact = SimpleNamespace(
        usage_metadata=_make_usage(prompt=1800, candidates=300, total=2100)
    )
    tracker.record(resp_compact, source="compaction")
    snap2 = tracker.get_snapshot()
    assert snap2["last_prompt_tokens"] == 2500
    assert snap2["compaction_api_calls"] == 1
    assert snap2["total_api_calls"] == 2
    assert snap2["cumulative_total_tokens"] == 2800 + 2100


def test_models_module_has_no_sdk_dependency_and_filters_gemini3_plus_text_models():
    """
    1. core/models.py が google.genai 等の外部SDKに依存しないこと
    2. gemini-3 以上 + generateContent 対応 + 7種の除外キーワード不含の条件で正確に絞り込めること
    3. Thinking 対応の厳密判定（MagicMock 耐性含む）およびソート条件（動的優先 -> 新バージョン優先、同一バージョン順序維持）が動作すること
    """
    import ast
    from pathlib import Path

    models_py = Path(__file__).resolve().parent.parent / "src" / "gemio" / "core" / "models.py"
    tree = ast.parse(models_py.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "google" not in imported_roots

    assert normalize_model_id("models/gemini-4-neon") == "gemini-4-neon"
    assert (
        normalize_model_id("publishers/google/models/gemini-3.8-flash")
        == "gemini-3.8-flash"
    )
    assert extract_model_version("models/gemini-4-neon") == (4,)
    assert extract_model_version("gemini-3.8-flash") == (3, 8)
    assert extract_model_version("gemini-flash-latest") == ()
    assert extract_model_version("gemma-3-27b-it") == ()

    # 採用対象（gemini-3以上 かつ generateContent あり かつ 除外キーワードなし）
    m_v4 = SimpleNamespace(
        name="models/gemini-4-neon",
        display_name="Gemini 4 Neon",
        description="Balanced reasoning model",
        supported_actions=["generateContent", "countTokens"],
        thinking=True,
        input_token_limit=1_048_576,
        output_token_limit=65_536,
    )
    m_v38 = SimpleNamespace(
        name="models/gemini-3.8-flash",
        supported_actions=["generateContent"],
        thinking=True,
    )
    assert is_text_generation_model(m_v4) is True
    assert is_text_generation_model(m_v38) is True

    # 除外対象: gemini-2以下 / gemini-直後が非数字 / 非Gemini / generateContentなし
    for invalid_name, actions in [
        ("models/gemini-2.5-pro", ["generateContent"]),
        ("models/gemini-2.5-flash", ["generateContent"]),
        ("models/gemini-2.0-flash", ["generateContent"]),
        ("models/gemini-2-flash", ["generateContent"]),
        ("models/gemini-1.5-pro", ["generateContent"]),
        ("models/gemini-flash-latest", ["generateContent"]),
        ("models/gemini-exp-1206", ["generateContent"]),
        ("models/gemma-3-27b-it", ["generateContent"]),
        ("models/gemini-4-embedding", ["embedContent"]),
    ]:
        assert (
            is_text_generation_model(
                SimpleNamespace(name=invalid_name, supported_actions=actions)
            )
            is False
        ), f"Expected {invalid_name} to be filtered out"

    # 除外対象: 7種の除外キーワード部分一致（中間・末尾いずれも除外）
    assert EXCLUDED_MODEL_KEYWORDS == (
        "tts",
        "image",
        "live",
        "transcribe",
        "translate",
        "omni",
        "nano-banana",
    )
    for kw in EXCLUDED_MODEL_KEYWORDS:
        m_mid = SimpleNamespace(
            name=f"models/gemini-4-{kw}-preview",
            supported_actions=["generateContent"],
        )
        m_end = SimpleNamespace(
            name=f"models/gemini-3.8-flash-{kw}",
            supported_actions=["generateContent"],
        )
        assert is_text_generation_model(m_mid) is False
        assert is_text_generation_model(m_end) is False

    assert is_text_generation_model(MagicMock()) is False

    assert is_thinking_supported(m_v4) is True
    assert is_thinking_supported(SimpleNamespace(thinking=False)) is False
    assert is_thinking_supported(SimpleNamespace(thinking=None)) is False
    assert is_thinking_supported(MagicMock()) is False

    info: ModelInfo = model_info_from_sdk(m_v4)
    assert info.model_id == "gemini-4-neon"
    assert info.is_dynamic is True
    assert info.supports_thinking is True
    assert info.format_context_limit() == "1M"

    # ソート検証: 1. 動的取得モデル優先 -> 2. バージョン降順（同一バージョン内の順序は維持）
    mixed = [
        ModelInfo(model_id="gemini-4-carbon", is_dynamic=False),
        ModelInfo(model_id="gemini-3.5-flash-lite", is_dynamic=True),
        ModelInfo(model_id="gemini-4-hydrogen", is_dynamic=True),
        ModelInfo(model_id="gemini-3.8-flash", is_dynamic=True),
        ModelInfo(model_id="gemini-4-neon", is_dynamic=True),
        ModelInfo(model_id="gemini-flash-latest", is_dynamic=False),
        ModelInfo(model_id="gemini-3.1-flash-lite", is_dynamic=False),
    ]
    sorted_ids = [m.model_id for m in sort_model_catalog(mixed)]
    assert sorted_ids == [
        # 1. is_dynamic=True 群（バージョン降順、同じ4系内は hydrogen -> neon の入力順維持）
        "gemini-4-hydrogen",
        "gemini-4-neon",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        # 2. is_dynamic=False 群（バージョン降順）
        "gemini-4-carbon",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
    ]

    fallback_catalog = get_fallback_model_catalog()
    assert all(not m.is_dynamic for m in fallback_catalog)
    assert fallback_catalog[0].model_id == "gemini-4-neon"
    assert fallback_catalog[-1].model_id == "gemini-flash-latest"


def test_genai_client_dynamic_model_catalog_caching_and_thinking_fallback():
    """
    1. list_available_models() が gemini-3 以上のテキスト生成モデルのみを抽出し、バージョン降順で個別キャッシュにも登録すること
    2. supports_thinking() がキャッシュを利用して重複APIコールを行わないこと
    3. ThinkingConfig が万一 400 エラーで拒否された際に自動で設定を外して再試行すること
    """
    from google.genai import types

    with patch("google.genai.Client") as mock_sdk_cls:
        mock_sdk = MagicMock()
        mock_sdk_cls.return_value = mock_sdk

        mock_sdk.models.list.return_value = [
            types.Model(
                name="models/gemini-3.5-flash-lite",
                display_name="Gemini 3.5 Flash Lite",
                supported_actions=["generateContent"],
                thinking=False,
                input_token_limit=1_048_576,
            ),
            types.Model(
                name="models/gemini-4-carbon",
                display_name="Gemini 4 Carbon",
                supported_actions=["generateContent"],
                thinking=True,
                input_token_limit=2_097_152,
            ),
            types.Model(
                name="models/gemini-4-neon",
                display_name="Gemini 4 Neon",
                supported_actions=["generateContent"],
                thinking=True,
                input_token_limit=1_048_576,
            ),
            types.Model(
                name="models/gemini-3.8-flash",
                display_name="Gemini 3.8 Flash",
                supported_actions=["generateContent"],
                thinking=True,
                input_token_limit=1_048_576,
            ),
            # 以下はすべて除外されるべきモデル
            types.Model(
                name="models/gemini-2.5-pro",
                supported_actions=["generateContent"],
                thinking=True,
            ),
            types.Model(
                name="models/gemini-4-tts",
                supported_actions=["generateContent"],
                thinking=False,
            ),
            types.Model(
                name="models/gemini-4-image-gen",
                supported_actions=["generateContent"],
                thinking=False,
            ),
            types.Model(
                name="models/gemini-flash-latest",
                supported_actions=["generateContent"],
                thinking=False,
            ),
            types.Model(
                name="models/embedding-001",
                supported_actions=["embedContent"],
            ),
        ]

        client = GenAIClient(model_name="gemini-4-carbon")
        catalog = client.list_available_models()
        ids = [m.model_id for m in catalog]
        assert ids == [
            "gemini-4-carbon",
            "gemini-4-neon",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
        ]
        assert mock_sdk.models.list.call_count == 1

        # キャッシュ済みのため再度の list() や models.get() なしで判定できること
        assert client.list_available_models() == catalog
        assert mock_sdk.models.list.call_count == 1
        assert client.supports_thinking("gemini-4-carbon") is True
        assert client.supports_thinking("gemini-3.5-flash-lite") is False
        assert mock_sdk.models.get.call_count == 0

        # 万一 ThinkingConfig 指定で 400 INVALID_ARGUMENT が返った場合、自動で外して成功すること
        ok_resp = MagicMock(text="フォールバック応答", usage_metadata=None)
        mock_sdk.models.generate_content.side_effect = [
            Exception("400 INVALID_ARGUMENT: Thinking config is not supported for this model"),
            ok_resp,
        ]
        res = client.generate_content(
            contents=["hi"],
            config={"thinking_config": types.ThinkingConfig(include_thoughts=True)},
        )
        assert res is ok_resp
        assert mock_sdk.models.generate_content.call_count == 2
        second_cfg = mock_sdk.models.generate_content.call_args_list[1].kwargs["config"]
        assert "thinking_config" not in second_cfg
        # 以降は自動的に Thinking 無効として記録されること
        assert client.supports_thinking("gemini-4-carbon") is False


def test_token_usage_tracker_set_context_tokens_and_client_count_tokens():
    """
    1. TokenUsageTracker.set_context_tokens が非負整数のみを受け付け、
       累積トークンやAPI呼び出し回数を変えずに last_prompt_tokens のみを更新すること
    2. GenAIClient.count_tokens が system_instruction を先頭に含めて total_tokens を返し、
       API例外や非整数応答時は例外を送出せず None を返すこと
    """
    from google.genai import types

    tracker = TokenUsageTracker()
    resp_chat = SimpleNamespace(
        usage_metadata=_make_usage(prompt=50_000, candidates=300, total=50_300)
    )
    tracker.record(resp_chat, source="chat")

    tracker.set_context_tokens(4_200)
    snap = tracker.get_snapshot()
    assert snap["last_prompt_tokens"] == 4_200
    assert snap["cumulative_total_tokens"] == 50_300
    assert snap["chat_api_calls"] == 1
    assert snap["total_api_calls"] == 1

    # bool や負数は無視されること
    tracker.set_context_tokens(True)  # type: ignore[arg-type]
    tracker.set_context_tokens(-10)
    assert tracker.last_prompt_tokens == 4_200

    with patch("google.genai.Client") as mock_sdk_cls:
        mock_sdk = MagicMock()
        mock_sdk_cls.return_value = mock_sdk
        client = GenAIClient(model_name="gemini-4-neon")

        base_contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="要約テキスト")],
            )
        ]
        mock_sdk.models.count_tokens.return_value = SimpleNamespace(total_tokens=1_234)
        counted = client.count_tokens(
            base_contents,
            system_instruction="システム指示",
        )
        assert counted == 1_234
        # 呼び出し引数には system_instruction が先頭に追加され、元リストは変更されないこと
        assert len(base_contents) == 1
        called_contents = mock_sdk.models.count_tokens.call_args.kwargs["contents"]
        assert len(called_contents) == 2
        assert called_contents[0].parts[0].text == "システム指示"

        # 例外発生時および非整数（MagicMock 等）応答時は安全に None を返すこと
        mock_sdk.models.count_tokens.side_effect = RuntimeError("network error")
        assert client.count_tokens(base_contents) is None

        mock_sdk.models.count_tokens.side_effect = None
        mock_sdk.models.count_tokens.return_value = MagicMock()
        assert client.count_tokens(base_contents) is None
