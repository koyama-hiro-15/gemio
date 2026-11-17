"""
Gemio アプリケーション起動設定（AppConfig）および起動前検証モジュール。

本モジュールは UI フレームワーク（Textual 等）や sys.exit に依存せず、
設定値の正規化と起動前ディレクトリ検証を行います。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemio.core.models import DEFAULT_AI_MODEL_NAME, resolve_model_name
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_TRUST,
)

APP_DEFAULT_MAX_ITERATIONS = 20


class AppStartupError(Exception):
    """起動前の設定・対象ディレクトリ・実行環境の検証失敗を表す例外。"""


def validate_project_dir(raw_dir: str | Path) -> Path:
    """
    対象プロジェクトディレクトリを絶対パスへ解決し、存在かつディレクトリであることを検証します。
    検証に失敗した場合は AppStartupError を送出します。
    """
    project_path = Path(raw_dir).resolve()
    if not project_path.exists():
        raise AppStartupError(
            f"エラー: 指定されたディレクトリが存在しません: {project_path}"
        )
    if not project_path.is_dir():
        raise AppStartupError(
            f"エラー: 指定されたパスはディレクトリではありません: {project_path}"
        )
    return project_path


@dataclass(frozen=True)
class AppConfig:
    """Gemio アプリケーションセッションの正規化済み起動設定。"""

    project_path: Path
    initial_model: str = DEFAULT_AI_MODEL_NAME
    dev_mode: bool = False
    container_name: str | None = None
    requested_edit: bool = False
    requested_trust: bool = False
    dynamic_tools: bool = False
    max_iterations: int = APP_DEFAULT_MAX_ITERATIONS

    @property
    def resolved_initial_model(self) -> str:
        """エイリアス解決済みの初期AIモデル名を返します。"""
        return resolve_model_name(self.initial_model)

    @classmethod
    def from_namespace(cls, args: Any) -> "AppConfig":
        """argparse.Namespace 等の引数オブジェクトから検証済み AppConfig を構築します。"""
        raw_dir = getattr(args, "project_dir", ".")
        validated_path = validate_project_dir(raw_dir)
        raw_model = getattr(args, "model", DEFAULT_AI_MODEL_NAME) or DEFAULT_AI_MODEL_NAME
        return cls(
            project_path=validated_path,
            initial_model=resolve_model_name(raw_model),
            dev_mode=bool(getattr(args, "dev", False)),
            container_name=getattr(args, "container", None),
            requested_edit=bool(getattr(args, "edit", False)),
            requested_trust=bool(getattr(args, "trust", False)),
            dynamic_tools=bool(getattr(args, "dynamic_tools", False)),
        )

    def resolve_initial_approval_mode(
        self,
        trust_consented: bool = False,
    ) -> tuple[str, bool]:
        """
        起動オプション（--trust / --edit）とトラストモード同意結果に基づき、
        初期承認モードとフォールバック発生有無 `(approval_mode, fallback_from_trust)` を返します。
        """
        if self.requested_trust:
            if trust_consented:
                return APPROVAL_MODE_TRUST, False
            if self.requested_edit:
                return APPROVAL_MODE_EDIT, True
            return APPROVAL_MODE_NORMAL, True

        if self.requested_edit:
            return APPROVAL_MODE_EDIT, False

        return APPROVAL_MODE_NORMAL, False


__all__ = [
    "APP_DEFAULT_MAX_ITERATIONS",
    "AppConfig",
    "AppStartupError",
    "validate_project_dir",
]
