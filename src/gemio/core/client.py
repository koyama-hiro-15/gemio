"""
Gemini API 通信基盤（指数バックオフ付き自動リトライ）およびトークン使用量集計モジュール。
"""

import random
import time
from typing import Any, Callable

from google import genai

from gemio.core.models import (
    DEFAULT_AI_MODEL_NAME,
    get_now_time_text,
    resolve_model_name,
)

try:
    from google.genai import errors
except ImportError:
    errors = None

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 2.0
DEFAULT_BACKOFF_FACTOR = 2.0


class TokenUsageTracker:
    """
    セッション内のAPI呼び出し回数、直近の対話コンテキスト量、および累積トークン消費量を追跡するクラス。
    通常対話（chat）、Web検索（search）、コンテキスト要約（compaction）を呼び出し元種別ごとに区別して記録します。
    """

    def __init__(self) -> None:
        self._usage: dict[str, int] = {}
        self.reset()

    def reset(self) -> None:
        """統計カウンタをすべて初期状態（0）にリセットします。"""
        self._usage = {
            "last_prompt_tokens": 0,
            "last_candidates_tokens": 0,
            "last_total_tokens": 0,
            "cumulative_prompt_tokens": 0,
            "cumulative_candidates_tokens": 0,
            "cumulative_thoughts_tokens": 0,
            "cumulative_cached_tokens": 0,
            "cumulative_tool_use_tokens": 0,
            "cumulative_total_tokens": 0,
            "chat_api_calls": 0,
            "search_api_calls": 0,
            "compaction_api_calls": 0,
            "total_api_calls": 0,
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        """メタデータ属性値が正の整数（boolを除く）である場合のみその数値を返し、それ以外は0を返します。"""
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return 0

    def record(self, response: Any, source: str = "chat") -> None:
        """
        Gemini APIレスポンスの usage_metadata からトークン使用量を抽出し、統計を更新します。

        Args:
            response: Gemini API のレスポンスオブジェクト。
            source: 呼び出し元種別 ("chat" | "search" | "compaction")。
        """
        if source == "search":
            self._usage["search_api_calls"] += 1
        elif source == "compaction":
            self._usage["compaction_api_calls"] += 1
        else:
            self._usage["chat_api_calls"] += 1
        self._usage["total_api_calls"] += 1

        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return

        prompt_tokens = self._safe_int(getattr(usage, "prompt_token_count", 0))
        candidates_tokens = self._safe_int(getattr(usage, "candidates_token_count", 0))
        thoughts_tokens = self._safe_int(getattr(usage, "thoughts_token_count", 0))
        cached_tokens = self._safe_int(getattr(usage, "cached_content_token_count", 0))
        tool_use_tokens = self._safe_int(getattr(usage, "tool_use_prompt_token_count", 0))
        total_tokens = self._safe_int(getattr(usage, "total_token_count", 0))

        if total_tokens == 0 and (
            prompt_tokens > 0 or candidates_tokens > 0 or thoughts_tokens > 0 or tool_use_tokens > 0
        ):
            total_tokens = prompt_tokens + candidates_tokens + thoughts_tokens + tool_use_tokens

        if source == "chat":
            self._usage["last_prompt_tokens"] = prompt_tokens
            self._usage["last_candidates_tokens"] = candidates_tokens
            self._usage["last_total_tokens"] = total_tokens

        self._usage["cumulative_prompt_tokens"] += prompt_tokens
        self._usage["cumulative_candidates_tokens"] += candidates_tokens
        self._usage["cumulative_thoughts_tokens"] += thoughts_tokens
        self._usage["cumulative_cached_tokens"] += cached_tokens
        self._usage["cumulative_tool_use_tokens"] += tool_use_tokens
        self._usage["cumulative_total_tokens"] += total_tokens

    @property
    def last_prompt_tokens(self) -> int:
        return self._usage["last_prompt_tokens"]

    @property
    def cumulative_total_tokens(self) -> int:
        return self._usage["cumulative_total_tokens"]

    def get_snapshot(
        self,
        history_messages: int = 0,
        materials_count: int = 0,
    ) -> dict[str, int]:
        """
        現在のコンテキスト量、累積トークン量、API呼び出し回数、および履歴・資料件数のスナップショット辞書を返します。
        """
        snapshot = dict(self._usage)
        snapshot["chat_history_messages"] = history_messages
        snapshot["materials_count"] = materials_count
        return snapshot


class GenAIClient:
    """
    Google GenAI SDK クライアントの保持、モデル切替、指数バックオフ付きリトライ、
    およびトークン使用量記録を一元管理する共通通信クライアントクラス。
    """

    def __init__(
        self,
        model_name: str = DEFAULT_AI_MODEL_NAME,
        api_key: str | None = None,
        token_tracker: TokenUsageTracker | None = None,
    ) -> None:
        if api_key:
            self._sdk_client = genai.Client(api_key=api_key)
        else:
            self._sdk_client = genai.Client()
        self.model_name = resolve_model_name(model_name)
        self.token_tracker = token_tracker or TokenUsageTracker()

    def set_model(self, model_name: str) -> None:
        """使用するAIモデルを変更します（エイリアス指定にも対応）。"""
        self.model_name = resolve_model_name(model_name)

    @staticmethod
    def is_retryable_error(err: Exception) -> bool:
        """
        発生した例外が自動リトライ対象（サーバー混雑、レートリミット、通信エラー等）かを判定します。
        """
        if errors and isinstance(err, errors.APIError):
            code = getattr(err, "code", None) or getattr(err, "status_code", None)
            if code in RETRYABLE_STATUS_CODES:
                return True
            if code and 400 <= code < 500:
                return False

        if isinstance(err, (ConnectionError, TimeoutError, OSError)):
            return True

        err_str = str(err).lower()
        retryable_keywords = [
            "503",
            "429",
            "500",
            "502",
            "504",
            "resource_exhausted",
            "unavailable",
            "rate limit",
            "server is overloaded",
            "internal error",
            "timeout",
            "timed out",
        ]
        return any(kw in err_str for kw in retryable_keywords)

    def generate_content(
        self,
        contents: list,
        config: dict[str, Any] | None = None,
        model: str | None = None,
        on_retry_warning: Callable[[str], None] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        initial_delay: float = DEFAULT_INITIAL_BACKOFF,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        usage_source: str = "chat",
    ) -> Any:
        """
        サーバー混雑や一時的エラーに対して指数バックオフでリトライを行いながらコンテンツ生成を実行します。
        """
        target_model = resolve_model_name(model) if model else self.model_name
        delay = initial_delay

        for attempt in range(max_retries + 1):
            try:
                response = self._sdk_client.models.generate_content(
                    model=target_model,
                    contents=contents,
                    config=config,
                )
                self.token_tracker.record(response, source=usage_source)
                return response
            except Exception as e:
                if attempt < max_retries and self.is_retryable_error(e):
                    jitter = random.uniform(-0.5, 0.5) * min(delay, 2.0)
                    wait_time = max(1.0, delay + jitter)
                    if on_retry_warning:
                        on_retry_warning(
                            f"{get_now_time_text()}\n"
                            f"[警告] サーバー混雑または通信エラーを検知しました ({e})。\n"
                            f"{wait_time:.1f}秒後に自動再試行します (試行 {attempt + 1}/{max_retries})..."
                        )
                    time.sleep(wait_time)
                    delay *= backoff_factor
                else:
                    raise

    def close(self) -> None:
        """内部の Google GenAI SDK クライアントをクローズします。"""
        self._sdk_client.close()
