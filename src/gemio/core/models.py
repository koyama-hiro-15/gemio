"""
AIモデル定義・スペック構造（ModelInfo）・エイリアス解決・トークン閾値・ログカテゴリ等のコア定数モジュール。
外部SDK（google-genai 等）に依存せず、どのレイヤーからも軽量にインポート可能です。
"""

from dataclasses import dataclass
import datetime
from typing import Any

DEFAULT_AI_MODEL_NAME = "gemini-flash-latest"

MODEL_PRESETS = [
    ("gemini-flash-latest", "最新Flashモデル（高速・標準、常に最新に追従）"),
    ("gemini-4-argon", "最新最上位モデル（高度な推論・複雑なタスク）"),
    ("gemini-3.8-flash", "Flash 3.8（バージョン固定）"),
    ("gemini-3.6-flash", "Flash 3.6（前世代固定）"),
    ("gemini-3.5-flash-lite", "Flash 3.5 Lite（軽量・低レイテンシ）"),
    ("gemini-3.1-flash-lite", "Flash 3.1 Lite（旧世代軽量）"),
]

MODEL_ALIASES = {
    # gemini-flash-latest (デフォルト)
    "flash": "gemini-flash-latest",
    "latest": "gemini-flash-latest",
    "flash-latest": "gemini-flash-latest",
    "default": "gemini-flash-latest",
    # gemini-4-argon
    "argon": "gemini-4-argon",
    "gemini-4": "gemini-4-argon",
    "4": "gemini-4-argon",
    # gemini-3.8-flash (バージョン固定)
    "flash-3.8": "gemini-3.8-flash",
    "3.8": "gemini-3.8-flash",
    # gemini-3.6-flash
    "flash-3.6": "gemini-3.6-flash",
    "3.6": "gemini-3.6-flash",
    # gemini-3.5-flash-lite
    "lite": "gemini-3.5-flash-lite",
    "flash-lite": "gemini-3.5-flash-lite",
    "3.5-lite": "gemini-3.5-flash-lite",
    "3.5": "gemini-3.5-flash-lite",
    # gemini-3.1-flash-lite
    "3.1-lite": "gemini-3.1-flash-lite",
    "3.1": "gemini-3.1-flash-lite",
}

CONTEXT_TOKEN_WARNING_THRESHOLD = 50_000
CONTEXT_TOKEN_DANGER_THRESHOLD = 150_000


