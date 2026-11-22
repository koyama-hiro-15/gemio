"""
通常対話1ターンを表現する TurnWidget および構成サブウィジェット群。

【重要な描画仕様】
- UserMessageCard (ユーザー入力プロンプト):
  Markdown レンダリングや Rich マークアップ解釈を一切行わず (markup=False)、
  入力されたテキスト・記号・インデントを1文字の狂いもなくそのまま表示します。
- ActivityGroup (ツール呼び出し・警告・思考ログ):
  Collapsible によりデフォルト折りたたみ表示とし、対話の文脈を邪魔しません。
- AssistantMessageCard (AIからの回答):
  Textual の Markdown ウィジェットを用いてリッチにレンダリング表示します。
"""

import re
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Collapsible, Markdown, Static

from gemio.core.models import AgentLogCategory, get_now_time_text
from gemio.tui.messages import CopyTextRequested

_LEADING_TIMESTAMP_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\r?\n(.*)$", re.DOTALL)
_TOOL_CALL_RE = re.compile(r"^Tool call:\s*([a-zA-Z0-9_]+)(?:\s*\((.*)\))?$", re.DOTALL)


def split_timestamp_and_body(log_message: str) -> tuple[str, str]:
    """先頭に 'HH:MM:SS\\n' が付与されたログ文字列を `(timestamp, body)` に分離します。"""
    stripped = log_message.strip("\r\n")
    match = _LEADING_TIMESTAMP_RE.match(stripped)
    if match:
        return match.group(1), match.group(2)
    return "", stripped


def extract_tool_name_from_log(body: str) -> str:
    """'Tool call: func_name (...)' 形式の本文からツール関数名を抽出します。"""
    match = _TOOL_CALL_RE.match(body.strip())
    if match:
        return match.group(1)
    return "tool"


