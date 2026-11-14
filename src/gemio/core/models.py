"""
AIモデル定義・エイリアス解決・トークン閾値・ログカテゴリ等のコア定数モジュール。
外部SDK（google-genai 等）に依存せず、どのレイヤーからも軽量にインポート可能です。
"""

import datetime

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
    UI層（コンソール表示や将来のTextualペイン振り分け）とコア層で共有します。
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    WARNING = "warning"
    ABORT = "abort"
    SEARCH = "search"


def resolve_model_name(input_name: str) -> str:
    """
    入力されたモデル名またはエイリアスを正規のモデル名に解決します。

    Args:
        input_name: ユーザーが指定したモデル名またはエイリアス。

    Returns:
        解決された正規のモデル名。
    """
    cleaned = input_name.strip()
    lowered = cleaned.lower()
    if lowered in MODEL_ALIASES:
        return MODEL_ALIASES[lowered]
    return cleaned


def get_now_time_text() -> str:
    """
    現在時刻のフォーマット済み文字列（"HH:MM:SS" 形式）を取得します。
    """
    return datetime.datetime.now().strftime("%H:%M:%S")
