import ast
from argparse import Namespace
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

from google.genai import types
import pytest

from gemio.app import (
    APP_WEB_SEARCH_DISABLED_INSTRUCTION,
    APP_WEB_SEARCH_ENABLED_INSTRUCTION,
    AppConfig,
    AppStartupError,
    ChatPromptInput,
    ClearCommand,
    CompactCommand,
    CopyCommand,
    EditModeCommand,
    ExitCommand,
    GemioController,
    HelpCommand,
    ModelCommand,
    RewindCommand,
    SandboxCommand,
    SearchCommand,
    TokensCommand,
    TrustModeCommand,
    parse_slash_command,
    validate_project_dir,
)
from gemio.core.search import SearchResult
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_TRUST,
    DeveloperProject,
)


def test_app_package_has_no_ui_framework_imports():
    """src/gemio/app/ 配下の全モジュールが textual / prompt_toolkit / rich をインポートしていないこと"""
    app_dir = Path(__file__).resolve().parent.parent / "src" / "gemio" / "app"
    py_files = sorted(app_dir.glob("*.py"))
    assert len(py_files) >= 6

    forbidden = {"textual", "prompt_toolkit", "rich"}
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        assert forbidden.isdisjoint(imported_roots), (
            f"{py_file.name} に UI フレームワーク依存が含まれています: {imported_roots & forbidden}"
        )


def test_app_config_validation_and_approval_resolution(tmp_path):
    """AppConfig のディレクトリ検証（AppStartupError）・Web検索初期値・初期承認モード解決が正確に動作すること"""
    # 1. 存在しないディレクトリ・ファイル指定時は AppStartupError となること
    with pytest.raises(AppStartupError):
        validate_project_dir(tmp_path / "missing_dir")

    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("hello", encoding="utf-8")
    with pytest.raises(AppStartupError):
        validate_project_dir(dummy_file)

    # 2. Namespace からの構築とモデルエイリアス解決・Web検索デフォルトON / --no-web-search OFF
    ns = Namespace(
        project_dir=str(tmp_path),
        model="argon",
        dev=False,
        container=None,
        edit=True,
        trust=True,
        dynamic_tools=True,
        no_web_search=False,
    )
    cfg = AppConfig.from_namespace(ns)
    assert cfg.project_path == tmp_path.resolve()
    assert cfg.resolved_initial_model == "gemini-4-argon"
    assert cfg.dynamic_tools is True
    assert cfg.enable_web_search is True

    ns_no_web = Namespace(project_dir=str(tmp_path), no_web_search=True)
    assert AppConfig.from_namespace(ns_no_web).enable_web_search is False

    # 3. --trust 同意時は TRUST、拒否時は --edit があれば EDIT へフォールバック
    assert cfg.resolve_initial_approval_mode(trust_consented=True) == (
        APPROVAL_MODE_TRUST,
        False,
    )
    assert cfg.resolve_initial_approval_mode(trust_consented=False) == (
        APPROVAL_MODE_EDIT,
        True,
    )

    cfg_trust_only = AppConfig(project_path=tmp_path, requested_trust=True)
    assert cfg_trust_only.resolve_initial_approval_mode(trust_consented=False) == (
        APPROVAL_MODE_NORMAL,
        True,
    )


