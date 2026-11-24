import ast
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.genai import types
import pytest

from gemio.app import (
    APP_MATERIALS_GUIDANCE_INSTRUCTION,
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
    MaterialsCommand,
    ModelCommand,
    RewindCommand,
    SandboxCommand,
    TokensCommand,
    TrustModeCommand,
    parse_slash_command,
    validate_project_dir,
)
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
    """AppConfig のディレクトリ検証（AppStartupError）・初期承認モード解決が正確に動作すること"""
    with pytest.raises(AppStartupError):
        validate_project_dir(tmp_path / "missing_dir")

    dummy_file = tmp_path / "file.txt"
    dummy_file.write_text("hello", encoding="utf-8")
    with pytest.raises(AppStartupError):
        validate_project_dir(dummy_file)

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
    """スラッシュコマンドパーサが全コマンド（/materials 含む）・エイリアス・通常入力を正確に構造化すること"""
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

    # /materials [clear | rm <id>]
    assert parse_slash_command("/materials") == MaterialsCommand(action="open")
    assert parse_slash_command("/mat") == MaterialsCommand(action="open")
    assert parse_slash_command("/materials clear") == MaterialsCommand(action="clear")
    assert parse_slash_command("/mat --clear") == MaterialsCommand(action="clear")
    assert parse_slash_command("/materials rm m1") == MaterialsCommand(
        action="remove", target="m1"
    )
    assert parse_slash_command("/mat del 2") == MaterialsCommand(
        action="remove", target="2"
    )

    assert parse_slash_command("/copy") == CopyCommand()
    assert parse_slash_command("/compact") == CompactCommand(keep_recent_turns=2)
    assert parse_slash_command("/compact 4") == CompactCommand(keep_recent_turns=4)
    assert parse_slash_command("/rewind") == RewindCommand(steps=1)
    assert parse_slash_command("/undo 3") == RewindCommand(steps=3)

    # 旧 /search および /web は廃止され通常プロンプト扱いになること
    assert parse_slash_command("/search Python 3.15") == ChatPromptInput(
        text="/search Python 3.15"
    )
    assert parse_slash_command("/web on") == ChatPromptInput(
        text="/web on"
    )
    assert parse_slash_command("/etc/hosts の設定形式を教えて") == ChatPromptInput(
        text="/etc/hosts の設定形式を教えて"
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
        assert ctrl.project.approval_mode == APPROVAL_MODE_EDIT

        snap = ctrl.get_snapshot()
        assert snap.mode_badge_text == "DEV:in-container"
        assert snap.approval_badge_text == "EDIT"
        with pytest.raises(RuntimeError):
            ctrl.run_chat_turn("テスト")

        with patch("gemio.core.client.genai.Client") as mock_genai:
            ctrl.set_api_key("DUMMY_CONTAINER_KEY")
            assert ctrl.needs_api_key_input is False
            assert ctrl.is_client_ready is True
            mock_genai.assert_called_once_with(api_key="DUMMY_CONTAINER_KEY")

        mode, fb = ctrl.apply_initial_trust_consent(True)
        assert mode == APPROVAL_MODE_TRUST
        assert fb is False
        assert ctrl.needs_initial_trust_consent is False
        assert ctrl.get_snapshot().approval_badge_text == "TRUST"


def test_controller_materials_management_and_secret_blocking(tmp_path):
    """
    GemioController の資料管理API（list_materials, add_user_material, remove_material, clear_materials）と
    機密混入時の登録ブロック、およびセッションリセット連携が正しく動作すること
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
        assert ctrl.set_model("3.8") == "gemini-3.8-flash"

        # 機密情報を含む手動追加はブロックされること
        fake_key = "AI" + "za" + ("X" * 28)
        with pytest.raises(ValueError):
            ctrl.add_user_material("不正資料", f"api_key={fake_key}")

        # 正常な資料の追加・重複判定・個別削除・全クリア
        m1, st1 = ctrl.add_user_material("Textual仕様", "Textual 8 の最新 API")
        assert st1 == "added"
        assert len(ctrl.list_materials()) == 1
        _, st_dup = ctrl.add_user_material("別名", "Textual 8 の最新 API")
        assert st_dup == "duplicate"

        m2, st2 = ctrl.add_user_material("PEP 735", "Dependency groups spec")
        assert st2 == "added"
        assert len(ctrl.list_materials()) == 2

        removed = ctrl.remove_material(m1.material_id)
        assert removed is not None and removed.material_id == m1.material_id
        assert len(ctrl.list_materials()) == 1

        assert ctrl.clear_materials() == 1
        assert len(ctrl.list_materials()) == 0

        ctrl.skill_manager.activate("sample-skill")
        assert ctrl.get_snapshot().active_skills == ("sample-skill",)
        ctrl.clear_session(clear_materials=True)
        assert ctrl.get_snapshot().active_skills == ()


def test_controller_has_no_web_search_tools_and_includes_materials_guidance(tmp_path):
    """
    GemioController に Web検索・外部検索支援ツールが一切登録されておらず、
    システムプロンプトに作業前提資料（/materials）案内が含まれていること
    """
    cfg = AppConfig(project_path=tmp_path, dynamic_tools=True)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.chat_agent is not None
        assert ctrl.tool_registry.get_toolset("web_search") is None
        tool_names = [f.__name__ for f in ctrl.chat_agent.tools]
        assert "request_user_search" not in tool_names
        assert "web_search" not in tool_names
        assert APP_MATERIALS_GUIDANCE_INSTRUCTION.strip() in ctrl.chat_agent.instruction


def test_safe_turn_cancellation_preserves_role_integrity_and_kills_subprocess(tmp_path):
    """
    AIツールループ実行中に cancel_active_execution() が呼ばれた際:
    1. project.terminate_active_process() が即座に呼ばれること
    2. 複数 function_call の途中であっても未実行 call にスキップ応答が補完され、
       ロール・ペア整合性を保ったまま aborted=True の ChatTurn として安全に終了すること
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

        ctrl.chat_session.history.append_turn(_turn("t1", "質問1", "回答1"))
        skip_res = ctrl.compact_context(keep_recent_turns=2)
        assert skip_res.compacted is False
        assert mock_sdk.models.count_tokens.call_count == 0
        assert ctrl.get_snapshot().context_tokens == 60_000

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

        ctrl.token_tracker.set_context_tokens(45_000)
        ctrl.chat_session.history.append_turn(_turn("t4", "質問4", "回答4"))
        mock_sdk.models.count_tokens.side_effect = RuntimeError("count_tokens unavailable")

        comp_res2 = ctrl.compact_context(keep_recent_turns=1)
        assert comp_res2.compacted is True
        assert ctrl.get_snapshot().context_tokens == 45_000
