"""
Google Search Grounding 専用の Web 検索エージェント（SearchAgent）モジュール。

機密情報およびローカル環境情報の漏洩を防止するため、通常対話の履歴（ChatSession / MaterialStore）や
ローカル操作ツールとは完全に隔離された独立コンテキストで検索を実行します。
"""

from dataclasses import dataclass, field
import threading
from typing import Callable

from google.genai import types

from gemio.core.agent import emit_agent_log
from gemio.core.client import GenAIClient
from gemio.core.models import AgentLogCategory, get_now_time_text
from gemio.core.prompts import DEFAULT_SEARCH_INSTRUCTION


@dataclass
class SearchResult:
    """Web検索の1回の実行結果を表すデータクラス。"""

    query: str
    response_text: str
    sources: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    succeeded: bool = True
    aborted: bool = False


class SearchAgent:
    """
    Google Search Grounding を使用して Web 調査を行い、回答と参照元ソースを返す専用エージェントクラス。
    検索結果の作業前提資料への登録は呼び出し側（CLI層等）で行い、本クラス内には蓄積しません。
    """

    def __init__(
        self,
        client: GenAIClient,
        search_instruction: str = DEFAULT_SEARCH_INSTRUCTION,
    ) -> None:
        self.client: GenAIClient = client
        self.search_instruction: str = search_instruction or DEFAULT_SEARCH_INSTRUCTION
        self.search_history: list[types.Content] = []

    @property
    def has_active_session(self) -> bool:
        """連続深掘り検索用の検索履歴が存在するかどうかを返します。"""
        return bool(self.search_history)

    def reset_history(self) -> None:
        """保持している連続検索文脈（search_history）をクリアします。"""
        self.search_history.clear()

    def search(
        self,
        query: str,
        on_log_append: Callable | None = None,
        new_session: bool = False,
        *,
        cancel_event: threading.Event | None = None,
    ) -> SearchResult:
        """
        Google Search Grounding を用いて検索プロンプトを実行し、SearchResult を返します。

        Args:
            query: ユーザーからの検索クエリ文字列。
            on_log_append: ログ通知コールバック（任意）。
            new_session: True の場合、直前の検索文脈をリセットして新規検索セッションを開始します。
            cancel_event: 検索キャンセル通知用のイベント（任意）。
        """
        if new_session:
            self.search_history.clear()

        start_time = get_now_time_text()
        if cancel_event is not None and cancel_event.is_set():
            cancel_msg = "ユーザーによってWeb検索が中断されました。"
            emit_agent_log(
                on_log_append,
                f"{start_time}\n{cancel_msg}",
                AgentLogCategory.ABORT,
            )
            return SearchResult(
                query=query,
                response_text=cancel_msg,
                sources=[],
                created_at=start_time,
                succeeded=False,
                aborted=True,
            )

        emit_agent_log(
            on_log_append,
            f"{start_time}\n[Search] {query}",
            AgentLogCategory.SEARCH,
        )

        search_user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"/search {query}")],
        )
        self.search_history.append(search_user_content)

        search_config = {
            "tools": [{"google_search": {}}],
            "system_instruction": self.search_instruction,
            "temperature": 0.0,
        }

        def on_retry_warn(warn_msg: str) -> None:
            emit_agent_log(on_log_append, warn_msg, AgentLogCategory.WARNING)

        try:
            response = self.client.generate_content(
                contents=list(self.search_history),
                config=search_config,
                on_retry_warning=on_retry_warn,
                usage_source="search",
            )
        except Exception as e:
            if self.search_history and self.search_history[-1].role == "user":
                self.search_history.pop()

            if cancel_event is not None and cancel_event.is_set():
                cancel_time = get_now_time_text()
                cancel_msg = "ユーザーによってWeb検索が中断されました。"
                emit_agent_log(
                    on_log_append,
                    f"{cancel_time}\n{cancel_msg}",
                    AgentLogCategory.ABORT,
                )
                return SearchResult(
                    query=query,
                    response_text=cancel_msg,
                    sources=[],
                    created_at=cancel_time,
                    succeeded=False,
                    aborted=True,
                )

            error_text = (
                f"申し訳ありません。Web検索中にサーバー混雑または通信エラーが発生しました: {e}"
            )
            err_time = get_now_time_text()
            emit_agent_log(
                on_log_append,
                f"{err_time}\n{error_text}",
                AgentLogCategory.WARNING,
            )
            return SearchResult(
                query=query,
                response_text=error_text,
                sources=[],
                created_at=err_time,
                succeeded=False,
            )

        if cancel_event is not None and cancel_event.is_set():
            if self.search_history and self.search_history[-1].role == "user":
                self.search_history.pop()
            cancel_time = get_now_time_text()
            cancel_msg = "ユーザーによってWeb検索が中断されました。"
            emit_agent_log(
                on_log_append,
                f"{cancel_time}\n{cancel_msg}",
                AgentLogCategory.ABORT,
            )
            return SearchResult(
                query=query,
                response_text=cancel_msg,
                sources=[],
                created_at=cancel_time,
                succeeded=False,
                aborted=True,
            )

        response_text = response.text or ""

        sources: list[dict[str, str]] = []
        if response.candidates:
            candidate = response.candidates[0]
            grounding_metadata = getattr(candidate, "grounding_metadata", None)
            if grounding_metadata:
                grounding_chunks = (
                    getattr(grounding_metadata, "grounding_chunks", None) or []
                )
                for chunk in grounding_chunks:
                    web = getattr(chunk, "web", None)
                    if web:
                        title = getattr(web, "title", "") or ""
                        uri = getattr(web, "uri", "") or ""
                        if uri and not any(s["url"] == uri for s in sources):
                            sources.append({"title": title, "url": uri})

        done_time = get_now_time_text()
        emit_agent_log(
            on_log_append,
            f"{done_time}\n{response_text}",
            AgentLogCategory.SEARCH,
        )

        search_model_content = types.Content(
            role="model",
            parts=[types.Part.from_text(text=response_text)],
        )
        self.search_history.append(search_model_content)

        return SearchResult(
            query=query,
            response_text=response_text,
            sources=sources,
            created_at=done_time,
            succeeded=True,
        )
