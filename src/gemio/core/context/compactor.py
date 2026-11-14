"""
対話コンテキストの要約・圧縮（Context Compaction）モジュール。

長時間のセッションで蓄積した古い対話ターン群を構造化サマリへ圧縮し、
直近の生ターンを維持したままコンテキスト肥大化（Context Rot）を解消します。
"""

from dataclasses import dataclass
from typing import Callable

from google.genai import types

from gemio.core.client import GenAIClient
from gemio.core.context.history import ChatHistory, ChatTurn

MAX_TURN_TEXT_FOR_COMPACTION = 4_000

DEFAULT_COMPACTION_INSTRUCTION = """
あなたはAIアシスタントの対話コンテキストを正確に引き継ぐための「履歴要約スペシャリスト」です。
与えられた「これまでの要約（存在する場合）」と「古い対話ターン履歴」を統合し、
後続の対話でAIが文脈を一切失わずに作業を継続できるよう、簡潔かつ網羅的な構造化サマリを作成してください。

【要約ルール】
- 対話に現れた客観的な事実・決定事項・相対パス名・識別子のみを記録し、推測で情報を補完しないでください。
- 以下の4つの見出し（Markdown形式）で整理してください（該当事項がない項目は「特になし」と記載）：
  1. **目的・背景**: ユーザーが達成しようとしている目標や全体要件
  2. **実施済みの作業・変更点**: 調査結果、作成・更新・削除した対象、実行した主要ツール操作
  3. **決定事項・制約条件**: ユーザーとの合意事項、採用した設計方針、守るべきルール
  4. **現在の状況・残課題**: 直近の進行状況や、まだ完了していない後続タスク
"""


def _clip_turn_text(text: str, max_chars: int = MAX_TURN_TEXT_FOR_COMPACTION) -> str:
    """要約入力プロンプトが過大にならないよう、単一テキストの長さを安全に制限します。"""
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    half = max_chars // 2
    omitted = len(cleaned) - (half * 2)
    return f"{cleaned[:half]}\n... [中略: {omitted} 文字省略] ...\n{cleaned[-half:]}"


@dataclass
class CompactionResult:
    """コンテキスト要約・圧縮処理の実行結果を表すデータクラス。"""

    compacted: bool
    summarized_turns: int
    retained_turns: int
    summary_text: str
    reason: str = ""


class ContextCompactor:
    """
    GenAIClient を用いて ChatHistory の古い対話ターン群を構造化要約へ圧縮するクラス。
    """

    def __init__(
        self,
        client: GenAIClient,
        compaction_instruction: str = DEFAULT_COMPACTION_INSTRUCTION,
    ) -> None:
        self.client: GenAIClient = client
        self.compaction_instruction: str = (
            compaction_instruction or DEFAULT_COMPACTION_INSTRUCTION
        )

    @staticmethod
    def build_compaction_prompt(
        existing_summary: str | None,
        turns_to_summarize: list[ChatTurn],
    ) -> str:
        """既存の要約文と圧縮対象ターン群から、要約生成用の入力テキストを構築します。"""
        sections: list[str] = []
        if existing_summary and existing_summary.strip():
            sections.append(
                "【これまでの要約（前回までの引き継ぎ内容）】\n"
                f"{existing_summary.strip()}"
            )

        turn_lines: list[str] = ["【今回新たに要約へ統合する対話ターン履歴】"]
        for idx, turn in enumerate(turns_to_summarize, 1):
            status_note = " (※途中中断ターン)" if turn.aborted else ""
            turn_lines.append(f"--- ターン {idx} ({turn.turn_id}){status_note} ---")
            turn_lines.append(f"[ユーザーの指示]\n{_clip_turn_text(turn.user_prompt)}")

            tool_names = turn.extract_tool_names()
            if tool_names:
                turn_lines.append(f"[実行されたツール] {', '.join(tool_names)}")

            turn_lines.append(
                f"[AIの応答]\n{_clip_turn_text(turn.assistant_response)}"
            )
            turn_lines.append("")

        sections.append("\n".join(turn_lines))
        sections.append(
            "上記の内容を統合し、最新の引き継ぎ要約を作成してください。"
        )
        return "\n\n".join(sections)

    def compact(
        self,
        history: ChatHistory,
        keep_recent_turns: int = 2,
        model: str | None = None,
        on_retry_warning: Callable[[str], None] | None = None,
    ) -> CompactionResult:
        """
        直近 `keep_recent_turns` 件のターンを残し、それより古いターン群を要約して履歴を圧縮します。
        """
        keep = max(0, int(keep_recent_turns))
        if history.turn_count <= keep:
            return CompactionResult(
                compacted=False,
                summarized_turns=0,
                retained_turns=history.turn_count,
                summary_text=history.summary_text or "",
                reason="要約対象となる古い対話ターンがありません。",
            )

        turns_to_summarize = (
            history.turns[:-keep] if keep > 0 else list(history.turns)
        )
        prompt_text = self.build_compaction_prompt(
            history.summary_text,
            turns_to_summarize,
        )

        request_contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt_text)],
            )
        ]
        config = {
            "system_instruction": self.compaction_instruction,
            "temperature": 0.0,
            "response_mime_type": "text/plain",
        }

        response = self.client.generate_content(
            contents=request_contents,
            config=config,
            model=model,
            on_retry_warning=on_retry_warning,
            usage_source="compaction",
        )
        new_summary = (getattr(response, "text", "") or "").strip()
        if not new_summary:
            return CompactionResult(
                compacted=False,
                summarized_turns=0,
                retained_turns=history.turn_count,
                summary_text=history.summary_text or "",
                reason="生成された要約テキストが空だったため、履歴の圧縮をスキップしました。",
            )

        summarized_count = history.apply_compaction(
            new_summary,
            keep_recent_turns=keep,
        )
        return CompactionResult(
            compacted=True,
            summarized_turns=summarized_count,
            retained_turns=history.turn_count,
            summary_text=new_summary,
        )
