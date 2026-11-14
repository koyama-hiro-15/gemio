"""
ターン（ChatTurn）単位の対話履歴管理モジュール。

1回のプロンプト入力から（複数回のツール呼び出し往復を含む）最終回答までを1つの ChatTurn として構造化し、
ロール交互規約や function_call / function_response ペア整合性を壊さない安全な巻き戻し、
クリップボードコピー・再表示用の範囲抽出、および要約圧縮の適用を実現します。
"""

from dataclasses import dataclass, field

from google.genai import types


@dataclass
class ChatTurn:
    """
    1回のユーザー指示から最終回答までの1対話ターンを表すデータクラス。
    """

    turn_id: str
    user_prompt: str
    contents: list[types.Content] = field(default_factory=list)
    assistant_response: str = ""
    thoughts: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    aborted: bool = False
    created_at: str = ""

    def extract_tool_names(self) -> list[str]:
        """このターン内で呼び出されたツール関数名のリスト（呼び出し順）を返します。"""
        names: list[str] = []
        for content in self.contents:
            for part in content.parts or []:
                fc = getattr(part, "function_call", None)
                fname = getattr(fc, "name", None) if fc is not None else None
                if isinstance(fname, str) and fname:
                    names.append(fname)
        return names


class ChatHistory:
    """
    ChatTurn のリストおよび要約圧縮テキストを保持・管理する対話履歴クラス。
    """

    def __init__(self) -> None:
        self.turns: list[ChatTurn] = []
        self.summary_text: str | None = None
        self._turn_seq: int = 0

    def next_turn_id(self) -> str:
        """新しいターン用の連番識別子（'t1', 't2', ...）を発行します。"""
        self._turn_seq += 1
        return f"t{self._turn_seq}"

    def append_turn(self, turn: ChatTurn) -> None:
        """完了した対話ターンを履歴末尾に追加します。"""
        self.turns.append(turn)

    @property
    def turn_count(self) -> int:
        """現在保持しているターン数を返します。"""
        return len(self.turns)

    @property
    def last_turn(self) -> ChatTurn | None:
        """直近の対話ターンを返します（履歴が空の場合は None）。"""
        return self.turns[-1] if self.turns else None

    def get_last_prompt_text(self) -> str:
        """直近ターンのユーザー入力プロンプト文字列を返します（履歴が空の場合は空文字列）。"""
        return self.turns[-1].user_prompt if self.turns else ""

    def get_last_response_text(self) -> str:
        """直近ターンのAI最終回答テキストを返します（履歴が空の場合は空文字列）。"""
        return self.turns[-1].assistant_response if self.turns else ""

    def get_turn(self, identifier: int | str) -> ChatTurn | None:
        """
        1始まりのターン番号（負数の場合は末尾基準、例: -1 は直近ターン）または turn_id（例: 't1'）で
        単一の ChatTurn を取得します。該当しない場合は None を返します。
        """
        if not self.turns:
            return None

        if isinstance(identifier, int):
            if identifier > 0:
                idx = identifier - 1
                return self.turns[idx] if idx < len(self.turns) else None
            if identifier < 0:
                idx = len(self.turns) + identifier
                return self.turns[idx] if 0 <= idx < len(self.turns) else None
            return None

        cleaned = identifier.strip()
        if cleaned.lstrip("-").isdigit():
            return self.get_turn(int(cleaned))

        lowered = cleaned.lower()
        for turn in self.turns:
            if turn.turn_id.lower() == lowered:
                return turn
        return None

    def get_turns(
        self,
        start: int | None = None,
        end: int | None = None,
        *,
        last_n: int | None = None,
    ) -> list[ChatTurn]:
        """
        指定範囲の ChatTurn リスト（コピー）を取得します。

        Args:
            start: 開始ターン番号（1始まり・含む。省略時は先頭）。
            end: 終了ターン番号（1始まり・含む。省略時は末尾）。
            last_n: 直近から取得するターン数（指定時は start/end より優先）。
        """
        if not self.turns:
            return []

        if last_n is not None:
            if last_n <= 0:
                return []
            return list(self.turns[-last_n:])

        total = len(self.turns)
        s_idx = 0 if start is None else max(0, start - 1)
        e_idx = total if end is None else min(total, max(0, end))
        if s_idx >= e_idx:
            return []
        return list(self.turns[s_idx:e_idx])

    def format_turns_markdown(
        self,
        turns: list[ChatTurn] | None = None,
        *,
        include_summary: bool = False,
        include_user: bool = True,
        include_assistant: bool = True,
        include_thoughts: bool = False,
    ) -> str:
        """
        クリップボードへのコピーやコンテキスト再表示向けに、指定ターン群を整形済み Markdown 文字列へ変換します。
        """
        target_turns = self.turns if turns is None else turns
        blocks: list[str] = []

        if include_summary and self.summary_text:
            blocks.append(f"## これまでの対話の要約\n\n{self.summary_text.strip()}")

        for idx, turn in enumerate(target_turns, 1):
            turn_lines: list[str] = []
            header = f"### Turn {idx} ({turn.turn_id})"
            if turn.created_at:
                header += f" [{turn.created_at}]"
            turn_lines.append(header)

            if include_user:
                turn_lines.append(f"\n**User:**\n{turn.user_prompt}")

            if include_thoughts and turn.thoughts:
                joined_thoughts = "\n\n".join(turn.thoughts)
                turn_lines.append(f"\n**Thoughts:**\n{joined_thoughts}")

            if include_assistant:
                turn_lines.append(f"\n**Gemio:**\n{turn.assistant_response}")

            blocks.append("\n".join(turn_lines))

        return "\n\n---\n\n".join(blocks)

    def rewind(self, steps: int = 1) -> list[ChatTurn]:
        """
        末尾から指定されたターン数だけ対話履歴を巻き戻し、取り消された ChatTurn のリスト（時系列順）を返します。
        ターン単位で除去するため、ツール呼び出しペアやロール交互規約の整合性が常に保たれます。
        """
        if steps <= 0 or not self.turns:
            return []
        actual = min(steps, len(self.turns))
        removed = self.turns[-actual:]
        del self.turns[-actual:]
        return removed

    def apply_compaction(self, summary_text: str, keep_recent_turns: int = 2) -> int:
        """
        古い対話ターン群を要約テキストへ置き換えて履歴を圧縮し、削減されたターン数を返します。

        Args:
            summary_text: 新しく統合・生成された要約テキスト。
            keep_recent_turns: 末尾に残す直近の生ターン数（0以上の整数）。
        """
        keep = max(0, int(keep_recent_turns))
        total = len(self.turns)
        if total <= keep:
            self.summary_text = summary_text.strip() or self.summary_text
            return 0

        summarized_count = total - keep
        if keep == 0:
            self.turns.clear()
        else:
            self.turns = self.turns[-keep:]
        self.summary_text = summary_text.strip()
        return summarized_count

    def to_contents(self) -> list[types.Content]:
        """
        Gemini API リクエスト送信用の types.Content フラットリスト（防衛的コピー）を構築します。
        要約テキストが存在する場合は先頭に要約コンテキストペアを展開します。
        """
        contents: list[types.Content] = []
        if self.summary_text:
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=f"【これまでの対話の要約】\n{self.summary_text}"
                        )
                    ],
                )
            )
            contents.append(
                types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text="これまでの対話の経緯と決定事項の要約を把握しました。引き続いて作業を進めます。"
                        )
                    ],
                )
            )
        for turn in self.turns:
            contents.extend(turn.contents)
        return contents

    @property
    def message_count(self) -> int:
        """展開後の総 Content メッセージ要素数を返します。"""
        base = 2 if self.summary_text else 0
        return base + sum(len(t.contents) for t in self.turns)

    def clear(self) -> int:
        """
        全ターンおよび要約テキストを消去し、消去前の総メッセージ要素数を返します。
        """
        prev_count = self.message_count
        self.turns.clear()
        self.summary_text = None
        self._turn_seq = 0
        return prev_count
