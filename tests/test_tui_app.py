import threading
from unittest.mock import MagicMock, patch

from google.genai import types
from rich.console import Console
from textual import events
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Footer, Static

from gemio.app import AppConfig, GemioController
from gemio.core.search import SearchResult
from gemio.tui import GemioApp
from gemio.tui.widgets import (
    ActivityBar,
    ChatTimeline,
    PromptComposer,
    SearchTurnWidget,
    StatusHeader,
    TurnWidget,
)


def _render_widget_plain(widget, width: int = 80) -> str:
    """Textual Static ウィジェットの render() 出力をプレーンテキスト化して検証します。"""
    console = Console(width=width, record=True)
    console.print(widget.render())
    return console.export_text()


class _DummyApprovalModal(ModalScreen[str]):
    """Phase 2 で ThreadModalBridge の往復同期動作を検証するための軽量テストモーダル。"""

    def __init__(self, desc: str) -> None:
        super().__init__()
        self.desc = desc

    def compose(self) -> ComposeResult:
        yield Static(self.desc, markup=False)

    def on_key(self, event: events.Key) -> None:
        if event.key in {"y", "n", "a", "k"}:
            event.stop()
            self.dismiss(event.key)


async def test_tui_layout_80x24_and_composer_keybindings(tmp_path):
    """
    最小画面サイズ (80x24) で4ゾーンすべてが正常にマウントされ、
    PromptTextArea の初期フォーカスおよび Alt+Backspace (Undo可能全消去) が動作すること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        app = GemioApp(ctrl)

        async with app.run_test(size=(80, 24)) as pilot:
            assert app.query_one(StatusHeader) is not None
            assert app.query_one(ChatTimeline) is not None
            assert app.query_one(ActivityBar) is not None
            assert app.query_one(PromptComposer) is not None
            assert app.query_one(Footer) is not None

            editor = app.composer.editor
            assert app.focused is editor

            editor.insert("1行目のテキスト\n2行目のテキスト")
            await pilot.pause()
            assert "1行目のテキスト" in editor.text

            # Alt+Backspace で一括全消去され、Undo で元に戻せること
            await pilot.press("alt+backspace")
            await pilot.pause()
            assert editor.text == ""

            editor.undo()
            await pilot.pause()
            assert editor.text == "1行目のテキスト\n2行目のテキスト"


async def test_user_prompt_renders_raw_plain_text_while_assistant_renders_markdown_and_tools_collapse(
    tmp_path,
):
    """
    1. UserMessageCard が Markdown や Rich マークアップを一切解釈せず生テキストをそのまま表示すること
    2. ツール呼び出しが ActivityGroup (折りたたみ状態) に格納されること
    3. AssistantMessageCard が Markdown で回答を表示し、/rewind で末尾ターンが除去されること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        # 1回目のAPI応答: get_current_date ツール呼び出し / 2回目: Markdown回答
        fc_content = types.Content(
            role="model",
            parts=[types.Part.from_function_call(name="get_current_date", args={})],
        )
        resp_tool = MagicMock()
        resp_tool.candidates = [MagicMock(content=fc_content)]
        resp_tool.function_calls = [fc_content.parts[0].function_call]
        resp_tool.text = ""

        resp_final = MagicMock()
        resp_final.candidates = []
        resp_final.function_calls = None
        resp_final.text = "確認結果: **すべて正常** です。"

        raw_tricky_prompt = (
            "# 見出し記号\n"
            "def __init__(self): *斜体にならない* [bold red]タグ保持[/bold red] <tag>"
        )

        with patch.object(
            ctrl.ai_client,
            "generate_content",
            side_effect=[resp_tool, resp_final],
        ):
            app = GemioApp(ctrl)
            async with app.run_test(size=(80, 24)) as pilot:
                app.composer.editor.insert(raw_tricky_prompt)
                await pilot.press("ctrl+j")
                await app.workers.wait_for_complete()
                await pilot.pause()

                turns = list(app.timeline.query(TurnWidget))
                assert len(turns) == 1
                turn_w = turns[0]

                # ユーザー入力が生テキストのまま一切変換・消失せず保持描画されていること
                body_static = turn_w.user_card.query_one(".user-prompt-body", Static)
                rendered_user = _render_widget_plain(body_static, width=100)
                assert "# 見出し記号" in rendered_user
                assert "def __init__(self):" in rendered_user
                assert "*斜体にならない*" in rendered_user
                assert "[bold red]タグ保持[/bold red]" in rendered_user
                assert "<tag>" in rendered_user

                # ActivityGroup が表示され、デフォルトで折りたたまれていること
                assert turn_w.activity_group.display is True
                collapsible = turn_w.activity_group.query_one(Collapsible)
                assert collapsible.collapsed is True
                assert "get_current_date" in str(collapsible.title)

                # AI回答カードが表示されていること
                assert turn_w.assistant_card.display is True
                assert turn_w.assistant_card.response_text == "確認結果: **すべて正常** です。"

                # /rewind コマンドで末尾 TurnWidget が除去されること
                app.composer.editor.insert("/rewind")
                await pilot.press("alt+enter")
                await pilot.pause()
                assert len(list(app.timeline.query(TurnWidget))) == 0