def test_parse_slash_command_all_variants():
    """スラッシュコマンドパーサが全コマンド・エイリアス・オプション・通常入力を正確に構造化すること"""
    assert parse_slash_command("   \n\t  ") is None

    assert parse_slash_command("/exit") == ExitCommand()
    assert parse_slash_command("QUIT") == ExitCommand()

    assert parse_slash_command("/help") == HelpCommand()
    assert parse_slash_command("/info") == HelpCommand()

    assert parse_slash_command("/tokens") == TokensCommand()
    assert parse_slash_command("/usage") == TokensCommand()

    assert parse_slash_command("/sandbox") == SandboxCommand()
    assert parse_slash_command("/status") == SandboxCommand()

    assert parse_slash_command("/clear") == ClearCommand(clear_materials=False)
    assert parse_slash_command("/reset --all") == ClearCommand(clear_materials=True)
    assert parse_slash_command("/new -a") == ClearCommand(clear_materials=True)

    assert parse_slash_command("/edit") == EditModeCommand(enable=None)
    assert parse_slash_command("/edit on") == EditModeCommand(enable=True)
    assert parse_slash_command("/edit off") == EditModeCommand(enable=False)

    assert parse_slash_command("/trust") == TrustModeCommand(enable=None)
    assert parse_slash_command("/auto enable") == TrustModeCommand(enable=True)
    assert parse_slash_command("/trust disable") == TrustModeCommand(enable=False)

    assert parse_slash_command("/model") == ModelCommand(target_model=None)
    assert parse_slash_command("/model 3.8") == ModelCommand(target_model="3.8")

    assert parse_slash_command("/search") == SearchCommand(show_help=True)
    assert parse_slash_command("/s") == SearchCommand(show_help=True)
    assert parse_slash_command("/search on") == SearchCommand(toggle_enabled=True)
    assert parse_slash_command("/s enable") == SearchCommand(toggle_enabled=True)
    assert parse_slash_command("/search off") == SearchCommand(toggle_enabled=False)
    assert parse_slash_command("/s disable") == SearchCommand(toggle_enabled=False)
    assert parse_slash_command("/search --save") == SearchCommand(save_last=True)
    assert parse_slash_command("/s -s") == SearchCommand(save_last=True)
    assert parse_slash_command("/search --clear") == SearchCommand(clear_materials=True)
    assert parse_slash_command("/s -c") == SearchCommand(clear_materials=True)
    assert parse_slash_command("/search Python 3.15") == SearchCommand(
        query="Python 3.15"
    )

    assert parse_slash_command("/copy") == CopyCommand()
    assert parse_slash_command("/compact") == CompactCommand(keep_recent_turns=2)
    assert parse_slash_command("/compact 4") == CompactCommand(keep_recent_turns=4)
    assert parse_slash_command("/rewind") == RewindCommand(steps=1)
    assert parse_slash_command("/undo 3") == RewindCommand(steps=3)

    # スラッシュから始まるファイルパス記述等の通常プロンプトが誤判定されないこと
    assert parse_slash_command("/etc/hosts の設定形式を教えて") == ChatPromptInput(
        text="/etc/hosts の設定形式を教えて"
    )
    assert parse_slash_command("リファクタリングしてください") == ChatPromptInput(
        text="リファクタリングしてください"
    )


def test_controller_deferred_api_key_and_trust_consent_lifecycle(tmp_path):
    """
    1. in_container かつ環境変数キー未設定時はクライアント初期化を保留し、set_api_key で有効化されること
    2. --trust 指定時は同意を得るまで安全側モードで待機し、apply_initial_trust_consent で反映されること
    """
    cfg = AppConfig(
        project_path=tmp_path,
        dev_mode=True,
        requested_edit=True,
        requested_trust=True,
    )
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"), \
         patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
        ctrl = GemioController(cfg)
        assert ctrl.needs_api_key_input is True
        assert ctrl.is_client_ready is False
        assert ctrl.needs_initial_trust_consent is True
        # 同意前は安全側の EDIT モードかつ Web検索はデフォルト +WEB であること
        assert ctrl.project.approval_mode == APPROVAL_MODE_EDIT
        assert ctrl.web_search_enabled is True

        # APIキー未設定での対話実行はエラーになるが、スナップショット取得は安全に行えること
        snap = ctrl.get_snapshot()
        assert snap.mode_badge_text == "DEV:in-container"
        assert snap.approval_badge_text == "EDIT"
        assert snap.web_badge_text == "+WEB"
        with pytest.raises(RuntimeError):
            ctrl.run_chat_turn("テスト")

        # APIキー設定後にエージェントが利用可能になること
        with patch("gemio.core.client.genai.Client") as mock_genai:
            ctrl.set_api_key("DUMMY_CONTAINER_KEY")
            assert ctrl.needs_api_key_input is False
            assert ctrl.is_client_ready is True
            mock_genai.assert_called_once_with(api_key="DUMMY_CONTAINER_KEY")

        # トラスト同意を適用すると TRUST モードへ昇格すること
        mode, fb = ctrl.apply_initial_trust_consent(True)
        assert mode == APPROVAL_MODE_TRUST
        assert fb is False
        assert ctrl.needs_initial_trust_consent is False
        assert ctrl.get_snapshot().approval_badge_text == "TRUST"


