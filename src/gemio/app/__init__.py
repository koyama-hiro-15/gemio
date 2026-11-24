"""
Gemio アプリケーション制御層（gemio.app）公開ファサード。

UI フレームワーク（Textual 等）に依存しないセッション統括・コマンド解析・状態モデルを提供します。
"""

from gemio.app.callbacks import InteractionHandler
from gemio.app.commands import (
    ChatPromptInput,
    ClearCommand,
    CompactCommand,
    CopyCommand,
    EditModeCommand,
    ExitCommand,
    HelpCommand,
    MaterialsCommand,
    ModelCommand,
    ParsedInput,
    RewindCommand,
    SandboxCommand,
    TokensCommand,
    TrustModeCommand,
    parse_slash_command,
)
from gemio.app.config import (
    APP_DEFAULT_MAX_ITERATIONS,
    AppConfig,
    AppStartupError,
    validate_project_dir,
)
from gemio.app.controller import GemioController
from gemio.app.prompts import APP_MATERIALS_GUIDANCE_INSTRUCTION
from gemio.app.state import (
    ContextHealth,
    SessionSnapshot,
)

__all__ = [
    "APP_DEFAULT_MAX_ITERATIONS",
    "APP_MATERIALS_GUIDANCE_INSTRUCTION",
    "AppConfig",
    "AppStartupError",
    "ChatPromptInput",
    "ClearCommand",
    "CompactCommand",
    "ContextHealth",
    "CopyCommand",
    "EditModeCommand",
    "ExitCommand",
    "GemioController",
    "HelpCommand",
    "InteractionHandler",
    "MaterialsCommand",
    "ModelCommand",
    "ParsedInput",
    "RewindCommand",
    "SandboxCommand",
    "SessionSnapshot",
    "TokensCommand",
    "TrustModeCommand",
    "parse_slash_command",
    "validate_project_dir",
]
