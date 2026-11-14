import sys
from typing import Callable

from prompt_toolkit import PromptSession, prompt as pt_prompt
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from rich.console import Console
from rich.prompt import Confirm, Prompt

from gemio.cli.ui.renderables import (
    build_api_key_guide_panel,
    build_approval_panel,
    build_ask_user_panel,
    build_model_selection_panel,
    build_trust_mode_consent_panel,
    format_prompt_header,
)
from gemio.core.exceptions import AgentExecutionAborted
from gemio.core.models import (
    MODEL_PRESETS,
    resolve_model_name,
)
from gemio.project import Project


# =====================================================================
# 1. プロンプト入力・セッション構築 (prompt_toolkit)
# =====================================================================

def clear_prompt_buffer(buffer: Buffer) -> None:
    """プロンプト入力欄のテキストをUndo可能な状態で全クリアします。"""
    if buffer.text:
        buffer.save_to_undo_stack()
        buffer.document = Document(text="", cursor_position=0)


def create_prompt_session() -> PromptSession:
    """Esc + Enter 送信および Esc + Backspace 入力クリアを備えた PromptSession を作成します。"""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _(event):
        """Esc + Enter で入力を確定して送信する"""
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "backspace")
    def _(event):
        """Esc + Backspace でプロンプト入力欄を全クリアする"""
        clear_prompt_buffer(event.current_buffer)

    return PromptSession(key_bindings=kb)


def prompt_user_input(
    session: PromptSession,
    project: Project,
    token_usage: dict[str, int] | None = None,
) -> str:
    """ステータスヘッダー付きのマルチラインプロンプトを表示し、ユーザー入力文字列を取得します。"""
    header_html = format_prompt_header(project, token_usage)
    return session.prompt(HTML(header_html), multiline=True).strip()


# =====================================================================
# 2. 対話的確認・選択プロンプト (rich.prompt / prompt_toolkit)
# =====================================================================

def request_trust_mode_consent(console: Console | None = None) -> bool:
    """トラストモードを有効化する前のユーザー同意確認を行います。"""
    active_console = console or Console()
    active_console.print()
    active_console.print(build_trust_mode_consent_panel())
    return Confirm.ask(
        "[bold red]このプロジェクトを信頼し、トラストモード（自動承認）を有効にしますか？[/bold red]",
        default=False,
    )


def prompt_api_key_for_container(
    prompt_fn: Callable | None = None,
    console: Console | None = None,
) -> str:
    """
    コンテナ内起動モード（in_container）において、環境変数を介さずにユーザーから安全にAPIキーを受け取ります。
    入力文字はすべて '*' でマスク表示され、再確認なしでそのまま起動します。
    """
    active_console = console or Console()
    active_prompt_fn = prompt_fn or pt_prompt
    active_console.print()
    active_console.print(build_api_key_guide_panel())
    while True:
        try:
            entered_key = active_prompt_fn(
                HTML("<ansicyan><b>GEMINI_API_KEY を入力してください &gt; </b></ansicyan>"),
                is_password=True,
            ).strip()
            if entered_key:
                active_console.print("[bold green]✓ APIキーを受け付けました。[/bold green]\n")
                return entered_key
            active_console.print(
                "[yellow]APIキーが空です。貼り付けてから Enter を押してください（終了するには Ctrl+C）。[/yellow]"
            )
        except (KeyboardInterrupt, EOFError):
            active_console.print("\n[bold yellow]APIキーの入力がキャンセルされたため、終了します。[/bold yellow]")
            sys.exit(1)


def prompt_approval_choice(
    action_description: str,
    console: Console | None = None,
) -> str:
    """
    破壊的操作実行前の承認要求パネルを表示し、ユーザーの選択（y / n / a / k）を取得します。
    """
    active_console = console or Console()
    active_console.print()
    active_console.print(build_approval_panel(action_description))
    return Prompt.ask(
        "[bold green]この操作を許可しますか？[/bold green]",
        choices=["y", "n", "a", "k"],
        default="n",
    )


def prompt_iteration_limit_continue(console: Console | None = None) -> tuple[bool, int]:
    """
    エージェントの連続ツール呼び出し上限到達時に、継続可否と追加ループ回数を問い合わせます。
    """
    active_console = console or Console()
    active_console.print()
    should_continue = Confirm.ask(
        "[bold yellow]連続ツール呼び出しの上限に達しました。処理を継続（追加ループ）しますか？[/bold yellow]",
        default=True,
    )
    if should_continue:
        extra_str = Prompt.ask(
            "[bold cyan]追加で何回ループを実行しますか？[/bold cyan]",
            default="10",
        )
        try:
            extra_iter = int(extra_str) if extra_str.isdigit() and int(extra_str) > 0 else 10
        except Exception:
            extra_iter = 10
        return True, extra_iter
    return False, 0


def prompt_ask_user_response(
    question: str,
    options: list[str] | None = None,
    context: str = "",
    console: Console | None = None,
) -> str:
    """
    AIからの質問・確認要求をパネル表示し、ユーザーの選択または自由記述回答を取得します。
    """
    active_console = console or Console()
    active_console.print()
    active_console.print(build_ask_user_panel(question, options, context))

    while True:
        prompt_label = (
            "[bold green]番号を選択、または直接回答・指示を入力してください[/bold green]"
            if options
            else "[bold green]回答・指示を入力してください[/bold green]"
        )
        ans = Prompt.ask(prompt_label).strip()
        if not ans:
            continue
        if ans.lower() in ["a", "abort"]:
            raise AgentExecutionAborted(
                "ユーザーによって作業の強制中止（Abort）が選択されました。"
            )
        if options:
            if ans == "0":
                while True:
                    custom = Prompt.ask(
                        "[bold cyan]回答・指示を入力してください（[bold red]a[/bold red]で強制中止）[/bold cyan]"
                    ).strip()
                    if not custom:
                        continue
                    if custom.lower() in ["a", "abort"]:
                        raise AgentExecutionAborted(
                            "ユーザーによって作業の強制中止（Abort）が選択されました。"
                        )
                    return custom
            if ans.isdigit():
                choice_idx = int(ans)
                if 1 <= choice_idx <= len(options):
                    return str(options[choice_idx - 1])
        return ans


def prompt_model_selection(
    current_model: str,
    console: Console | None = None,
) -> str | None:
    """
    プリセットモデル一覧を表示し、ユーザーが選択したモデル名（キャンセル時は None）を返します。
    """
    active_console = console or Console()
    active_console.print(build_model_selection_panel(current_model))

    choice = Prompt.ask("[bold green]選択[/bold green]", default="").strip()
    if not choice:
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(MODEL_PRESETS):
        return MODEL_PRESETS[int(choice) - 1][0]
    return resolve_model_name(choice)
