"""
対話タイムライン先頭に表示するコンパクトな WelcomeBanner ウィジェット。
"""

from rich.text import Text
from textual.widgets import Static

from gemio.app.state import SessionSnapshot
from gemio.project import APPROVAL_MODE_EDIT, APPROVAL_MODE_TRUST


class WelcomeBanner(Static):
    """起動時およびセッション初期化時に表示する案内バナー。"""

    def __init__(
        self,
        snapshot: SessionSnapshot,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._snapshot = snapshot

    def on_mount(self) -> None:
        self.update_snapshot(self._snapshot)

    def update_snapshot(self, snap: SessionSnapshot) -> None:
        self._snapshot = snap
        body = Text()
        body.append("Welcome to Gemio! ", style="bold green")
        body.append("あなたの作業と開発を安全にサポートします。\n", style="")

        body.append("  Project : ", style="bold cyan")
        body.append(f"{snap.project_path}\n")

        body.append("  Mode    : ", style="bold cyan")
        mode_style = "bold magenta" if snap.is_dev_mode else "bold green"
        body.append(f"{snap.mode_display_label}", style=mode_style)
        body.append("  |  ", style="dim")

        if snap.approval_mode == APPROVAL_MODE_TRUST:
            appr_style = "bold red"
        elif snap.approval_mode == APPROVAL_MODE_EDIT:
            appr_style = "bold yellow"
        else:
            appr_style = "bold green"
        body.append(f"{snap.approval_display_label}\n", style=appr_style)

        body.append("  Model   : ", style="bold cyan")
        body.append(f"{snap.current_model}\n\n")

        body.append(
            "  送信: Ctrl+J  |  改行: Enter  |  全選択・消去: F7 ➔ Backspace  |  中断: Esc\n",
            style="dim",
        )
        body.append(
            "  入力欄へ移動: F4  |  コマンド検索: Ctrl+P  |  ヘルプ: F1 (/help)  |  トークン: /tokens",
            style="dim",
        )
        self.update(body)


__all__ = [
    "WelcomeBanner",
]
