"""
Google Search Grounding 専用のステートレス Web 検索エージェント（SearchAgent）モジュール。

機密情報およびローカル環境情報の漏洩を防止するため、通常対話の履歴（ChatSession / MaterialStore）や
ローカル操作ツールとは完全に隔離され、かつ過去の検索履歴も引き継がない1問1答の独立コンテキストで検索を実行します。
"""

from dataclasses import dataclass, field
import re
import threading
from typing import Callable

from google.genai import types

from gemio.core.agent import emit_agent_log
from gemio.core.client import GenAIClient
from gemio.core.context.materials import MaterialStore
from gemio.core.models import AgentLogCategory, get_now_time_text
from gemio.core.prompts import DEFAULT_SEARCH_INSTRUCTION

WEB_SEARCH_TOOLSET_NAME = "web_search"
MAX_TOOL_SEARCH_QUERY_CHARS = 600

# 検索クエリへの混入を機械的にブロックする既知の機密トークン・秘密鍵パターン
_SECRET_TOKEN_RE = re.compile(
    r"(?:"
    r"-----BEGIN [A-Z0-9 _-]+-----"
    r"|\bAIza[0-9A-Za-z_-]{20,}\b"
    r"|\bsk-[0-9A-Za-z_-]{20,}\b"
    r"|\bgh[pousr]_[0-9A-Za-z]{20,}\b"
    r"|\bgithub_pat_[0-9A-Za-z_]{20,}\b"
    r"|\bxox[baprs]-[0-9A-Za-z-]{15,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r")"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b[A-Z0-9_]*(?:API_KEY|SECRET|SECRET_KEY|TOKEN|PASSWORD|PRIVATE_KEY)\s*[:=]\s*['\"]?[^\s'\"]{8,}"
)


