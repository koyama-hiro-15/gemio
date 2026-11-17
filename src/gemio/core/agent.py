"""
通常対話および Function Calling（ローカルツール実行）ループを担当する ChatAgent モジュール。
Web検索機能や検索履歴は SearchAgent へ完全に分離されています。
"""

from dataclasses import dataclass
import inspect
import threading
from typing import Any, Callable

from google.genai import types

from gemio.core.client import GenAIClient
from gemio.core.context.history import ChatTurn
from gemio.core.context.session import ChatSession
from gemio.core.exceptions import AgentExecutionAborted
from gemio.core.models import AgentLogCategory, get_now_time_text
from gemio.core.prompts import SystemPromptComposer
from gemio.core.tools.registry import StaticToolProvider, ToolProvider


def emit_agent_log(
    callback: Callable | None,
    message: str,
    category: str,
) -> None:
    """
    ログ出力コールバックを呼び出します。
    コールバックがキーワード引数 `category` を受け取れる場合は明示的なログ種別を渡し、
    1引数のみのコールバック（list.append や簡易ラムダ等）の場合は文字列のみを渡します。
    """
    if callback is None:
        return
    accepts_category = False
    try:
        sig = inspect.signature(callback)
        params = sig.parameters
        if "category" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        ):
            accepts_category = True
    except (TypeError, ValueError):
        accepts_category = False

    if accepts_category:
        callback(message, category=category)
    else:
        callback(message)


@dataclass
class ChatTurnResult:
    """1回のプロンプト対話処理の完了結果を表すデータクラス。"""

    response_text: str
    turn: ChatTurn
    aborted: bool = False


