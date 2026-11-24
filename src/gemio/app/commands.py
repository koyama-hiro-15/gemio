"""
ユーザー入力およびスラッシュコマンドの構造化パーサモジュール。

UI フレームワークに依存せず、入力文字列を型付きのコマンドデータクラスまたは
通常プロンプト（ChatPromptInput）へ変換します。
"""

from dataclasses import dataclass
from typing import Literal, Union


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
class MaterialsCommand:
    """作業前提資料管理コマンド (/materials [clear | rm <id>])。"""

    action: Literal["open", "clear", "remove"] = "open"
    target: str = ""


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
    MaterialsCommand,
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

    if lower in {"/materials", "/mat", "/mats"} or lower.startswith(
        ("/materials ", "/mat ", "/mats ")
    ):
        parts = stripped.split(maxsplit=2)
        if len(parts) == 1:
            return MaterialsCommand(action="open")
        sub = parts[1].strip().lower()
        if sub in {"clear", "-c", "--clear"}:
            return MaterialsCommand(action="clear")
        if sub in {"rm", "remove", "del", "delete"}:
            target = parts[2].strip() if len(parts) > 2 else ""
            if target:
                return MaterialsCommand(action="remove", target=target)
            return MaterialsCommand(action="open")
        return MaterialsCommand(action="open")

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
    "MaterialsCommand",
    "ModelCommand",
    "ParsedInput",
    "RewindCommand",
    "SandboxCommand",
    "TokensCommand",
    "TrustModeCommand",
    "parse_slash_command",
]
