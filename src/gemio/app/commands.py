"""
ユーザー入力およびスラッシュコマンドの構造化パーサモジュール。

UI フレームワークに依存せず、入力文字列を型付きのコマンドデータクラスまたは
通常プロンプト（ChatPromptInput）へ変換します。
"""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class ChatPromptInput:
    """通常の対話プロンプト入力を表すデータクラス。"""

    text: str


@dataclass(frozen=True)
class ExitCommand:
    """アプリケーション終了コマンド (/exit, exit, quit)。"""


@dataclass(frozen=True)
class HelpCommand:
    """ヘルプ表示コマンド (/help, /h, help, /info)。"""


@dataclass(frozen=True)
class TokensCommand:
    """トークン使用量・コンテキスト診断表示コマンド (/tokens, /usage, /token)。"""


@dataclass(frozen=True)
class SandboxCommand:
    """サンドボックス環境・マウント保護診断コマンド (/sandbox, /status, /container)。"""


@dataclass(frozen=True)
class ClearCommand:
    """対話セッションリセットコマンド (/clear, /reset, /new)。"""

    clear_materials: bool = False


@dataclass(frozen=True)
class EditModeCommand:
    """エディットモード切替コマンド (/edit [on|off])。enable=None はトグルを表します。"""

    enable: bool | None = None


@dataclass(frozen=True)
class TrustModeCommand:
    """トラストモード切替コマンド (/trust, /auto [on|off])。enable=None はトグルを表します。"""

    enable: bool | None = None


@dataclass(frozen=True)
class ModelCommand:
    """AIモデル切替コマンド (/model [name])。target_model=None は対話選択を表します。"""

    target_model: str | None = None


@dataclass(frozen=True)
class SearchCommand:
    """Web検索・作業前提資料操作コマンド (/search, /s)。"""

    query: str = ""
    new_session: bool = False
    clear_materials: bool = False
    show_help: bool = False


@dataclass(frozen=True)
class CopyCommand:
    """直近のAI回答をクリップボードへコピーするコマンド (/copy, /cp)。"""


@dataclass(frozen=True)
class CompactCommand:
    """古い対話ターン群を構造化要約へ圧縮するコマンド (/compact [keep_recent_turns])。"""

    keep_recent_turns: int = 2


@dataclass(frozen=True)
class RewindCommand:
    """直近の対話ターンを指定数だけ巻き戻すコマンド (/rewind, /undo [steps])。"""

    steps: int = 1


ParsedInput = Union[
    ChatPromptInput,
    ExitCommand,
    HelpCommand,
    TokensCommand,
    SandboxCommand,
    ClearCommand,
    EditModeCommand,
    TrustModeCommand,
    ModelCommand,
    SearchCommand,
    CopyCommand,
    CompactCommand,
    RewindCommand,
]


def _parse_optional_bool_flag(arg: str) -> bool | None:
    norm = arg.strip().lower()
    if norm in {"on", "true", "enable"}:
        return True
    if norm in {"off", "false", "disable"}:
        return False
    return None


def parse_slash_command(raw_input: str) -> ParsedInput | None:
    """
    ユーザー入力文字列を解析し、対応するコマンドオブジェクトまたは ChatPromptInput を返します。
    空白のみの入力の場合は None を返します。
    """
    stripped = raw_input.strip()
    if not stripped:
        return None

    lower = stripped.lower()

    if lower in {"/exit", "exit", "quit"}:
        return ExitCommand()

    if lower in {"/help", "/h", "help", "/info"}:
        return HelpCommand()

    if lower in {"/tokens", "/usage", "/token"}:
        return TokensCommand()

    if lower in {"/sandbox", "/status", "/container"}:
        return SandboxCommand()

    if lower in {"/clear", "/reset", "/new"} or lower.startswith(
        ("/clear ", "/reset ", "/new ")
    ):
        parts = lower.split()
        clear_mats = any(arg in {"-a", "--all"} for arg in parts[1:])
        return ClearCommand(clear_materials=clear_mats)

    if lower == "/edit" or lower.startswith("/edit "):
        parts = lower.split()
        enable = _parse_optional_bool_flag(parts[1]) if len(parts) > 1 else None
        return EditModeCommand(enable=enable)

    if lower in {"/trust", "/auto"} or lower.startswith(("/trust ", "/auto ")):
        parts = lower.split()
        enable = _parse_optional_bool_flag(parts[1]) if len(parts) > 1 else None
        return TrustModeCommand(enable=enable)

    if lower == "/model" or lower.startswith("/model "):
        parts = stripped.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            return ModelCommand(target_model=parts[1].strip())
        return ModelCommand(target_model=None)

    if lower in {"/search", "/s"} or lower.startswith(("/search ", "/s ")):
        if lower in {"/search --clear", "/search -c", "/s --clear", "/s -c"}:
            return SearchCommand(clear_materials=True)
        if lower in {
            "/search",
            "/s",
            "/search -n",
            "/search --new",
            "/s -n",
            "/s --new",
        }:
            return SearchCommand(show_help=True)

        raw_query = (
            stripped[8:].strip()
            if lower.startswith("/search ")
            else stripped[3:].strip()
        )
        raw_query_lower = raw_query.lower()

        if raw_query_lower in {"-c", "--clear"}:
            return SearchCommand(clear_materials=True)
        if not raw_query or raw_query_lower in {"-n", "--new"}:
            return SearchCommand(show_help=True)

        if raw_query_lower.startswith(("-n ", "--new ")):
            split_q = raw_query.split(maxsplit=1)
            actual_q = split_q[1].strip() if len(split_q) > 1 else ""
            if not actual_q:
                return SearchCommand(show_help=True)
            return SearchCommand(query=actual_q, new_session=True)

        return SearchCommand(query=raw_query, new_session=False)

    if lower in {"/copy", "/cp"}:
        return CopyCommand()

    if lower == "/compact" or lower.startswith("/compact "):
        parts = lower.split()
        keep_n = 2
        if len(parts) > 1 and parts[1].isdigit():
            keep_n = int(parts[1])
        return CompactCommand(keep_recent_turns=keep_n)

    if lower in {"/rewind", "/undo"} or lower.startswith(("/rewind ", "/undo ")):
        parts = lower.split()
        steps = 1
        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 0:
            steps = int(parts[1])
        return RewindCommand(steps=steps)

    return ChatPromptInput(text=stripped)


__all__ = [
    "ChatPromptInput",
    "ClearCommand",
    "CompactCommand",
    "CopyCommand",
    "EditModeCommand",
    "ExitCommand",
    "HelpCommand",
    "ModelCommand",
    "ParsedInput",
    "RewindCommand",
    "SandboxCommand",
    "SearchCommand",
    "TokensCommand",
    "TrustModeCommand",
    "parse_slash_command",
]