def test_controller_single_search_and_selective_material_saving(tmp_path):
    """
    1. /search (run_search) は単発検索として unretained を返し、自動では MaterialStore へ追加しないこと
    2. save_search_result_to_materials により任意で資料追加・重複判定（added / duplicate）が行えること
    3. 失敗・中断時の状態（failed / aborted）およびスキル・セッションクリア連動が動作すること
    """
    skill_dir = tmp_path / ".gemio" / "skills" / "sample-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: サンプル\nrecommended_toolsets: [project_explore]\n---\n手順本文",
        encoding="utf-8",
    )

    cfg = AppConfig(project_path=tmp_path, dynamic_tools=True)
    mock_handler = MagicMock()
    mock_handler.request_approval.return_value = "y"
    mock_handler.ask_user.return_value = "選択肢1"
    mock_handler.confirm_iteration_limit.return_value = (True, 5)

    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg, interaction_handler=mock_handler)

        assert ctrl.on_approval("write a.py") == "y"
        assert ctrl.on_ask_user("質問", ["選択肢1"], "背景") == "選択肢1"
        assert ctrl.on_iteration_limit() == (True, 5)

        enabled, changed = ctrl.set_edit_mode()
        assert (enabled, changed) == (True, True)
        assert ctrl.set_edit_mode(True) == (True, False)

        assert ctrl.set_model("3.8") == "gemini-3.8-flash"
        assert ctrl.ai_client.model_name == "gemini-3.8-flash"

        ctrl.token_tracker._usage["last_prompt_tokens"] = 60_000
        assert ctrl.get_snapshot().context_health == "warning"
        ctrl.token_tracker._usage["last_prompt_tokens"] = 160_000
        assert ctrl.get_snapshot().context_health == "danger"

        # 検索未実行時は save_search_result_to_materials が None を返すこと
        assert ctrl.save_search_result_to_materials() is None

        # 1. 単発検索直後は unretained であり、MaterialStore には自動蓄積されないこと
        assert ctrl.search_agent is not None
        ok_res = SearchResult(
            query="Textual 8",
            response_text="Textual 8 の新機能解説",
            sources=[{"title": "Textual Docs", "url": "https://textual.textualize.io"}],
            created_at="10:00:00",
            succeeded=True,
        )
        with patch.object(ctrl.search_agent, "search", return_value=ok_res):
            out_single = ctrl.run_search("Textual 8")
            assert out_single.material_status == "unretained"
            assert out_single.total_materials == 0
            assert len(ctrl.chat_session.materials) == 0

        # 2. 任意保存を実行した時のみ added / duplicate になること
        saved_once = ctrl.save_search_result_to_materials()
        assert saved_once is not None
        assert saved_once.material_status == "added"
        assert saved_once.total_materials == 1

        saved_dup = ctrl.save_search_result_to_materials()
        assert saved_dup is not None
        assert saved_dup.material_status == "duplicate"
        assert saved_dup.total_materials == 1

        fail_res = SearchResult(
            query="Err",
            response_text="通信エラー",
            sources=[],
            created_at="10:01:00",
            succeeded=False,
        )
        with patch.object(ctrl.search_agent, "search", return_value=fail_res):
            out_fail = ctrl.run_search("Err")
            assert out_fail.material_status == "failed"
            assert out_fail.total_materials == 1

        abort_res = SearchResult(
            query="Cancel",
            response_text="中断",
            sources=[],
            created_at="10:02:00",
            succeeded=False,
            aborted=True,
        )
        with patch.object(ctrl.search_agent, "search", return_value=abort_res):
            out_abort = ctrl.run_search("Cancel")
            assert out_abort.material_status == "aborted"
            assert out_abort.total_materials == 1

        assert ctrl.clear_search_materials() == 1
        assert len(ctrl.chat_session.materials) == 0

        ctrl.skill_manager.activate("sample-skill")
        assert ctrl.get_snapshot().active_skills == ("sample-skill",)
        ctrl.clear_session(clear_materials=True)
        assert ctrl.get_snapshot().active_skills == ()
        assert ctrl.last_search_result is None