@dataclass
class SearchResult:
    """Web検索の1回の実行結果を表すデータクラス。"""

    query: str
    response_text: str
    sources: list[dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    succeeded: bool = True
    aborted: bool = False


def validate_search_query_safety(
    query: str,
    *,
    forbidden_paths: list[str] | None = None,
    max_chars: int = MAX_TOOL_SEARCH_QUERY_CHARS,
) -> str | None:
    """
    AIツールから渡された検索プロンプトに、長大な生テキスト・ローカル物理パス・機密トークンが
    含まれていないかを事前検査します。

    Returns:
        安全な場合は None、ブロックすべき問題がある場合は AI 向けのエラー案内文字列。
    """
    cleaned = (query or "").strip()
    if not cleaned:
        return "[ERROR] 検索クエリが空です。調査したい具体的な質問を指定してください。"

    if len(cleaned) > max_chars:
        return (
            f"[SECURITY_BLOCK] 検索クエリが長すぎます（{len(cleaned)}文字 > 上限{max_chars}文字）。"
            "作業中の原稿抜粋やソースコード・ログ全文を貼り付けず、"
            "公開されている一般名称・概念のみを用いた短い自己完結した質問文に要約してください。"
        )

    if forbidden_paths:
        lowered_query = cleaned.lower()
        norm_slash_query = lowered_query.replace("\\", "/")
        for raw_path in forbidden_paths:
            if not raw_path:
                continue
            stripped_path = raw_path.strip()
            norm_path = stripped_path.replace("\\", "/").rstrip("/")
            if len(norm_path) < 4:
                continue
            if (
                stripped_path.lower() in lowered_query
                or norm_path.lower() in norm_slash_query
            ):
                return (
                    "[SECURITY_BLOCK] 検索クエリにローカル環境のパス情報が含まれているため送信をブロックしました。"
                    "ローカルパスや固有のディレクトリ名を除去し、一般化した質問文に書き直してください。"
                )

    if _SECRET_TOKEN_RE.search(cleaned) or _SECRET_ASSIGNMENT_RE.search(cleaned):
        return (
            "[SECURITY_BLOCK] 検索クエリに秘密鍵や認証トークン等の機密情報と疑われる文字列が含まれているため"
            "送信をブロックしました。機密情報を完全に除外して質問を書き直してください。"
        )

    return None


def format_search_result_for_tool(result: SearchResult) -> str:
    """
    SearchResult を ChatAgent への function_response 返却用テキストへ整形します。
    暗号化リダイレクトURL（vertexaisearch等）は除外し、タイトルとドメインのみを記載します。
    """
    lines = [
        "【Web検索結果（外部最新情報）】",
        "※以下はGoogle検索エージェントから返却された外部情報です。対象時期・前提条件・手元のローカル環境との差異に注意して扱ってください。",
        "",
        result.response_text.strip(),
    ]

    if result.sources:
        lines.append("")
        lines.append("■ 参照元ソース:")
        for src in result.sources:
            display = MaterialStore.extract_source_display(
                src.get("title", ""),
                src.get("url", ""),
            )
            lines.append(f"- {display}")

    return "\n".join(lines)


class SearchAgent:
    """
    Google Search Grounding を使用して1問1答（ステートレス）で Web 調査を行い、
    回答と参照元ソースを返す専用エージェントクラス。
    ローカル履歴や過去の検索履歴は一切保持・送信しません。
    """

    def __init__(
        self,
        client: GenAIClient,
        search_instruction: str = DEFAULT_SEARCH_INSTRUCTION,
    ) -> None:
        self.client: GenAIClient = client
        self.search_instruction: str = (
            search_instruction or DEFAULT_SEARCH_INSTRUCTION
        )

    def search(
        self,
        query: str,
        on_log_append: Callable | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> SearchResult:
        """
        Google Search Grounding を用いて単発の検索プロンプトを実行し、SearchResult を返します。

        Args:
            query: 検索・質問内容の文字列。
            on_log_append: ログ通知コールバック（任意）。
            cancel_event: 検索キャンセル通知用のイベント（任意）。
        """
        cleaned_query = query.strip()
        start_time = get_now_time_text()
        if cancel_event is not None and cancel_event.is_set():
            cancel_msg = "ユーザーによってWeb検索が中断されました。"
            emit_agent_log(
                on_log_append,
                f"{start_time}\n{cancel_msg}",
                AgentLogCategory.ABORT,
            )
            return SearchResult(
                query=cleaned_query,
                response_text=cancel_msg,
                sources=[],
                created_at=start_time,
                succeeded=False,
                aborted=True,
            )

        emit_agent_log(
            on_log_append,
            f"{start_time}\n[Search] {cleaned_query}",
            AgentLogCategory.SEARCH,
        )

        request_contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=cleaned_query)],
            )
        ]

        search_config = {
            "tools": [{"google_search": {}}],
            "system_instruction": self.search_instruction,
            "temperature": 0.0,
        }

        def on_retry_warn(warn_msg: str) -> None:
            emit_agent_log(on_log_append, warn_msg, AgentLogCategory.WARNING)

        try:
            response = self.client.generate_content(
                contents=request_contents,
                config=search_config,
                on_retry_warning=on_retry_warn,
                usage_source="search",
            )
        except Exception as e:
            if cancel_event is not None and cancel_event.is_set():
                cancel_time = get_now_time_text()
                cancel_msg = "ユーザーによってWeb検索が中断されました。"
                emit_agent_log(
                    on_log_append,
                    f"{cancel_time}\n{cancel_msg}",
                    AgentLogCategory.ABORT,
                )
                return SearchResult(
                    query=cleaned_query,
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
                query=cleaned_query,
                response_text=error_text,
                sources=[],
                created_at=err_time,
                succeeded=False,
            )

        if cancel_event is not None and cancel_event.is_set():
            cancel_time = get_now_time_text()
            cancel_msg = "ユーザーによってWeb検索が中断されました。"
            emit_agent_log(
                on_log_append,
                f"{cancel_time}\n{cancel_msg}",
                AgentLogCategory.ABORT,
            )
            return SearchResult(
                query=cleaned_query,
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

        return SearchResult(
            query=cleaned_query,
            response_text=response_text,
            sources=sources,
            created_at=done_time,
            succeeded=True,
        )
