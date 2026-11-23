"""
UI 表示およびセッション診断用の不変状態モデル（State Snapshot）定義モジュール。

UI フレームワーク固有のマークアップやウィジェット型を持たず、
ステータスバー・モーダル・テストが共通参照できる純粋なデータ構造を提供します。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gemio.core.models import (
    CONTEXT_TOKEN_DANGER_THRESHOLD,
    CONTEXT_TOKEN_WARNING_THRESHOLD,
)
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
)

ContextHealth = Literal["normal", "warning", "danger"]


@dataclass(frozen=True)
class SessionSnapshot:
    """ある時点における Gemio セッション全体の状態スナップショット。"""

    project_name: str
    project_path: Path
    is_dev_mode: bool
    execution_mode: str
    container_name: str | None
    approval_mode: str
    current_model: str
    web_search_enabled: bool = True
    context_tokens: int = 0
    cumulative_tokens: int = 0
    total_api_calls: int = 0
    materials_count: int = 0
    turn_count: int = 0
    has_summary: bool = False
    active_skills: tuple[str, ...] = ()
    dynamic_tools_enabled: bool = False

    @property
    def context_health(self) -> ContextHealth:
        """直近コンテキストトークン量に基づく3段階の健全性ステータスを返します。"""
        if self.context_tokens >= CONTEXT_TOKEN_DANGER_THRESHOLD:
            return "danger"
        if self.context_tokens >= CONTEXT_TOKEN_WARNING_THRESHOLD:
            return "warning"
        return "normal"

    @property
    def mode_badge_text(self) -> str:
        """ステータスバー向けのコンパクトな動作モード表記を返します。"""
        if not self.is_dev_mode:
            return "STD"
        if self.execution_mode == "in_container":
            return "DEV:in-container"
        label = self.container_name or "docker"
        return f"DEV:{label}"

    @property
    def mode_display_label(self) -> str:
        """ウェルカム表示や診断画面向けの日本語動作モード表記を返します。"""
        if not self.is_dev_mode:
            return "基本モード (Standard)"
        if self.execution_mode == "in_container":
            return "開発者モード (Dev Container内実行)"
        return f"開発者モード (Docker: {self.container_name})"

    @property
    def approval_badge_text(self) -> str:
        """ステータスバー向けのコンパクトな承認モード表記を返します。"""
        if self.approval_mode == APPROVAL_MODE_TRUST:
            return "TRUST"
        if self.approval_mode == APPROVAL_MODE_EDIT:
            return "EDIT"
        return "NORMAL"

    @property
    def approval_display_label(self) -> str:
        """通知や案内画面向けの日本語承認モード表記を返します。"""
        if self.approval_mode == APPROVAL_MODE_TRUST:
            return "トラストモード（自動承認）"
        if self.approval_mode == APPROVAL_MODE_EDIT:
            return "エディットモード（編集自動 / 削除・コマンド確認）"
        return "通常承認モード（都度確認）"

    @property
    def web_badge_text(self) -> str:
        """ステータスバー向けの外部検索支援ツール有効/無効バッジ表記（+WEB / -WEB）を返します。"""
        return "+WEB" if self.web_search_enabled else "-WEB"

    @property
    def web_display_label(self) -> str:
        """案内画面向けの日本語検索支援状態表記を返します。"""
        return "Web検索支援: ON" if self.web_search_enabled else "Web検索支援: OFF"


__all__ = [
    "ContextHealth",
    "SessionSnapshot",
]