def test_agent_web_search_tool_always_confirms_full_prompt_in_all_modes_and_respects_toggle(
    tmp_path,
):
    """
    1. +WEB / -WEB の切替が有効ツール一覧（Static / Dynamic 両方）とシステムプロンプトへ即時同期すること
    2. NORMAL / EDIT / TRUST のすべての承認モードで必ず承認コールバックが呼ばれ、
       500文字超のクエリでも省略なしでプロンプト全文が提示されること
    3. 拒否 ('n') 時にファイル用サーキットブレーカー (_rejection_pending) が汚染されないこと
    4. プリフライト違反（ローカルパス混入）時は承認モーダルを出す前にブロックされること
    """
    approvals_seen: list[str] = []
    decision_box = {"val": "y"}

    def approval_cb(desc: str) -> str:
        approvals_seen.append(desc)
        return decision_box["val"]

    cfg = AppConfig(project_path=tmp_path, dynamic_tools=True)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg, approval_callback=approval_cb)
        assert ctrl.chat_agent is not None
        assert ctrl.search_agent is not None

        # 1. デフォルト +WEB 時のツール・プロンプト検証
        assert ctrl.web_search_enabled is True
        assert "web_search" in [f.__name__ for f in ctrl.chat_agent.tools]
        assert APP_WEB_SEARCH_ENABLED_INSTRUCTION.strip() in ctrl.chat_agent.instruction

        # -WEB に切り替えるとツールとプロンプトが即座に切り替わること
        enabled, changed = ctrl.set_web_search_enabled(False)
        assert (enabled, changed) == (False, True)
        assert ctrl.get_snapshot().web_badge_text == "-WEB"
        assert "web_search" not in [f.__name__ for f in ctrl.chat_agent.tools]
        assert APP_WEB_SEARCH_DISABLED_INSTRUCTION.strip() in ctrl.chat_agent.instruction
        disabled_msg = ctrl.tool_provider.execute("web_search", {"query": "test"})
        assert "無効化" in disabled_msg

        # +WEB に戻す
        ctrl.set_web_search_enabled(True)
        assert ctrl.get_snapshot().web_badge_text == "+WEB"

        # 2. NORMAL / EDIT / TRUST すべてで省略なしの全文確認が入ること
        long_safe_query = "最新の公的統計と技術動向の調査: " + ("あ" * 520)
        assert 500 < len(long_safe_query) <= 600

        mock_search_res = SearchResult(
            query=long_safe_query,
            response_text="調査結果の要点です。",
            sources=[
                {
                    "title": "Official Portal",
                    "url": "https://vertexaisearch.cloud.google.com/redirect/xyz",
                },
                {
                    "title": "Python Docs",
                    "url": "https://docs.python.org/3/",
                },
            ],
            created_at="12:00:00",
            succeeded=True,
        )

        with patch.object(
            ctrl.search_agent, "search", return_value=mock_search_res
        ) as mock_search:
            for mode in (
                APPROVAL_MODE_NORMAL,
                APPROVAL_MODE_EDIT,
                APPROVAL_MODE_TRUST,
            ):
                ctrl.set_approval_mode(mode)
                approvals_seen.clear()
                tool_out = ctrl.tool_provider.execute(
                    "web_search", {"query": long_safe_query}
                )
                assert len(approvals_seen) == 1, f"{mode} モードで承認確認が呼ばれていません"
                # 520文字以上のクエリが '...' 等で途中省略されず全文そのまま含まれていること
                assert long_safe_query in approvals_seen[0]
                assert "【Web検索結果（外部最新情報）】" in tool_out
                assert "Python Docs (docs.python.org)" in tool_out
                assert "vertexaisearch" not in tool_out
                # AIツール経由の検索結果は MaterialStore へ自動蓄積されないこと
                assert len(ctrl.chat_session.materials) == 0
            assert mock_search.call_count == 3

            # 3. 拒否 ('n') 時は [REJECTED] を返し、Project._rejection_pending を汚染しないこと
            ctrl.set_approval_mode(APPROVAL_MODE_TRUST)
            decision_box["val"] = "n"
            rej_out = ctrl.tool_provider.execute(
                "web_search", {"query": "FastAPI 最新バージョン"}
            )
            assert "[REJECTED]" in rej_out
            assert ctrl.project._rejection_pending is False
            # TRUSTモードのファイル書き込みがサーキットブレーカーで誤遮断されないこと
            write_res = ctrl.project.write_file("note.txt", "ok")
            assert "成功" in write_res

            # 4. ローカル物理パスが含まれるクエリは承認モーダル前にプリフライトブロックされること
            approvals_seen.clear()
            decision_box["val"] = "y"
            blocked_out = ctrl.tool_provider.execute(
                "web_search",
                {"query": f"{tmp_path} にある設定ファイルの仕様"},
            )
            assert "[SECURITY_BLOCK]" in blocked_out
            assert len(approvals_seen) == 0