class ChatAgent:
    """
    GenAIClient、ChatSession、ToolProvider、および SystemPromptComposer を用いて、
    AIとの対話およびローカルツール呼び出しループを実行するクラス。
    各イテレーション先頭でプロンプトと有効ツールを動的評価するため、ループ中のスキル有効化や
    ツール追加が直後のターンから即座に反映されます。
    """

    def __init__(
        self,
        client: GenAIClient,
        tools: list[Callable] | None = None,
        system_instruction: str = "",
        session: ChatSession | None = None,
        *,
        tool_provider: ToolProvider | None = None,
        prompt_composer: SystemPromptComposer | None = None,
        max_iterations: int = 10,
        on_iteration_limit: Callable[[], tuple[bool, int]] | None = None,
        on_prompt_start: Callable[[], None] | None = None,
        on_prompt_end: Callable[[], None] | None = None,
    ) -> None:
        self.client: GenAIClient = client
        self.tool_provider: ToolProvider = tool_provider or StaticToolProvider.from_functions(
            list(tools or [])
        )
        self.prompt_composer: SystemPromptComposer = prompt_composer or SystemPromptComposer(
            base_instruction=system_instruction,
            tool_provider=self.tool_provider,
        )
        if self.prompt_composer.tool_provider is None:
            self.prompt_composer.tool_provider = self.tool_provider

        self.session: ChatSession = session or ChatSession()
        self.max_iterations: int = max_iterations
        self.on_iteration_limit = on_iteration_limit
        self.on_prompt_start = on_prompt_start
        self.on_prompt_end = on_prompt_end

    @property
    def tools(self) -> list[Callable]:
        """現在有効化されているツール関数のリストを返します。"""
        return self.tool_provider.get_active_tools()

    @property
    def instruction(self) -> str:
        """現在の状態で合成されたシステムインストラクション文字列を返します。"""
        return self.prompt_composer.compose()

    def _build_generation_config(self) -> dict[str, Any]:
        """現在のイテレーション時点におけるシステムインストラクションと有効ツールから生成設定を構築します。"""
        return {
            "response_mime_type": "text/plain",
            "tools": self.tool_provider.get_active_tools(),
            "system_instruction": self.prompt_composer.compose(),
            "temperature": 0.0,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        }

    def get_token_usage(self) -> dict[str, int]:
        """現在のコンテキスト量・累積トークン量・履歴メッセージ数・前提資料件数の辞書を返します。"""
        return self.client.token_tracker.get_snapshot(
            history_messages=self.session.history.message_count,
            materials_count=len(self.session.materials),
        )

    @staticmethod
    def _format_arg_value_for_log(key: str, value: Any, max_str_len: int = 80) -> str:
        """ツール呼び出しログ表示用に単一の引数値を整形し、巨大な文字列やコレクションを省略します。"""
        large_text_keys = {"content", "find_text", "replace_text", "patch_content", "data"}
        if isinstance(value, str):
            char_len = len(value)
            line_count = len(value.splitlines()) if value else 0
            single_line = value.replace("\r", "").replace("\n", "\\n")
            if key in large_text_keys or char_len > max_str_len or line_count > 2:
                snippet = single_line[:max_str_len]
                ellipsis_mark = "..." if len(single_line) > max_str_len else ""
                return f"[{char_len}文字/{line_count}行] '{snippet}{ellipsis_mark}'"
            return f"'{single_line}'"
        if isinstance(value, list):
            if len(value) > 5:
                preview_items = ", ".join(repr(x) for x in value[:5])
                return f"[{len(value)}件: {preview_items}, ...]"
            return repr(value)
        if isinstance(value, dict):
            return f"{{...({len(value)} keys)}}"
        return repr(value)

    @classmethod
    def _format_tool_call_log(cls, func_name: str, func_args: dict[str, Any] | None) -> str:
        """ツール呼び出しの名称と主要引数を、巨大データを省略した分かりやすい形式でフォーマットします。"""
        if not func_args:
            return f"Tool call: {func_name}"
        formatted_pairs = [
            f"{k}={cls._format_arg_value_for_log(k, v)}"
            for k, v in func_args.items()
        ]
        args_str = ", ".join(formatted_pairs)
        return f"Tool call: {func_name} ({args_str})"

    @staticmethod
    def _extract_thoughts(response: Any) -> list[str]:
        """レスポンス内に思考テキスト（thought=True のテキストパート）が含まれていれば抽出します。"""
        thoughts: list[str] = []
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return thoughts
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", False) is True:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text.strip():
                    thoughts.append(text.strip())
        return thoughts

    @staticmethod
    def _emit_turn_cancelled_log(on_log_append: Callable | None) -> str:
        """ユーザーによるターン中断メッセージを構築し、ABORT カテゴリでログ出力します。"""
        abort_text = (
            "🚨 【作業の強制中止】\n"
            "ユーザーによって現在のターンが中断されました。\n"
            "現在のエージェント作業ループを停止しました。次の指示を入力してください。"
        )
        emit_agent_log(
            on_log_append,
            f"{get_now_time_text()}\n{abort_text}",
            AgentLogCategory.ABORT,
        )
        return abort_text

    def _execute_agent_loop(
        self,
        max_iter: int,
        turn_contents: list[types.Content],
        turn_thoughts: list[str],
        on_log_append: Callable | None,
        tool_executor: Callable | None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, str, int, bool]:
        """
        AIモデルとの生成およびツール呼び出しループを実行します。

        Returns:
            (完了したか, 最終または中断応答テキスト, 実行ツール呼び出し数, 中断終了フラグ)
        """
        executed_calls = 0

        def on_retry_warn(warn_msg: str) -> None:
            emit_agent_log(on_log_append, warn_msg, AgentLogCategory.WARNING)

        for _ in range(max_iter):
            if cancel_event is not None and cancel_event.is_set():
                abort_text = self._emit_turn_cancelled_log(on_log_append)
                return True, abort_text, executed_calls, True

            request_contents = self.session.build_request_contents(
                pending_contents=turn_contents
            )
            config = self._build_generation_config()

            try:
                response = self.client.generate_content(
                    contents=request_contents,
                    config=config,
                    on_retry_warning=on_retry_warn,
                    usage_source="chat",
                )
            except Exception:
                if cancel_event is not None and cancel_event.is_set():
                    abort_text = self._emit_turn_cancelled_log(on_log_append)
                    return True, abort_text, executed_calls, True

                error_text = (
                    "申し訳ありません。サーバー混雑または通信エラーが発生したため、処理を一時中断しました。\n"
                    "直前までに実行されたツール結果（ファイルの作成・修正など）は保持されています。\n"
                    "作業を再開する場合は「続けてください」と入力するか、新しい指示を入力してください。"
                )
                emit_agent_log(
                    on_log_append,
                    f"{get_now_time_text()}\n{error_text}",
                    AgentLogCategory.WARNING,
                )
                return True, error_text, executed_calls, True

            if cancel_event is not None and cancel_event.is_set():
                abort_text = self._emit_turn_cancelled_log(on_log_append)
                return True, abort_text, executed_calls, True

            turn_thoughts.extend(self._extract_thoughts(response))

            if response.function_calls:
                turn_contents.append(response.candidates[0].content)
                calls = list(response.function_calls)

                for idx, function_call in enumerate(calls):
                    func_name = function_call.name
                    func_args = function_call.args or {}

                    try:
                        if cancel_event is not None and cancel_event.is_set():
                            raise AgentExecutionAborted(
                                "ユーザーによって現在のターンが中断されました。"
                            )

                        executed_calls += 1
                        tool_log_msg = self._format_tool_call_log(func_name, func_args)
                        emit_agent_log(
                            on_log_append,
                            f"{get_now_time_text()}\n{tool_log_msg}",
                            AgentLogCategory.TOOL,
                        )

                        tool_result = self.tool_provider.execute(
                            func_name,
                            func_args,
                            executor=tool_executor,
                        )
                    except AgentExecutionAborted as abort_err:
                        abort_reason = str(abort_err)
                        turn_contents.append(
                            types.Content(
                                role="user",
                                parts=[
                                    types.Part.from_function_response(
                                        name=func_name,
                                        response={"result": f"[ABORTED] {abort_reason}"},
                                    )
                                ],
                            )
                        )
                        for remaining_call in calls[idx + 1 :]:
                            turn_contents.append(
                                types.Content(
                                    role="user",
                                    parts=[
                                        types.Part.from_function_response(
                                            name=remaining_call.name,
                                            response={
                                                "result": "[ABORTED] 作業が強制中止されたためスキップされました。"
                                            },
                                        )
                                    ],
                                )
                            )
                        abort_text = (
                            f"🚨 【作業の強制中止】\n{abort_reason}\n"
                            "現在のエージェント作業ループを停止しました。次の指示を入力してください。"
                        )
                        emit_agent_log(
                            on_log_append,
                            f"{get_now_time_text()}\n{abort_text}",
                            AgentLogCategory.ABORT,
                        )
                        return True, abort_text, executed_calls, True

                    turn_contents.append(
                        types.Content(
                            role="user",
                            parts=[
                                types.Part.from_function_response(
                                    name=func_name,
                                    response={"result": tool_result},
                                )
                            ],
                        )
                    )

                    if cancel_event is not None and cancel_event.is_set():
                        for remaining_call in calls[idx + 1 :]:
                            turn_contents.append(
                                types.Content(
                                    role="user",
                                    parts=[
                                        types.Part.from_function_response(
                                            name=remaining_call.name,
                                            response={
                                                "result": "[ABORTED] 作業が強制中止されたためスキップされました。"
                                            },
                                        )
                                    ],
                                )
                            )
                        abort_text = self._emit_turn_cancelled_log(on_log_append)
                        return True, abort_text, executed_calls, True

                continue

            response_text = response.text or ""
            emit_agent_log(
                on_log_append,
                f"{get_now_time_text()}\n{response_text}",
                AgentLogCategory.ASSISTANT,
            )
            return True, response_text, executed_calls, False

        return False, "", executed_calls, False

    def process_prompt(
        self,
        prompt: str,
        on_log_append: Callable | None = None,
        tool_executor: Callable | None = None,
        on_iteration_limit: Callable[[], tuple[bool, int]] | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ChatTurnResult:
        """
        ユーザーからのプロンプトを同期的に処理し、1つの ChatTurn として対話履歴に確定記録します。
        """
        if self.on_prompt_start is not None:
            self.on_prompt_start()

        created_at = get_now_time_text()
        emit_agent_log(
            on_log_append,
            f"{created_at}\n{prompt}",
            AgentLogCategory.USER,
        )

        user_content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        )
        turn_contents: list[types.Content] = [user_content]
        turn_thoughts: list[str] = []
        total_tool_calls = 0
        aborted = False

        try:
            completed, response_text, calls_cnt, aborted = self._execute_agent_loop(
                self.max_iterations,
                turn_contents,
                turn_thoughts,
                on_log_append,
                tool_executor,
                cancel_event=cancel_event,
            )
            total_tool_calls += calls_cnt

            if not completed:
                limit_handler = on_iteration_limit or self.on_iteration_limit

                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        should_continue, extra_iterations = False, 0
                    elif limit_handler:
                        should_continue, extra_iterations = limit_handler()
                    else:
                        choice = (
                            input(
                                "処理を継続（追加ループ）しますか、それとも中断しますか？ [c: 継続 / a: 中断]: "
                            )
                            .strip()
                            .lower()
                        )
                        if choice in ["c", "continue", "y", "yes"]:
                            try:
                                add_more = input(
                                    "追加で何回ループを実行しますか？ (デフォルト: 10): "
                                ).strip()
                                extra_iterations = int(add_more) if add_more.isdigit() else 10
                            except Exception:
                                extra_iterations = 10
                            should_continue = True
                        elif choice in ["a", "abort", "n", "no", ""]:
                            should_continue = False
                            extra_iterations = 0
                        else:
                            print("'c' (継続) または 'a' (中断) を入力してください。")
                            continue

                    if should_continue:
                        emit_agent_log(
                            on_log_append,
                            f"-> ユーザーの指示により、追加で {extra_iterations} 回のループを実行します。\n",
                            AgentLogCategory.WARNING,
                        )
                        completed_extra, response_text, extra_calls, aborted = (
                            self._execute_agent_loop(
                                extra_iterations,
                                turn_contents,
                                turn_thoughts,
                                on_log_append,
                                tool_executor,
                                cancel_event=cancel_event,
                            )
                        )
                        total_tool_calls += extra_calls
                        if completed_extra:
                            break
                    else:
                        emit_agent_log(
                            on_log_append,
                            "-> ユーザーによって処理が中断されました。\n",
                            AgentLogCategory.WARNING,
                        )
                        # 未完のツールループ経過をロールバックし、ユーザー発話と中断応答のみでクリーンに閉じる
                        turn_contents = [user_content]
                        response_text = (
                            "申し訳ありません。複雑すぎる処理または無限ループを検知したため、"
                            "今回の処理を中断しました。"
                        )
                        aborted = True
                        break

            turn_contents.append(
                types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=response_text)],
                )
            )
            turn = ChatTurn(
                turn_id=self.session.history.next_turn_id(),
                user_prompt=prompt,
                contents=turn_contents,
                assistant_response=response_text,
                thoughts=turn_thoughts,
                tool_call_count=total_tool_calls,
                aborted=aborted,
                created_at=created_at,
            )
            self.session.history.append_turn(turn)
            return ChatTurnResult(
                response_text=response_text,
                turn=turn,
                aborted=aborted,
            )
        finally:
            if self.on_prompt_end is not None:
                self.on_prompt_end()

    def process_prompt_async(
        self,
        prompt: str,
        on_log_append: Callable | None = None,
        on_finished: Callable[[], None] | None = None,
        tool_executor: Callable | None = None,
        on_iteration_limit: Callable[[], tuple[bool, int]] | None = None,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        """非同期（別スレッド）でプロンプトを処理します。"""

        def background_task() -> None:
            try:
                self.process_prompt(
                    prompt,
                    on_log_append=on_log_append,
                    tool_executor=tool_executor,
                    on_iteration_limit=on_iteration_limit,
                    cancel_event=cancel_event,
                )
            finally:
                if on_finished:
                    on_finished()

        thread = threading.Thread(target=background_task, daemon=True)
        thread.start()
