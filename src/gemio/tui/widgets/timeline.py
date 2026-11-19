"""
対話ターン群を縦方向に積み上げ、スマートオートスクロールを提供する ChatTimeline ウィジェット。
"""

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Markdown, Static

from gemio.app.state import SessionSnapshot
from gemio.tui.widgets.turn import TurnWidget
from gemio.tui.widgets.welcome import WelcomeBanner


class ChatTimeline(VerticalScroll):
    """
    独立スクロール可能なメイン対話タイムライン。
    ユーザーが上方向へスクロールして過去ログを閲覧している最中は自動スクロールを抑止します。
    """

    can_focus = True

    def __init__(
        self,
        initial_snapshot: SessionSnapshot,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._initial_snapshot = initial_snapshot

    def compose(self):
        yield WelcomeBanner(self._initial_snapshot)

    def is_near_bottom(self, threshold: float = 3.0) -> bool:
        """現在のスクロール位置が最下部付近にあるかどうかを判定します。"""
        if self.max_scroll_y <= 0:
            return True
        return (self.max_scroll_y - self.scroll_y) <= threshold

    def mount_entry(self, widget: Widget, *, force_scroll: bool = False) -> None:
        """タイムライン末尾にウィジェットを追加し、必要に応じて最下部へ追従スクロールします。"""
        should_follow = force_scroll or self.is_near_bottom()
        self.mount(widget)
        if should_follow:
            self.call_after_refresh(self.scroll_end, animate=False)

    def follow_if_near_bottom(self) -> None:
        """最下部付近を表示中の場合のみ、コンテンツ伸長に合わせて末尾へ追従します。"""
        if self.is_near_bottom():
            self.call_after_refresh(self.scroll_end, animate=False)

    def append_summary_card(self, summary_markdown: str) -> None:
        """コンテキスト圧縮（/compact）実行後の引き継ぎ要約カードをタイムラインに表示します。"""
        header = Static("Context Summary (圧縮済み履歴の引き継ぎ要約)", markup=False)
        body = Markdown(summary_markdown)
        self.mount_entry(header, force_scroll=True)
        self.mount_entry(body, force_scroll=True)

    def remove_last_turns(self, count: int = 1) -> int:
        """
        末尾から指定数の TurnWidget を DOM から除去し、実際に削除した個数を返します（/rewind 連動）。
        """
        if count <= 0:
            return 0
        turn_widgets = list(self.query(TurnWidget))
        targets = turn_widgets[-count:]
        for w in targets:
            w.remove()
        return len(targets)

    def reset_timeline(self, snapshot: SessionSnapshot) -> None:
        """WelcomeBanner 以外の全対話ウィジェットを除去し、WelcomeBanner を最新状態へ更新します。"""
        welcome_list = list(self.query(WelcomeBanner))
        for child in list(self.children):
            if not isinstance(child, WelcomeBanner):
                child.remove()

        if welcome_list:
            welcome_list[0].update_snapshot(snapshot)
        else:
            self.mount(WelcomeBanner(snapshot))
        self.call_after_refresh(self.scroll_home, animate=False)


__all__ = [
    "ChatTimeline",
]
