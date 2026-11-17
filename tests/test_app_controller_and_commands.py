import ast
from argparse import Namespace
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

from google.genai import types
import pytest

from gemio.app import (
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
    """AppConfig のディレクトリ検証（AppStartupError）および初期承認モード解決が正確に動作すること"""
    # 1. 存在しないディレクトリ・ファイル指定時は AppStartupError となること
    with pytest.raises(AppStartupError):
        validate_project_dir(tmp_path / "missing_dir")

    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("hello", encoding="utf-8")
    with pytest.raises(AppStartupError):
        validate_project_dir(dummy_file)

    # 2. Namespace からの構築とモデルエイリアス解決
    ns = Namespace(
        project_dir=str(tmp_path),
        model="argon",
        dev=False,
        container=None,
        edit=True,
        trust=True,
        dynamic_tools=True,
    )
    cfg = AppConfig.from_namespace(ns)
    assert cfg.project_path == tmp_path.resolve()
    assert cfg.resolved_initial_model == "gemini-4-argon"
    assert cfg.dynamic_tools is True

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
    assert parse_slash_command("/s -n") == SearchCommand(show_help=True)
    assert parse_slash_command("/search --clear") == SearchCommand(clear_materials=True)
    assert parse_slash_command("/s -c") == SearchCommand(clear_materials=True)
    assert parse_slash_command("/search Python 3.15") == SearchCommand(
        query="Python 3.15", new_session=False
    )
    assert parse_slash_command("/s --new Textual DataTable") == SearchCommand(
        query="Textual DataTable", new_session=True
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
        # 同意前は安全側の EDIT モードであること
        assert ctrl.project.approval_mode == APPROVAL_MODE_EDIT

        # APIキー未設定での対話実行はエラーになるが、スナップショット取得は安全に行えること
        snap = ctrl.get_snapshot()
        assert snap.mode_badge_text == "DEV:in-container"
        assert snap.approval_badge_text == "EDIT"
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


def test_controller_state_operations_search_outcomes_and_context_actions(tmp_path):
    """
    GemioController の状態変更、InteractionHandler 連携、SearchOutcome の4状態判定、
    巻き戻し・要約・クリア（動的ツール＆スキル連動）が正確に動作すること
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

        # 1. InteractionHandler 委譲
        assert ctrl.on_approval("write a.py") == "y"
        assert ctrl.on_ask_user("質問", ["選択肢1"], "背景") == "選択肢1"
        assert ctrl.on_iteration_limit() == (True, 5)

        # 2. 承認モード・モデル変更・コンテキスト健全性判定
        enabled, changed = ctrl.set_edit_mode()
        assert (enabled, changed) == (True, True)
        assert ctrl.set_edit_mode(True) == (True, False)

        assert ctrl.set_model("3.8") == "gemini-3.8-flash"
        assert ctrl.ai_client.model_name == "gemini-3.8-flash"

        ctrl.token_tracker._usage["last_prompt_tokens"] = 60_000
        assert ctrl.get_snapshot().context_health == "warning"
        ctrl.token_tracker._usage["last_prompt_tokens"] = 160_000
        assert ctrl.get_snapshot().context_health == "danger"

        # 3. SearchOutcome の4状態（added / duplicate / failed / aborted）判定
        assert ctrl.search_agent is not None
        ok_res = SearchResult(
            query="Textual 8",
            response_text="Textual 8 の新機能解説",
            sources=[{"title": "Textual Docs", "url": "https://textual.textualize.io"}],
            created_at="10:00:00",
            succeeded=True,
        )
        with patch.object(ctrl.search_agent, "search", return_value=ok_res):
            out_added = ctrl.run_search("Textual 8")
            assert out_added.material_status == "added"
            assert out_added.total_materials == 1

            out_dup = ctrl.run_search("Textual 8")
            assert out_dup.material_status == "duplicate"
            assert out_dup.total_materials == 1

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

        # 4. スキル連動と clear_session による一括リセット
        ctrl.skill_manager.activate("sample-skill")
        assert ctrl.get_snapshot().active_skills == ("sample-skill",)
        ctrl.clear_session(clear_materials=True)
        assert ctrl.get_snapshot().active_skills == ()


def test_safe_turn_cancellation_preserves_role_integrity_and_kills_subprocess(tmp_path):
    """
    AIツールループ実行中に cancel_active_execution() が呼ばれた際:
    1. project.terminate_active_process() が即座に呼ばれること
    2. 複数 function_call の途中であっても未実行 call にスキップ応答が補完され、
       ロール・ペア整合性を保ったまま aborted=True の ChatTurn として安全に終了すること
    3. Web検索中にキャンセルされた際は search_history がロールバックされること
    """
    cfg = AppConfig(project_path=tmp_path, requested_edit=True)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        # モデルが2つのツール呼び出し (get_current_date, get_current_time) を同時に要求したレスポンスを模擬
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
            # 1つ目のツール完了直後にユーザーが Esc (ターン中断) を押した状況を再現
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
            # 1つ目のツール完了後にキャンセル検知したため、2回目の API リクエストは送信されないこと
            assert mock_gen.call_count == 1

            # 確定されたターンの contents 構造検証:
            # [user_prompt, model(2 calls), user(resp 1), user(skipped resp 2), model(abort_text)]
            saved_turn = ctrl.chat_session.history.last_turn
            assert saved_turn is not None
            assert len(saved_turn.contents) == 5
            assert saved_turn.contents[0].role == "user"
            assert saved_turn.contents[1].role == "model"
            assert saved_turn.contents[2].role == "user"
            assert saved_turn.contents[3].role == "user"
            assert saved_turn.contents[4].role == "model"
            assert any(cat == "abort" for cat, _ in logs)

        # Web検索中のキャンセル時に search_history が汚されないこと
        cancel_ev = threading.Event()
        cancel_ev.set()
        assert ctrl.search_agent is not None
        search_res = ctrl.search_agent.search("Python", cancel_event=cancel_ev)
        assert search_res.aborted is True
        assert search_res.succeeded is False
        assert ctrl.search_agent.has_active_session is False
