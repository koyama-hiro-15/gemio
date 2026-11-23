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
    F7->Backspace (全選択消去・Undo可能) および F4 (エディタへフォーカス移動) が動作すること
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

            # フッター表示用の F4 バインディングが登録されていること
            f4_bindings = [b for b in app.BINDINGS if b.key == "f4" and b.show]
            assert len(f4_bindings) == 1

            editor = app.composer.editor
            assert app.focused is editor
            # 未入力時のプレースホルダーはIME未確定文字と連続しないよう半角スペース1つで始まること
            assert editor.placeholder == " プロンプトまたはコマンド (/help 等) を入力..."
            rendered_first_line = "".join(seg.text for seg in editor.render_line(0))
            assert rendered_first_line.startswith(" プロンプト")

            editor.insert("1行目のテキスト\n2行目のテキスト")
            await pilot.pause()
            assert "1行目のテキスト" in editor.text

            # Textual標準の F7 (全選択) -> Backspace で一括消去され、Ctrl+Z で元に戻せること
            await pilot.press("f7")
            await pilot.press("backspace")
            await pilot.pause()
            assert editor.text == ""

            await pilot.press("ctrl+z")
            await pilot.pause()
            assert editor.text == "1行目のテキスト\n2行目のテキスト"

            # Ctrl+Y がエディタの Redo として衝突なく動作すること
            await pilot.press("ctrl+y")
            await pilot.pause()
            assert editor.text == ""

            # タイムラインへフォーカスを移した後、F4 でエディタへ戻れること
            app.timeline.focus()
            await pilot.pause()
            assert app.focused is app.timeline

            await pilot.press("f4")
            await pilot.pause()
            assert app.focused is editor


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

                # /cp (/copy) コマンドで直近回答がコピーされること
                with patch.object(app, "copy_to_clipboard") as mock_copy:
                    app.composer.editor.insert("/cp")
                    await pilot.press("ctrl+j")
                    await pilot.pause()
                    mock_copy.assert_called_once_with("確認結果: **すべて正常** です。")

                # /rewind コマンドで末尾 TurnWidget が除去されること
                app.composer.editor.insert("/rewind")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert len(list(app.timeline.query(TurnWidget))) == 0


async def test_slash_commands_web_badge_toggle_and_search_card_selective_save(tmp_path):
    """
    1. デフォルト +WEB で起動し、ヘッダーの #hdr-web クリックおよび /search on|off で
       +WEB (.badge-web-on) / -WEB (.badge-web-off) が即座に切り替わること
    2. /search <query> は単発検索として表示され自動では M:0 のままであり、
       カードの [+ 資料に追加] ボタン押下（または /search --save）で M:1 へ保持されること
    """
    from textual.widgets import Button

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
                web_badge = app.status_header.query_one("#hdr-web", Static)
                assert app.status_header.snapshot.web_badge_text == "+WEB"
                assert web_badge.has_class("badge-web-on")
                assert web_badge.styles.text_style.reverse is not True

                # +WEB 状態でマウスホバー時に反転ハイライトすること
                await pilot.hover("#hdr-web")
                await pilot.pause()
                assert web_badge.styles.text_style.reverse is True

                # 1. #hdr-web バッジのクリックで -WEB (.badge-web-off) に切り替わること
                await pilot.click("#hdr-web")
                await pilot.pause()
                assert app.status_header.snapshot.web_badge_text == "-WEB"
                assert web_badge.has_class("badge-web-off")
                assert not web_badge.has_class("badge-web-on")

                # -WEB 状態でもマウスホバー時に反転ハイライトすること
                await pilot.hover("#hdr-project")
                await pilot.pause()
                assert web_badge.styles.text_style.reverse is not True
                await pilot.hover("#hdr-web")
                await pilot.pause()
                assert web_badge.styles.text_style.reverse is True

                # /search on で +WEB に戻ること
                app.composer.editor.insert("/search on")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert app.status_header.snapshot.web_badge_text == "+WEB"
                assert web_badge.has_class("badge-web-on")

                # /edit と /model の即時ヘッダー更新
                app.composer.editor.insert("/edit on")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert app.status_header.snapshot.approval_badge_text == "EDIT"

                app.composer.editor.insert("/model argon")
                await pilot.press("ctrl+j")
                await pilot.pause()
                assert app.status_header.snapshot.current_model == "gemini-4-argon"

                # 2. /search 実行直後は自動保持されず M:0 のままで [+ 資料に追加] が有効であること
                app.composer.editor.insert("/search Python *raw* [tag]")
                await pilot.press("ctrl+j")
                await app.workers.wait_for_complete()
                await pilot.pause()

                search_cards = list(app.timeline.query(SearchTurnWidget))
                assert len(search_cards) == 1
                card = search_cards[0]
                query_static = card.query_one(".search-query-body", Static)
                assert "/search Python *raw* [tag]" in _render_widget_plain(
                    query_static, width=100
                )
                assert app.status_header.snapshot.materials_count == 0
                save_btn = card.query_one("#btn-save-material", Button)
                assert save_btn.display is True
                assert save_btn.disabled is False

                # [+ 資料に追加] ボタンのクリックで M:1 に増え、ボタンが保持済で無効化されること
                await pilot.click("#btn-save-material")
                await pilot.pause()
                assert app.status_header.snapshot.materials_count == 1
                assert save_btn.disabled is True
                assert "資料保持済" in str(save_btn.label)

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