class AgentLogCategory:
    """
    エージェントから出力されるログの種別を表す定数クラス。
    UI層とコア層で共有します。
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    THOUGHT = "thought"
    WARNING = "warning"
    ABORT = "abort"
    SEARCH = "search"


@dataclass(frozen=True)
class ModelInfo:
    """
    単一のAIモデルの識別情報・対応機能・トークン上限を表す不変データクラス。
    """

    model_id: str
    display_name: str = ""
    description: str = ""
    supports_thinking: bool = False
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    supported_actions: tuple[str, ...] = ()

    def format_context_limit(self) -> str:
        """最大入力トークン上限をコンパクトな表記（例: '1M', '128k'）へ整形します。"""
        limit = self.input_token_limit
        if limit is None or limit <= 0:
            return ""
        if limit >= 1_000_000:
            if limit % 1_048_576 == 0:
                return f"{limit // 1_048_576}M"
            if limit % 1_000_000 == 0:
                return f"{limit // 1_000_000}M"
            return f"{limit / 1_000_000:.1f}M"
        if limit >= 1_000:
            if limit % 1_024 == 0:
                return f"{limit // 1_024}k"
            return f"{limit // 1_000}k"
        return str(limit)

    def summary_label(self) -> str:
        """UI一覧やコマンドパレット向けの簡潔な説明ラベルを返します。"""
        base = self.display_name or self.description
        if self.display_name and self.description and self.display_name != self.description:
            first_line = self.description.splitlines()[0].strip()
            if len(first_line) <= 48:
                base = f"{self.display_name} ({first_line})"
        return base or "Gemini テキスト生成モデル"


def _safe_optional_int(value: Any) -> int | None:
    """値が正の整数（boolを除く）である場合のみその数値を返し、それ以外は None を返します。"""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _safe_str(value: Any) -> str:
    """値が文字列である場合のみ前後空白を除去して返し、それ以外は空文字列を返します。"""
    return value.strip() if isinstance(value, str) else ""


def normalize_model_id(raw_name: str) -> str:
    """
    APIリソース名（'models/gemini-...' や 'publishers/google/models/...'）から
    プレフィックスを除去し、正規化されたモデルIDを返します。
    """
    cleaned = raw_name.strip()
    if cleaned.startswith("publishers/google/models/"):
        cleaned = cleaned.removeprefix("publishers/google/models/")
    elif cleaned.startswith("models/"):
        cleaned = cleaned.removeprefix("models/")
    return cleaned


def resolve_model_name(input_name: str) -> str:
    """
    入力されたモデル名・リソース名・またはエイリアスを正規のモデルIDに解決します。

    Args:
        input_name: ユーザーが指定したモデル名またはエイリアス。

    Returns:
        解決された正規のモデルID。
    """
    cleaned = normalize_model_id(input_name)
    lowered = cleaned.lower()
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    return cleaned


def is_text_generation_model(
    sdk_model: Any,
    *,
    exclude_specialized_media: bool = True,
) -> bool:
    """
    SDKの Model オブジェクトがテキスト生成（generateContent）に対応しているかを判定します。
    """
    raw_name = _safe_str(getattr(sdk_model, "name", None))
    if not raw_name:
        return False

    raw_actions = getattr(sdk_model, "supported_actions", None)
    if not isinstance(raw_actions, (list, tuple, set)):
        return False
    actions = [a for a in raw_actions if isinstance(a, str)]
    if "generateContent" not in actions:
        return False

    if exclude_specialized_media:
        lowered_id = normalize_model_id(raw_name).lower()
        if lowered_id.endswith("-tts") or "-image" in lowered_id:
            return False

    return True


def is_thinking_supported(sdk_model: Any) -> bool:
    """
    SDKの Model オブジェクトが Thinking（思考機能）に対応しているかを厳密に判定します。
    """
    return getattr(sdk_model, "thinking", None) is True


def model_info_from_sdk(sdk_model: Any, fallback_id: str = "") -> ModelInfo:
    """
    SDKの types.Model（または同等の属性を持つオブジェクト）から不変の ModelInfo を構築します。
    """
    raw_name = _safe_str(getattr(sdk_model, "name", None))
    model_id = normalize_model_id(raw_name) if raw_name else normalize_model_id(fallback_id)

    raw_actions = getattr(sdk_model, "supported_actions", None)
    actions: tuple[str, ...] = ()
    if isinstance(raw_actions, (list, tuple, set)):
        actions = tuple(a for a in raw_actions if isinstance(a, str))

    return ModelInfo(
        model_id=model_id,
        display_name=_safe_str(getattr(sdk_model, "display_name", None)),
        description=_safe_str(getattr(sdk_model, "description", None)),
        supports_thinking=is_thinking_supported(sdk_model),
        input_token_limit=_safe_optional_int(getattr(sdk_model, "input_token_limit", None)),
        output_token_limit=_safe_optional_int(getattr(sdk_model, "output_token_limit", None)),
        supported_actions=actions,
    )


def get_fallback_model_catalog() -> list[ModelInfo]:
    """
    API未接続時・オフライン時・モックテスト環境向けの標準フォールバックモデル一覧を返します。
    """
    return [
        ModelInfo(
            model_id=m_id,
            display_name="",
            description=m_desc,
            supports_thinking=False,
            supported_actions=("generateContent",),
        )
        for m_id, m_desc in MODEL_PRESETS
    ]


def sort_model_catalog(models: list[ModelInfo]) -> list[ModelInfo]:
    """
    モデル一覧を「デフォルトモデル ➔ プリセット代表モデル ➔ Thinking対応モデル ➔ 名前順」に整列します。
    """
    preset_rank = {m_id: idx for idx, (m_id, _) in enumerate(MODEL_PRESETS)}

    def _sort_key(info: ModelInfo) -> tuple[int, int, int, str]:
        if info.model_id == DEFAULT_AI_MODEL_NAME:
            return (0, 0, 0, info.model_id)
        if info.model_id in preset_rank:
            return (1, preset_rank[info.model_id], 0, info.model_id)
        is_gemini = 0 if info.model_id.startswith("gemini-") else 1
        think_rank = 0 if info.supports_thinking else 1
        return (2, is_gemini, think_rank, info.model_id)

    return sorted(models, key=_sort_key)


def get_now_time_text() -> str:
    """
    現在時刻のフォーマット済み文字列（"HH:MM:SS" 形式）を取得します。
    """
    return datetime.datetime.now().strftime("%H:%M:%S")


__all__ = [
    "AgentLogCategory",
    "CONTEXT_TOKEN_DANGER_THRESHOLD",
    "CONTEXT_TOKEN_WARNING_THRESHOLD",
    "DEFAULT_AI_MODEL_NAME",
    "MODEL_ALIASES",
    "MODEL_PRESETS",
    "ModelInfo",
    "get_fallback_model_catalog",
    "get_now_time_text",
    "is_text_generation_model",
    "is_thinking_supported",
    "model_info_from_sdk",
    "normalize_model_id",
    "resolve_model_name",
    "sort_model_catalog",
]