def test_safe_turn_cancellation_preserves_role_integrity_and_kills_subprocess(tmp_path):
    """
    AIツールループ実行中に cancel_active_execution() が呼ばれた際:
    1. project.terminate_active_process() が即座に呼ばれること
    2. 複数 function_call の途中であっても未実行 call にスキップ応答が補完され、
       ロール・ペア整合性を保ったまま aborted=True の ChatTurn として安全に終了すること
    3. Web検索中にキャンセルされた際は aborted=True を返すこと
    """
    cfg = AppConfig(project_path=tmp_path, requested_edit=True)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        fc_content = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(name="get_current_date", args={}),
                types.Part.from_function_call(name="get_current_time", args={}),
            ],
        )
        mock_resp = MagicMock()
        mock_resp.candidates = [MagicMock(content=fc_content)]
        mock_resp.function_calls = [
            fc_content.parts[0].function_call,
            fc_content.parts[1].function_call,
        ]
        mock_resp.text = ""

        orig_execute = ctrl.tool_provider.execute

        def execute_and_cancel(name, args, executor=None):
            res = orig_execute(name, args, executor=executor)
            assert ctrl.cancel_active_execution() is True
            return res

        with patch.object(ctrl.ai_client, "generate_content", return_value=mock_resp) as mock_gen, \
             patch.object(ctrl.project, "terminate_active_process") as mock_term, \
             patch.object(ctrl.tool_provider, "execute", side_effect=execute_and_cancel):
            logs: list[tuple[str, str]] = []
            turn_res = ctrl.run_chat_turn(
                "日時を確認して",
                on_log=lambda msg, category="assistant": logs.append((category, msg)),
            )

            assert turn_res.aborted is True
            assert ctrl.is_busy is False
            mock_term.assert_called_once()
            assert mock_gen.call_count == 1

            saved_turn = ctrl.chat_session.history.last_turn
            assert saved_turn is not None
            assert len(saved_turn.contents) == 5
            assert saved_turn.contents[0].role == "user"
            assert saved_turn.contents[1].role == "model"
            assert saved_turn.contents[2].role == "user"
            assert saved_turn.contents[3].role == "user"
            assert saved_turn.contents[4].role == "model"
            assert any(cat == "abort" for cat, _ in logs)

        cancel_ev = threading.Event()
        cancel_ev.set()
        assert ctrl.search_agent is not None
        search_res = ctrl.search_agent.search("Python", cancel_event=cancel_ev)
        assert search_res.aborted is True
        assert search_res.succeeded is False