async def test_phase1_layout_header_copy_buttons_collapsible_and_composer_height(tmp_path):
    """
    1. StatusHeader の全バッジが 80x24 画面内に収まって表示されること
    2. UserMessageCard / AssistantMessageCard / SearchTurnWidget の Copy ボタンがヘッダー行内に収まること
    3. ActivityGroup 展開時の高さが内容の行数（タイトル1行 + ログ行数）に一致すること
    4. PromptTextArea が改行ごとに即座に1行ずつ伸長すること（border分の2行を考慮）
    """
    from textual.widgets import Button

    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        app = GemioApp(ctrl)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            # 1. StatusHeader の全バッジ（#hdr-web 含む）が 80x24 ヘッダー領域内に収まっていること
            hdr = app.status_header
            for wid in (
                "#hdr-project",
                "#hdr-mode",
                "#hdr-approval",
                "#hdr-web",
                "#hdr-model",
                "#hdr-tokens",
            ):
                badge = hdr.query_one(wid)
                assert badge.region.width > 0
                assert hdr.region.contains_region(badge.region), f"{wid} がヘッダー外にはみ出しています"

            # 2. 対話ログ・検索カードの Copy ボタンが各カードのヘッダー行内に収まること
            tw = TurnWidget("hello\nworld")
            app.timeline.mount_entry(tw, force_scroll=True)
            tw.activity_group.add_tool_call("12:00:00", "Tool call: read_file (file_path='a.py')")
            tw.activity_group.add_tool_call("12:00:01", "Tool call: write_file (file_path='b.py')")
            tw.assistant_card.set_response("AI answer", timestamp="12:00:02")
            sw = SearchTurnWidget("query")
            app.timeline.mount_entry(sw, force_scroll=True)
            await pilot.pause()

            for card in (tw.user_card, tw.assistant_card, sw):
                row = card.query_one(".card-header-row")
                copy_btn = card.query_one(".copy-btn", Button)
                assert copy_btn.region.width > 0
                assert row.region.contains_region(copy_btn.region)

            # 3. ActivityGroup 展開時の高さが内容行数（タイトル1行 + 2行 = 3行）と一致すること
            col = tw.activity_group.query_one(Collapsible)
            col.collapsed = False
            await pilot.pause()
            items = tw.activity_group.query_one(".activity-items")
            assert items.region.height == 2
            assert tw.activity_group.region.height == 3

            # 4. PromptTextArea が1行→2行→3行で即座に内寸1→2→3行へ伸長すること
            ed = app.composer.editor
            ed.load_text("line1")
            await pilot.pause()
            assert ed.region.height == 3
            assert ed.content_region.height == 1

            ed.load_text("line1\nline2")
            await pilot.pause()
            assert ed.region.height == 4
            assert ed.content_region.height == 2

            ed.load_text("line1\nline2\nline3")
            await pilot.pause()
            assert ed.region.height == 5
            assert ed.content_region.height == 3


