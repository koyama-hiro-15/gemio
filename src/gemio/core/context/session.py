"""
対話セッション状態（ChatSession）管理モジュール。

ChatHistory（ターン単位の対話履歴）と MaterialStore（作業前提資料ストア）を統合管理し、
巻き戻し、コンテキスト要約圧縮、コピー・再表示用エクスポート、および
履歴本体を肥大化させない前提資料の先頭インジェクションを提供します。
"""

from typing import Callable

from google.genai import types

from gemio.core.client import GenAIClient, TokenUsageTracker
from gemio.core.context.compactor import CompactionResult, ContextCompactor
from gemio.core.context.history import ChatHistory, ChatTurn
from gemio.core.context.materials import MaterialStore


class ChatSession:
    """
    1つの対話セッションの状態（会話履歴＋作業前提資料）を保持・操作するクラス。
    """

    def __init__(
        self,
        history: ChatHistory | None = None,
        materials: MaterialStore | None = None,
    ) -> None:
        self.history: ChatHistory = history or ChatHistory()
        self.materials: MaterialStore = materials or MaterialStore()

    def rewind(self, steps: int = 1) -> list[ChatTurn]:
        """指定ターン数だけ対話履歴を安全に巻き戻し、取り消された ChatTurn リストを返します。"""
        return self.history.rewind(steps=steps)

    def compact(
        self,
        client: GenAIClient,
        keep_recent_turns: int = 2,
        model: str | None = None,
        on_retry_warning: Callable[[str], None] | None = None,
    ) -> CompactionResult:
        """古い対話ターン群を構造化要約へ圧縮し、直近ターンのみを生履歴として保持します。"""
        compactor = ContextCompactor(client=client)
        return compactor.compact(
            self.history,
            keep_recent_turns=keep_recent_turns,
            model=model,
            on_retry_warning=on_retry_warning,
        )

    def export_context_markdown(
        self,
        start: int | None = None,
        end: int | None = None,
        *,
        last_n: int | None = None,
        include_summary: bool = True,
        include_materials: bool = False,
        include_thoughts: bool = False,
    ) -> str:
        """
        コンテキスト再表示やクリップボードコピー向けに、指定範囲の履歴（および必要に応じ要約・作業前提資料）を
        整形済み Markdown テキストとして出力します。
        """
        sections: list[str] = []

        if include_materials and self.materials:
            sections.append(f"## 作業前提資料\n\n{self.materials.format_for_display()}")

        selected_turns = self.history.get_turns(start=start, end=end, last_n=last_n)
        turns_md = self.history.format_turns_markdown(
            turns=selected_turns,
            include_summary=include_summary,
            include_thoughts=include_thoughts,
        )
        if turns_md:
            sections.append(turns_md)

        return "\n\n===\n\n".join(sections)

    def build_request_contents(
        self,
        pending_contents: list[types.Content] | None = None,
    ) -> list[types.Content]:
        """
        AIモデルへのAPIリクエスト送信用の contents リスト（スナップショット）を構築します。
        作業前提資料が存在する場合、末尾から直近のテキスト形式ユーザー指示の先頭へ資料ブロックを合成します。
        対話履歴本体（self.history）および pending_contents の元データは変更せずクリーンに保ちます。
        """
        contents = self.history.to_contents()
        if pending_contents:
            contents.extend(pending_contents)

        if not self.materials:
            return contents

        formatted_materials = self.materials.format_for_prompt()
        if not formatted_materials:
            return contents

        for i in range(len(contents) - 1, -1, -1):
            content = contents[i]
            if content.role == "user" and content.parts:
                first_part = content.parts[0]
                if hasattr(first_part, "text") and first_part.text is not None:
                    composed_text = (
                        f"{formatted_materials}\n\n【ユーザーの指示】\n{first_part.text}"
                    )
                    contents[i] = types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=composed_text)],
                    )
                    break

        return contents

    def reset(
        self,
        clear_materials: bool = False,
        token_tracker: TokenUsageTracker | None = None,
    ) -> dict[str, int]:
        """
        対話履歴（および指定時は作業前提資料・トークン統計）をリセットし、リセット前の統計サマリを返します。
        """
        prev_context_tokens = token_tracker.last_prompt_tokens if token_tracker else 0
        prev_total_tokens = token_tracker.cumulative_total_tokens if token_tracker else 0
        total_mats = len(self.materials)

        cleared_messages = self.history.clear()
        if clear_materials:
            self.materials.clear()
        if token_tracker is not None:
            token_tracker.reset()

        return {
            "cleared_messages": cleared_messages,
            "previous_context_tokens": prev_context_tokens,
            "previous_total_tokens": prev_total_tokens,
            "cleared_materials": total_mats if clear_materials else 0,
            "retained_materials": 0 if clear_materials else total_mats,
        }