class UserMessageCard(Widget):
    """
    ユーザーの入力プロンプトをプレーンテキストのまま正確に表示するカードウィジェット。
    """

    def __init__(
        self,
        prompt_text: str,
        timestamp: str = "",
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.prompt_text: str = prompt_text
        self.timestamp: str = timestamp or get_now_time_text()

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-header-row"):
            yield Static("You", markup=False, classes="card-role-user")
            if self.timestamp:
                yield Static(
                    self.timestamp,
                    markup=False,
                    classes="card-timestamp",
                )
            yield Button("Copy", classes="copy-btn", compact=True)
        # Markdown解釈・Richマークアップ解釈を完全に無効化し、生テキストをそのまま描画する
        yield Static(
            self.prompt_text,
            markup=False,
            classes="user-prompt-body",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(
            CopyTextRequested(self.prompt_text, label="入力プロンプト")
        )


class ActivityGroup(Widget):
    """
    ターン途中で発生したツール呼び出し・リトライ警告・思考テキストを格納する折りたたみウィジェット。
    イベントが0件の間は非表示（display=False）を維持します。
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.tool_names: list[str] = []
        self.warning_count: int = 0
        self.thought_count: int = 0
        self._pending_lines: list[Text] = []
        self._thoughts_added: bool = False
        self._dom_ready: bool = False
        self.display = False

    def compose(self) -> ComposeResult:
        with Collapsible(
            title=self._build_summary_title(),
            collapsed=True,
            collapsed_symbol="▸",
            expanded_symbol="▾",
        ):
            yield Vertical(classes="activity-items")

    def on_mount(self) -> None:
        self._dom_ready = True
        if self._pending_lines:
            container = self.query_one(".activity-items", Vertical)
            for line in self._pending_lines:
                container.mount(Static(line, classes="activity-log-item"))
            self._pending_lines.clear()
        if self.display:
            self._refresh_title()

    def _append_activity_line(self, line: Text) -> None:
        if self._dom_ready:
            container = self.query_one(".activity-items", Vertical)
            container.mount(Static(line, classes="activity-log-item"))
            self._refresh_title()
        else:
            self._pending_lines.append(line)

    def _build_summary_title(self) -> str:
        parts: list[str] = []
        t_cnt = len(self.tool_names)
        if t_cnt > 0:
            if t_cnt <= 3:
                names_preview = ", ".join(self.tool_names)
            else:
                names_preview = f"..., {', '.join(self.tool_names[-2:])}"
            parts.append(f"Tools ({t_cnt} calls: {names_preview})")
        if self.thought_count > 0:
            parts.append(f"Thinking ({self.thought_count})")
        if self.warning_count > 0:
            parts.append(f"Warnings ({self.warning_count})")
        return " | ".join(parts) if parts else "Tools (0 calls)"

    def _refresh_title(self) -> None:
        if not self._dom_ready:
            return
        collapsible = self.query_one(Collapsible)
        collapsible.title = self._build_summary_title()

    def add_tool_call(self, timestamp: str, body: str) -> str:
        """ツール呼び出しログを追加し、抽出されたツール関数名を返します。"""
        self.display = True
        match = _TOOL_CALL_RE.match(body.strip())
        if match:
            tool_name = match.group(1)
            raw_args = match.group(2) or ""
        else:
            tool_name = "tool"
            raw_args = body.strip()

        self.tool_names.append(tool_name)

        line = Text()
        if timestamp:
            line.append(f"[{timestamp}] ", style="dim")
        line.append("Tool: ", style="bold cyan")
        line.append(tool_name, style="bold green")
        if raw_args:
            line.append(f" ({raw_args})", style="default")

        self._append_activity_line(line)
        return tool_name

    def add_warning(self, timestamp: str, body: str) -> None:
        """リトライ待機や上限継続などの警告ログを追加します。"""
        self.display = True
        self.warning_count += 1

        line = Text()
        if timestamp:
            line.append(f"[{timestamp}] ", style="dim")
        line.append(body.strip(), style="yellow")

        self._append_activity_line(line)

    def add_search_info(self, timestamp: str, body: str) -> None:
        """AIによる web_search ツール実行時の参照元ドメイン情報等を折りたたみログへ追加します。"""
        cleaned = body.strip()
        if not cleaned:
            return
        self.display = True

        line = Text()
        if timestamp:
            line.append(f"[{timestamp}] ", style="dim")
        line.append("[Web Search] ", style="bold cyan")
        line.append(cleaned, style="cyan")

        self._append_activity_line(line)

    def add_thought(self, timestamp: str, body: str) -> None:
        """逐次取得された思考テキスト（Thinking）を1件ずつリアルタイムで展開領域へ追加します。"""
        cleaned = body.strip()
        if not cleaned:
            return
        self.display = True
        self.thought_count += 1

        line = Text()
        if timestamp:
            line.append(f"[{timestamp}] ", style="dim")
        line.append(f"[Thinking #{self.thought_count}] ", style="bold magenta")
        line.append(cleaned, style="dim")
        self._append_activity_line(line)

    def set_thoughts(self, thoughts: list[str]) -> None:
        """ターン完了時に未表示分の思考テキスト（Thinking）を展開領域へ補完追加します。"""
        if self._thoughts_added:
            return
        valid = [t.strip() for t in thoughts if t and t.strip()]
        if not valid:
            return
        self._thoughts_added = True
        remaining = valid[self.thought_count :]
        if not remaining:
            return
        self.display = True
        for thought_text in remaining:
            self.thought_count += 1
            line = Text()
            line.append(f"[Thinking #{self.thought_count}] ", style="bold magenta")
            line.append(thought_text, style="dim")
            self._append_activity_line(line)


class AssistantMessageCard(Widget):
    """
    AIの最終回答（または中断メッセージ）を Markdown レンダリングで表示するカードウィジェット。
    回答が到着するまでは非表示（display=False）とします。
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.response_text: str = ""
        self.timestamp: str = ""
        self.aborted: bool = False
        self._has_response: bool = False
        self._dom_ready: bool = False
        self.display = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="card-header-row"):
            yield Static(
                "Gemio",
                markup=False,
                classes="assistant-role-label card-role-assistant",
            )
            yield Static(
                "",
                markup=False,
                classes="assistant-timestamp card-timestamp",
            )
            yield Button("Copy", classes="copy-btn", compact=True)
        yield Markdown("", classes="assistant-markdown")

    def on_mount(self) -> None:
        self._dom_ready = True
        if self._has_response:
            self._apply_response_to_dom()

    def _apply_response_to_dom(self) -> None:
        role_w = self.query_one(".assistant-role-label", Static)
        role_w.update("Gemio [中断]" if self.aborted else "Gemio")
        role_w.set_class(not self.aborted, "card-role-assistant")
        role_w.set_class(self.aborted, "card-role-aborted")

        ts_w = self.query_one(".assistant-timestamp", Static)
        ts_w.update(self.timestamp)

        md_w = self.query_one(".assistant-markdown", Markdown)
        md_w.update(self.response_text)

    def set_response(
        self,
        response_text: str,
        timestamp: str = "",
        *,
        aborted: bool = False,
    ) -> None:
        """AIの回答テキストを Markdown ウィジェットへ反映してカードを表示します。"""
        self.response_text = response_text
        self.timestamp = timestamp or get_now_time_text()
        self.aborted = aborted
        self._has_response = True
        self.set_class(aborted, "aborted")
        self.display = True

        if self._dom_ready:
            self._apply_response_to_dom()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if self.response_text:
            self.post_message(
                CopyTextRequested(self.response_text, label="AI回答")
            )


class TurnWidget(Widget):
    """
    1回のユーザープロンプト送信から（ツール実行を経て）AIの最終回答までを束ねる複合ターンウィジェット。
    """

    def __init__(
        self,
        prompt_text: str,
        *,
        turn_id: str | None = None,
        timestamp: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.prompt_text: str = prompt_text
        self.turn_id: str | None = turn_id
        self.created_at: str = timestamp or get_now_time_text()
        self._user_card = UserMessageCard(self.prompt_text, timestamp=self.created_at)
        self._activity_group = ActivityGroup()
        self._assistant_card = AssistantMessageCard()

    def compose(self) -> ComposeResult:
        yield self._user_card
        yield self._activity_group
        yield self._assistant_card

    @property
    def user_card(self) -> UserMessageCard:
        return self._user_card

    @property
    def activity_group(self) -> ActivityGroup:
        return self._activity_group

    @property
    def assistant_card(self) -> AssistantMessageCard:
        return self._assistant_card

    def append_agent_log(self, log_message: str, category: str) -> str | None:
        """
        ChatAgent からの逐次ログを受け取り、該当サブウィジェットへ振り分けます。
        ツール呼び出しの場合は抽出されたツール名を返します。
        """
        ts, body = split_timestamp_and_body(log_message)
        if category == AgentLogCategory.USER:
            return None
        if category == AgentLogCategory.TOOL:
            return self.activity_group.add_tool_call(ts, body)
        if category == AgentLogCategory.THOUGHT:
            self.activity_group.add_thought(ts, body)
            return None
        if category == AgentLogCategory.WARNING:
            self.activity_group.add_warning(ts, body)
            return None
        if category == AgentLogCategory.SEARCH:
            self.activity_group.add_search_info(ts, body)
            return None
        if category == AgentLogCategory.ABORT:
            self.assistant_card.set_response(body, timestamp=ts, aborted=True)
            return None
        if category == AgentLogCategory.ASSISTANT:
            self.assistant_card.set_response(body, timestamp=ts, aborted=False)
            return None
        return None

    def finalize_turn(
        self,
        *,
        turn_id: str,
        response_text: str,
        thoughts: list[str] | None = None,
        aborted: bool = False,
    ) -> None:
        """ChatTurnResult 確定時に turn_id・思考ログ・最終応答（未表示時の補完含む）を確定反映します。"""
        self.turn_id = turn_id
        if thoughts:
            self.activity_group.set_thoughts(thoughts)
        if not self.assistant_card.display and response_text:
            self.assistant_card.set_response(
                response_text,
                timestamp=get_now_time_text(),
                aborted=aborted,
            )


__all__ = [
    "ActivityGroup",
    "AssistantMessageCard",
    "TurnWidget",
    "UserMessageCard",
    "extract_tool_name_from_log",
    "split_timestamp_and_body",
]