def test_compact_context_refreshes_context_tokens_and_keeps_existing_on_failure(tmp_path):
    """
    1. compact_context() で要約・圧縮が成功したとき、count_tokens で圧縮後のコンテキスト量を再計測し、
       last_prompt_tokens および get_snapshot().context_tokens が即座に更新されること
    2. count_tokens が失敗（例外等）した場合は、既存の last_prompt_tokens を維持すること
    3. 圧縮対象がない場合（compacted=False）は count_tokens を呼ばず既存値を維持すること
    """
    from types import SimpleNamespace
    from gemio.core.context import ChatTurn

    def _turn(tid: str, prompt: str, resp: str) -> ChatTurn:
        return ChatTurn(
            turn_id=tid,
            user_prompt=prompt,
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text=prompt)]),
                types.Content(role="model", parts=[types.Part.from_text(text=resp)]),
            ],
            assistant_response=resp,
        )

    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client") as mock_sdk_cls:
        mock_sdk = MagicMock()
        mock_sdk_cls.return_value = mock_sdk
        ctrl = GemioController(cfg)

        # 圧縮前の肥大化状態（60,000 tokens -> warning）を模擬
        ctrl.token_tracker.record(
            SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    prompt_token_count=60_000,
                    candidates_token_count=500,
                    thoughts_token_count=0,
                    cached_content_token_count=0,
                    tool_use_prompt_token_count=0,
                    total_token_count=60_500,
                )
            ),
            source="chat",
        )
        assert ctrl.get_snapshot().context_tokens == 60_000
        assert ctrl.get_snapshot().context_health == "warning"

        # ターン不足で compacted=False のときは count_tokens を呼ばず既存値維持
        ctrl.chat_session.history.append_turn(_turn("t1", "質問1", "回答1"))
        skip_res = ctrl.compact_context(keep_recent_turns=2)
        assert skip_res.compacted is False
        assert mock_sdk.models.count_tokens.call_count == 0
        assert ctrl.get_snapshot().context_tokens == 60_000

        # 3ターンまで増やして圧縮成功 + count_tokens 成功 -> 即座に 3,400 tokens (normal) へ更新
        ctrl.chat_session.history.append_turn(_turn("t2", "質問2", "回答2"))
        ctrl.chat_session.history.append_turn(_turn("t3", "質問3", "回答3"))
        mock_sdk.models.generate_content.return_value = SimpleNamespace(
            text="1. **目的・背景**: 要約テスト",
            usage_metadata=SimpleNamespace(
                prompt_token_count=20_000,
                candidates_token_count=200,
                thoughts_token_count=0,
                cached_content_token_count=0,
                tool_use_prompt_token_count=0,
                total_token_count=20_200,
            ),
        )
        mock_sdk.models.count_tokens.return_value = SimpleNamespace(total_tokens=3_400)

        comp_res = ctrl.compact_context(keep_recent_turns=1)
        assert comp_res.compacted is True
        assert mock_sdk.models.count_tokens.call_count == 1
        snap = ctrl.get_snapshot()
        assert snap.context_tokens == 3_400
        assert snap.context_health == "normal"
        assert ctrl.get_token_usage()["compaction_api_calls"] == 1

        # 2回目の圧縮で count_tokens が失敗した場合は既存の 3,400 を維持すること
        ctrl.token_tracker.set_context_tokens(45_000)
        ctrl.chat_session.history.append_turn(_turn("t4", "質問4", "回答4"))
        mock_sdk.models.count_tokens.side_effect = RuntimeError("count_tokens unavailable")

        comp_res2 = ctrl.compact_context(keep_recent_turns=1)
        assert comp_res2.compacted is True
        assert ctrl.get_snapshot().context_tokens == 45_000
