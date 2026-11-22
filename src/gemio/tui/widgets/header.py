"""
画面最上部に常駐し、セッション状態（モード・承認・Web検索ON/OFF・モデル・トークン/資料数）を表示する StatusHeader ウィジェット。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from gemio.app.state import SessionSnapshot
from gemio.project import APPROVAL_MODE_EDIT, APPROVAL_MODE_TRUST
from gemio.tui.messages import HeaderBadgeClicked, HeaderBadgeTarget


def format_compact_tokens(count: int) -> str:
    """トークン数を 80 列画面でも収まりの良い短縮表記（例: 850, 14.2k, 1.2M）へ整形します。"""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


class _ClickableBadge(Static):
    """クリック時に指定ターゲットの HeaderBadgeClicked メッセージを送出するバッジウィジェット。"""

    def __init__(
        self,
        label: str,
        target: HeaderBadgeTarget,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(label, markup=False, id=id, classes=classes)
        self._target: HeaderBadgeTarget = target

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(HeaderBadgeClicked(self._target))


class StatusHeader(Widget):
    """Gemio TUI の上部常駐ステータスヘッダー。"""

    def __init__(
        self,
        snapshot: SessionSnapshot,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._snapshot = snapshot

    @property
    def snapshot(self) -> SessionSnapshot:
        return self._snapshot

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("", markup=False, id="hdr-project", classes="header-brand")
            yield Static("|", markup=False, classes="header-sep")
            yield _ClickableBadge(
                "",
                "sandbox",
                id="hdr-mode",
                classes="header-badge",
            )
            yield Static("|", markup=False, classes="header-sep")
            yield _ClickableBadge(
                "",
                "approval",
                id="hdr-approval",
                classes="header-badge",
            )
            yield _ClickableBadge(
                "",
                "web_search",
                id="hdr-web",
                classes="header-badge",
            )
            yield Static("|", markup=False, classes="header-sep")
            yield _ClickableBadge(
                "",
                "model",
                id="hdr-model",
                classes="header-badge badge-model",
            )
            yield Static("|", markup=False, classes="header-sep")
            yield _ClickableBadge(
                "",
                "tokens",
                id="hdr-tokens",
                classes="header-badge",
            )

    def on_mount(self) -> None:
        self._apply_snapshot(self._snapshot)

    def update_snapshot(self, snapshot: SessionSnapshot) -> None:
        """最新の SessionSnapshot を受け取り、各バッジの表示内容と警告色を即時更新します。"""
        self._snapshot = snapshot
        if self.is_mounted:
            self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snap: SessionSnapshot) -> None:
        proj_w = self.query_one("#hdr-project", Static)
        mode_w = self.query_one("#hdr-mode", _ClickableBadge)
        appr_w = self.query_one("#hdr-approval", _ClickableBadge)
        web_w = self.query_one("#hdr-web", _ClickableBadge)
        model_w = self.query_one("#hdr-model", _ClickableBadge)
        tok_w = self.query_one("#hdr-tokens", _ClickableBadge)

        proj_w.update(f"[Gemio] {snap.project_name}")

        mode_w.update(snap.mode_badge_text)
        mode_w.set_class(not snap.is_dev_mode, "badge-mode-std")
        mode_w.set_class(snap.is_dev_mode, "badge-mode-dev")

        appr_w.update(snap.approval_badge_text)
        appr_w.set_class(
            snap.approval_mode not in {APPROVAL_MODE_EDIT, APPROVAL_MODE_TRUST},
            "badge-approval-normal",
        )
        appr_w.set_class(
            snap.approval_mode == APPROVAL_MODE_EDIT,
            "badge-approval-edit",
        )
        appr_w.set_class(
            snap.approval_mode == APPROVAL_MODE_TRUST,
            "badge-approval-trust",
        )

        web_w.update(snap.web_badge_text)
        web_w.set_class(snap.web_search_enabled, "badge-web-on")
        web_w.set_class(not snap.web_search_enabled, "badge-web-off")

        model_w.update(snap.current_model)

        ctx_str = format_compact_tokens(snap.context_tokens)
        tok_text = f"Ctx:{ctx_str} M:{snap.materials_count}"
        if snap.active_skills:
            tok_text += f" S:{len(snap.active_skills)}"
        tok_w.update(tok_text)

        health = snap.context_health
        tok_w.set_class(health == "normal", "badge-ctx-normal")
        tok_w.set_class(health == "warning", "badge-ctx-warning")
        tok_w.set_class(health == "danger", "badge-ctx-danger")


__all__ = [
    "StatusHeader",
    "format_compact_tokens",
]