async def test_slash_commands_status_header_update_and_search_card(tmp_path):
    """
    /edit, /model, /search, /clear が StatusHeader およびタイムラインへ即座に反映されること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.search_agent is not None

        ok_search = SearchResult(
            query="Python *raw* [tag]",
            response_text="### 検索サマリ\n最新仕様の解説です。",
            sources=[{"title": "Python.org", "url": "https://www.python.org/doc/"}],
            created_at="15:00:00",
            succeeded=True,
        )

        with patch.object(ctrl.search_agent, "search", return_value=ok_search):
            app = GemioApp(ctrl)
            async with app.run_test(size=(80, 24)) as pilot:
                # 1. /edit と /model の即時ヘッダー更新
                app.composer.editor.insert("/edit on")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert app.status_header.snapshot.approval_badge_text == "EDIT"

                app.composer.editor.insert("/model argon")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert app.status_header.snapshot.current_model == "gemini-4-argon"

                # 2. /search の実行と SearchTurnWidget 表示・資料カウント M:1 反映
                app.composer.editor.insert("/search Python *raw* [tag]")
                await pilot.press("ctrl+j")
                await app.workers.wait_for_complete()
                await pilot.pause()

                search_cards = list(app.timeline.query(SearchTurnWidget))
                assert len(search_cards) == 1
                query_static = search_cards[0].query_one(".search-query-body", Static)
                assert "/search Python *raw* [tag]" in _render_widget_plain(
                    query_static, width=100
                )
                assert app.status_header.snapshot.materials_count == 1

                # 3. /clear --all でタイムラインと資料カウントがリセットされること
                app.composer.editor.insert("/clear --all")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert len(list(app.timeline.query(SearchTurnWidget))) == 0
                assert app.status_header.snapshot.materials_count == 0


async def test_thread_modal_bridge_and_esc_safe_turn_cancel(tmp_path):
    """
    1. ワーカースレッドでの承認確認時に ThreadModalBridge が ModalScreen を表示し、
       メインスレッドをフリーズさせずに結果 ('y') をワーカースレッドへ返せること
    2. AI処理中の Esc キー押下でアプリを終了させず進行中ターンのみを中断できること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        # ケース1: write_file ツール呼び出し -> Approval モーダルで 'y' を押して完了
        target_file = tmp_path / "hello.txt"
        fc_write = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="write_file",
                    args={"file_path": "hello.txt", "content": "Hello TUI!"},
                )
            ],
        )
        resp_write = MagicMock()
        resp_write.candidates = [MagicMock(content=fc_write)]
        resp_write.function_calls = [fc_write.parts[0].function_call]
        resp_write.text = ""

        resp_done = MagicMock()
        resp_done.candidates = []
        resp_done.function_calls = None
        resp_done.text = "ファイルを作成しました。"

        with patch.object(
            ctrl.ai_client,
            "generate_content",
            side_effect=[resp_write, resp_done],
        ):
            app = GemioApp(
                ctrl,
                approval_screen_factory=lambda desc: _DummyApprovalModal(desc),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                app.composer.editor.insert("hello.txt を作って")
                await pilot.press("ctrl+j")

                # ワーカースレッドがモーダルをプッシュするまで少し待機
                for _ in range(30):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, _DummyApprovalModal):
                        break
                assert isinstance(app.screen, _DummyApprovalModal)

                # モーダル上で 'y' キーを押して承認
                await pilot.press("y")
                await app.workers.wait_for_complete()
                await pilot.pause()

                assert target_file.read_text(encoding="utf-8") == "Hello TUI!"
                assert app.activity_bar.is_busy is False

        # ケース2: 長時間処理中に Esc を押すとアプリは終了せずターンのみ中断すること
        started = threading.Event()
        unblock = threading.Event()

        def slow_generate(*args, **kwargs):
            started.set()
            unblock.wait(timeout=2.0)
            r = MagicMock()
            r.candidates = []
            r.function_calls = None
            r.text = "遅れて届いた応答"
            return r

        with patch.object(ctrl.ai_client, "generate_content", side_effect=slow_generate):
            app2 = GemioApp(ctrl)
            async with app2.run_test(size=(80, 24)) as pilot:
                app2.composer.editor.insert("重い質問")
                await pilot.press("ctrl+j")

                for _ in range(50):
                    await pilot.pause(0.01)
                    if started.is_set():
                        break
                assert started.is_set() is True
                assert app2.controller.is_busy is True

                # Esc キーでターン中断（アプリ自体は継続稼働）
                await pilot.press("escape")
                unblock.set()
                await app2.workers.wait_for_complete()
                await pilot.pause()

                assert app2.is_running is True
                assert app2.activity_bar.is_busy is False
                last_turn = app2.controller.chat_session.history.last_turn
                assert last_turn is not None
                assert last_turn.aborted is True