async def test_composer_scrolls_immediately_on_newline_at_max_height(tmp_path):
    """
    PromptTextArea が最大高さ（外寸8行・内寸6行）に達した状態で最下行から改行したとき、
    文字入力を待たずに即座に新しい行へスクロール追従すること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        app = GemioApp(ctrl)

        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.composer.editor
            # 6行入力して最大高さ（外寸8行、内寸6行）まで拡大させる
            editor.insert("1\n2\n3\n4\n5\n6")
            await pilot.pause()
            assert editor.outer_size.height == 8
            assert editor.content_region.height == 6
            assert editor.scroll_y == 0

            # 6行目→7行目、7行目→8行目、8行目→9行目の各改行直後に即座に追従すること
            for expected_row, expected_scroll in ((6, 1), (7, 2), (8, 3)):
                await pilot.press("enter")
                await pilot.pause()
                assert editor.cursor_location == (expected_row, 0)
                assert editor.max_scroll_y == expected_scroll
                assert editor.scroll_y == expected_scroll


async def test_thinking_model_enables_thoughts_and_displays_in_activity_group(tmp_path):
    """
    models.get() で thinking=True と判定されたモデルでは ThinkingConfig(include_thoughts=True) が付与され、
    抽出された思考テキストが重複なく ActivityGroup に表示されること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client") as mock_sdk_cls:
        mock_sdk = MagicMock()
        mock_sdk_cls.return_value = mock_sdk
        mock_sdk.models.get.return_value = types.Model(
            name="models/gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            supported_actions=["generateContent"],
            thinking=True,
            input_token_limit=1_048_576,
        )

        resp = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(text="問題を段階的に整理します。", thought=True),
                            types.Part(text="最終的な結論です。"),
                        ],
                    )
                )
            ]
        )
        mock_sdk.models.generate_content.return_value = resp

        ctrl = GemioController(cfg)
        app = GemioApp(ctrl)
        async with app.run_test(size=(80, 24)) as pilot:
            app.composer.editor.insert("推論テスト")
            await pilot.press("ctrl+j")
            await app.workers.wait_for_complete()
            await pilot.pause()

            gen_cfg = mock_sdk.models.generate_content.call_args.kwargs["config"]
            assert "thinking_config" in gen_cfg
            assert gen_cfg["thinking_config"].include_thoughts is True

            turns = list(app.timeline.query(TurnWidget))
            assert len(turns) == 1
            ag = turns[0].activity_group
            assert ag.display is True
            assert ag.thought_count == 1
            col = ag.query_one(Collapsible)
            assert "Thinking (1)" in str(col.title)
            assert turns[0].assistant_card.response_text == "最終的な結論です。"


async def test_agent_web_search_tool_in_tui_confirms_even_in_trust_mode_and_logs_sources(
    tmp_path,
):
    """
    TRUST モード中であっても ChatAgent が web_search ツールを呼んだ際は ApprovalModal（全文表示）が開き、
    承認 ('y') するとステートレス検索結果を作業文脈に統合して回答し、ActivityGroup に出典が記録されること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        ctrl.set_trust_mode(True)
        assert ctrl.ai_client is not None
        assert ctrl.search_agent is not None

        fc_search = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="web_search",
                    args={"query": "2026年 最新のPythonパッケージング標準仕様"},
                )
            ],
        )
        resp_tool = MagicMock(
            candidates=[MagicMock(content=fc_search)],
            function_calls=[fc_search.parts[0].function_call],
            text="",
        )
        resp_final = MagicMock(
            candidates=[],
            function_calls=None,
            text="最新の仕様に基づいて設定を構成しました。",
        )

        mock_search_res = SearchResult(
            query="2026年 最新のPythonパッケージング標準仕様",
            response_text="pyproject.toml の標準フィールド解説。",
            sources=[
                {
                    "title": "PyPA Specifications",
                    "url": "https://packaging.python.org/en/latest/specifications/",
                }
            ],
            created_at="16:10:00",
            succeeded=True,
        )

        with patch.object(
            ctrl.ai_client, "generate_content", side_effect=[resp_tool, resp_final]
        ), patch.object(
            ctrl.search_agent, "search", return_value=mock_search_res
        ):
            app = GemioApp(
                ctrl,
                approval_screen_factory=lambda desc: _DummyApprovalModal(desc),
            )
            async with app.run_test(size=(80, 24)) as pilot:
                app.composer.editor.insert("最新のパッケージング仕様を調べて教えて")
                await pilot.press("ctrl+j")

                for _ in range(30):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, _DummyApprovalModal):
                        break
                assert isinstance(app.screen, _DummyApprovalModal)
                assert "2026年 最新のPythonパッケージング標準仕様" in app.screen.desc

                await pilot.press("y")
                await app.workers.wait_for_complete()
                await pilot.pause()

                turns = list(app.timeline.query(TurnWidget))
                assert len(turns) == 1
                assert (
                    turns[0].assistant_card.response_text
                    == "最新の仕様に基づいて設定を構成しました。"
                )
                assert "web_search" in turns[0].activity_group.tool_names
                assert len(ctrl.chat_session.materials) == 0
