"""
ワーカースレッド（ChatAgent / Project）と Textual メインスレッド（ModalScreen）を繋ぐ同期確認ブリッジ。

メインイベントループを一切ブロックすることなく、ワーカースレッドからの承認要求・質問・上限確認で
ModalScreen を表示し、ユーザーの応答が確定するまでワーカースレッド側のみを安全に待機させます。
"""

import threading
from typing import Any, Callable, TypeVar

from textual.app import App
from textual.screen import ModalScreen

from gemio.core.exceptions import AgentExecutionAborted

T = TypeVar("T")

MODAL_ABORT_SENTINEL = "__GEMIO_MODAL_ABORT__"


class ThreadModalBridge:
    """
    InteractionHandler プロトコルの Textual 実装クラス。
    """

    def __init__(
        self,
        app: App[Any],
        *,
        approval_screen_factory: Callable[[str], ModalScreen[str]] | None = None,
        ask_user_screen_factory: Callable[
            [str, list[str] | None, str], ModalScreen[str]
        ]
        | None = None,
        iteration_limit_screen_factory: Callable[
            [], ModalScreen[tuple[bool, int]]
        ]
        | None = None,
        on_kill_requested: Callable[[], None] | None = None,
    ) -> None:
        self.app = app
        self.approval_screen_factory = approval_screen_factory
        self.ask_user_screen_factory = ask_user_screen_factory
        self.iteration_limit_screen_factory = iteration_limit_screen_factory
        self.on_kill_requested = on_kill_requested

        self._lock = threading.Lock()
        self._pending_entries: list[dict[str, Any]] = []

    def show_modal_and_wait(
        self,
        screen_factory: Callable[[], ModalScreen[T]],
        default_on_cancel: T,
    ) -> T:
        """
        メインスレッド上に ModalScreen をプッシュし、dismiss されるまで現在のワーカースレッドを待機させます。
        """
        app_thread_id = getattr(self.app, "_thread_id", None)
        if app_thread_id is not None and threading.get_ident() == app_thread_id:
            raise RuntimeError(
                "show_modal_and_wait はメインUIスレッド上からは呼び出せません。ワーカースレッドから呼び出してください。"
            )

        done = threading.Event()
        entry: dict[str, Any] = {
            "event": done,
            "result": default_on_cancel,
            "default": default_on_cancel,
            "cancelled": False,
            "screen": None,
        }

        with self._lock:
            self._pending_entries.append(entry)

        def _on_dismiss(result: T | None) -> None:
            with self._lock:
                if not entry["cancelled"]:
                    entry["result"] = (
                        default_on_cancel if result is None else result
                    )
            done.set()

        def _push_on_main_thread() -> None:
            with self._lock:
                if entry["cancelled"]:
                    done.set()
                    return
                screen = screen_factory()
                entry["screen"] = screen
            self.app.push_screen(screen, callback=_on_dismiss)

        try:
            self.app.call_from_thread(_push_on_main_thread)
            done.wait()
            return entry["result"]
        except Exception:
            return default_on_cancel
        finally:
            with self._lock:
                if entry in self._pending_entries:
                    self._pending_entries.remove(entry)

    def cancel_all_pending(self) -> None:
        """
        待機中のすべてのモーダル確認をキャンセル解除し、ワーカースレッドのブロックを即座に開放します。
        （ターン強制中断 Esc やアプリケーション終了時にメインスレッドから呼び出します）
        """
        with self._lock:
            entries = list(self._pending_entries)
            for entry in entries:
                entry["cancelled"] = True
                entry["result"] = entry["default"]

        for entry in entries:
            screen = entry.get("screen")
            if screen is not None and getattr(screen, "is_Attached", False) or (
                screen is not None and screen in getattr(self.app, "screen_stack", [])
            ):
                try:
                    screen.dismiss(entry["default"])
                except Exception:
                    pass
            entry["event"].set()

    def request_approval(self, action_description: str) -> str:
        """
        破壊的操作実行前の承認モーダルを表示し、'y' / 'n' / 'a' / 'k' を返します。
        'k' (Kill) 選択時は安全にアプリケーション終了処理をトリガーしてループを中断します。
        """
        if self.approval_screen_factory is None:
            return "n"

        factory = self.approval_screen_factory
        choice = (
            self.show_modal_and_wait(
                lambda: factory(action_description),
                default_on_cancel="a",
            )
            .strip()
            .lower()
        )
        if choice == "k":
            if self.on_kill_requested is not None:
                try:
                    self.app.call_from_thread(self.on_kill_requested)
                except Exception:
                    pass
            raise AgentExecutionAborted(
                "ユーザーによってプロセス即時終了（Kill）が選択されました。"
            )
        return choice if choice in {"y", "n", "a"} else "n"

    def ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """
        AIからの質問モーダルを表示し、ユーザーの回答文字列を返します。
        Abort またはキャンセル時は AgentExecutionAborted を送出します。
        """
        if self.ask_user_screen_factory is None:
            raise AgentExecutionAborted(
                "質問確認画面が利用できないため作業を中断しました。"
            )

        factory = self.ask_user_screen_factory
        response = self.show_modal_and_wait(
            lambda: factory(question, options, context),
            default_on_cancel=MODAL_ABORT_SENTINEL,
        )
        if not response or response == MODAL_ABORT_SENTINEL:
            raise AgentExecutionAborted(
                "ユーザーによって作業の強制中止（Abort）が選択されました。"
            )
        return response

    def confirm_iteration_limit(self) -> tuple[bool, int]:
        """連続ツール呼び出し上限到達時の確認モーダルを表示します。"""
        if self.iteration_limit_screen_factory is None:
            return False, 0

        factory = self.iteration_limit_screen_factory
        return self.show_modal_and_wait(
            factory,
            default_on_cancel=(False, 0),
        )


__all__ = [
    "MODAL_ABORT_SENTINEL",
    "ThreadModalBridge",
]
