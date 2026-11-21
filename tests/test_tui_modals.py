from unittest.mock import MagicMock, patch

from google.genai import types
from textual.command import CommandPalette
from textual.widgets import DataTable, Input

from gemio.app import AppConfig, GemioController
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
    DeveloperProject,
)
from gemio.tui import (
    ApiKeyModal,
    ApprovalModal,
    AskUserModal,
    GemioApp,
    HelpModal,
    IterationLimitModal,
    ModelSelectModal,
    SandboxStatusModal,
    TokenUsageModal,
    TrustConsentModal,
)


async def test_production_approval_ask_user_and_iteration_limit_modals(tmp_path):
    """
    デフォルト結線された ApprovalModal, AskUserModal, IterationLimitModal が
    ワーカースレッドからの呼び出しに対して 80x24 画面上で正確に往復動作すること
    """
    # max_iterations=1 に設定し、ツール2回連続時に IterationLimitModal が開くことも合わせて検証
    cfg = AppConfig(project_path=tmp_path, max_iterations=1)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        # Step 1: ask_user ツール呼び出し -> Step 2 (上限到達確認で継続) -> write_file 呼び出し -> 最終回答
        fc_ask = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="ask_user",
                    args={
                        "question": "どちらのファイル名にしますか？",
                        "options": ["alpha.txt", "beta.txt"],
                        "context": "**出力先** の確認",
                    },
                )
            ],
        )
        r_ask = MagicMock(
            candidates=[MagicMock(content=fc_ask)],
            function_calls=[fc_ask.parts[0].function_call],
            text="",
        )

        fc_write = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="write_file",
                    args={"file_path": "beta.txt", "content": "beta content"},
                )
            ],
        )
        r_write = MagicMock(
            candidates=[MagicMock(content=fc_write)],
            function_calls=[fc_write.parts[0].function_call],
            text="",
        )

        r_done = MagicMock(candidates=[], function_calls=None, text="作成完了しました。")

        with patch.object(
            ctrl.ai_client,
            "generate_content",
            side_effect=[r_ask, r_write, r_done],
        ):
            app = GemioApp(ctrl)
            async with app.run_test(size=(80, 24)) as pilot:
                app.composer.editor.insert("ファイルを作って")
                await pilot.press("ctrl+j")

                # 1. AskUserModal が開く -> 数字キー '2' で "beta.txt" を即選択
                for _ in range(40):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, AskUserModal):
                        break
                assert isinstance(app.screen, AskUserModal)
                await pilot.press("2")

                # 2. max_iterations=1 に達し IterationLimitModal が開く -> Enter で追加継続
                for _ in range(40):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, IterationLimitModal):
                        break
                assert isinstance(app.screen, IterationLimitModal)
                await pilot.press("enter")

                # 3. ApprovalModal が開く -> 'y' で承認
                for _ in range(40):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, ApprovalModal):
                        break
                assert isinstance(app.screen, ApprovalModal)
                await pilot.press("y")

                await app.workers.wait_for_complete()
                await pilot.pause()

                assert (tmp_path / "beta.txt").read_text(encoding="utf-8") == "beta content"
                last_turn = ctrl.chat_session.history.last_turn
                assert last_turn is not None
                assert last_turn.aborted is False
                assert last_turn.assistant_response == "作成完了しました。"


async def test_ask_user_custom_input_and_approval_kill_exit_code(tmp_path):
    """
    1. AskUserModal で '0' (その他) を押して自由記述を送信できること
    2. ApprovalModal で 'k' (Kill) を押すと終了コード 1 でクリーン終了すること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        assert ctrl.ai_client is not None

        fc_ask = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="ask_user",
                    args={"question": "方針は？", "options": ["案A"]},
                )
            ],
        )
        r_ask = MagicMock(
            candidates=[MagicMock(content=fc_ask)],
            function_calls=[fc_ask.parts[0].function_call],
            text="",
        )
        fc_write = types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="write_file",
                    args={"file_path": "kill_test.txt", "content": "x"},
                )
            ],
        )
        r_write = MagicMock(
            candidates=[MagicMock(content=fc_write)],
            function_calls=[fc_write.parts[0].function_call],
            text="",
        )

        with patch.object(
            ctrl.ai_client,
            "generate_content",
            side_effect=[r_ask, r_write],
        ):
            app = GemioApp(ctrl)
            async with app.run_test(size=(80, 24)) as pilot:
                app.composer.editor.insert("開始")
                await pilot.press("ctrl+j")

                for _ in range(40):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, AskUserModal):
                        break
                assert isinstance(app.screen, AskUserModal)

                # '0' で自由記述欄へ移動しテキストを送信
                await pilot.press("0")
                await pilot.pause()
                custom_in = app.screen.query_one("#ask-custom-input", Input)
                custom_in.value = "カスタム方針Z"
                await pilot.press("enter")

                # 次の write_file で ApprovalModal が開く -> 'k' で緊急終了
                for _ in range(40):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, ApprovalModal):
                        break
                assert isinstance(app.screen, ApprovalModal)
                await pilot.press("k")
                await pilot.pause(0.05)

            assert app.return_code == 1
            assert not (tmp_path / "kill_test.txt").exists()


async def test_startup_onboarding_chain_api_key_trust_and_sandbox_danger_alert(tmp_path):
    """
    in_container (APIキー未設定) + --trust + --edit 起動時に:
    1. ApiKeyModal が最初に開き、空Enterでは閉じず、キー入力で確定すること
    2. 続けて TrustConsentModal が開き、Escキャンセル時は EDIT モードへフォールバックすること
    3. サンドボックス診断で danger 警告がある場合は自動で SandboxStatusModal が開くこと
    """
    danger_status = {
        "execution_mode": "in_container",
        "container_name": None,
        "workspace_dir": "/app",
        "workspace_rw": True,
        "user": {"user_str": "uid=1000(sandbox)", "is_root": False},
        "network": {"mode": "none", "is_isolated": True},
        "resources": {
            "readonly_rootfs": True,
            "memory_limit": "2G",
            "cpus": "2.0",
            "pids_limit": "128",
        },
        "sensitive_env_keys": ["GITHUB_TOKEN"],
        "entries": [
            {
                "name": "src/",
                "perms": "drwxr-xr-x",
                "uid": 1000,
                "gid": 1000,
                "size": 4096,
                "mount_source": "workspace (rw)",
                "mount_mode": "rw",
                "risk_level": "normal",
                "status_label": "作業対象 (rw)",
            }
        ],
        "warnings": [
            {
                "level": "danger",
                "path": "コンテナ環境変数 (GITHUB_TOKEN)",
                "message": "機密トークンが設定されています。",
                "recommendation": "解除してください。",
            }
        ],
    }

    cfg = AppConfig(
        project_path=tmp_path,
        dev_mode=True,
        requested_edit=True,
        requested_trust=True,
    )
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"), \
         patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True), \
         patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        with patch.object(
            ctrl.project,
            "inspect_sandbox_status",
            return_value=danger_status,
        ):
            app = GemioApp(ctrl)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                # Step 1: ApiKeyModal
                assert isinstance(app.screen, ApiKeyModal)
                await pilot.press("enter")
                await pilot.pause()
                # 空入力では閉じないこと
                assert isinstance(app.screen, ApiKeyModal)

                key_input = app.screen.query_one("#api-key-input", Input)
                key_input.value = "DUMMY_ENTERED_API_KEY"
                await pilot.press("enter")
                await pilot.pause()
                assert ctrl.is_client_ready is True

                # Step 2: TrustConsentModal -> Esc で拒否 -> EDIT にフォールバック
                assert isinstance(app.screen, TrustConsentModal)
                await pilot.press("escape")
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert ctrl.project.approval_mode == APPROVAL_MODE_EDIT

                # Step 3: danger 警告により SandboxStatusModal が自動で開いていること
                for _ in range(30):
                    await pilot.pause(0.02)
                    if isinstance(app.screen, SandboxStatusModal):
                        break
                assert isinstance(app.screen, SandboxStatusModal)
                table = app.screen.query_one("#sandbox-table", DataTable)
                assert table.row_count == 1
                await pilot.press("escape")
                await pilot.pause()
                assert not isinstance(app.screen, SandboxStatusModal)


async def test_help_model_tokens_modals_and_command_palette(tmp_path):
    """
    F1 (HelpModal), F2 (ModelSelectModal), /tokens (TokenUsageModal からのクリア実行),
    /trust on (TrustConsentModal 同意), および Ctrl+P (CommandPalette) が正常に動作すること
    """
    cfg = AppConfig(project_path=tmp_path)
    with patch("gemio.core.client.genai.Client"):
        ctrl = GemioController(cfg)
        ctrl.chat_session.materials.add_search_result("q", "content", [])
        app = GemioApp(ctrl)

        async with app.run_test(size=(80, 24)) as pilot:
            # 1. F1 -> HelpModal 開閉（閉じるボタンがダイアログ内に収まりクリックで閉じられること）
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, HelpModal)
            dlg = app.screen.query_one(".modal-dialog-large")
            close_btn = app.screen.query_one("#btn-close")
            assert dlg.region.contains_region(close_btn.region)
            await pilot.click("#btn-close")
            await pilot.pause()
            assert not isinstance(app.screen, HelpModal)

            # 2. F2 -> ModelSelectModal -> '2' キーで gemini-4-argon を選択
            await pilot.press("f2")
            await pilot.pause()
            assert isinstance(app.screen, ModelSelectModal)
            await pilot.press("2")
            await pilot.pause()
            assert ctrl.current_model == "gemini-4-argon"

            # 3. /tokens -> TokenUsageModal -> [資料含め全クリア] ボタン押下
            app.composer.editor.insert("/tokens")
            await pilot.press("ctrl+j")
            await pilot.pause()
            assert isinstance(app.screen, TokenUsageModal)
            await pilot.click("#btn-clear-all")
            await pilot.pause()
            assert len(ctrl.chat_session.materials) == 0

            # 4. /trust on -> TrustConsentModal -> [同意して有効化] ボタン押下
            app.composer.editor.insert("/trust on")
            await pilot.press("ctrl+j")
            await pilot.pause()
            assert isinstance(app.screen, TrustConsentModal)
            await pilot.click("#btn-consent")
            await pilot.pause()
            assert ctrl.project.approval_mode == APPROVAL_MODE_TRUST

            # 5. Ctrl+P -> CommandPalette が起動すること
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert isinstance(app.screen, CommandPalette)
            await pilot.press("escape")
            await pilot.pause()


async def test_modals_fit_content_height_and_keep_buttons_visible(tmp_path):
    """
    大画面 (100x48) ではダイアログが画面全体に無駄に引き伸ばされず表示内容の高さに収まり、
    最小画面 (80x24) でもすべてのダイアログで下部ボタン行が領域内に収まること
    """
    dev_small = {
        "is_dev_mode": True,
        "execution_mode": "in_container",
        "workspace_dir": "/app",
        "workspace_rw": True,
        "user": {"user_str": "uid=1000", "is_root": False},
        "network": {"mode": "none", "is_isolated": True},
        "resources": {
            "readonly_rootfs": True,
            "memory_limit": "2G",
            "cpus": "2",
            "pids_limit": "128",
        },
        "sensitive_env_keys": [],
        "entries": [
            {
                "name": "a.py",
                "perms": "-rw-r--r--",
                "uid": 1000,
                "gid": 1000,
                "size": 10,
                "mount_source": "ws",
                "mount_mode": "rw",
                "risk_level": "normal",
                "status_label": "ok",
            }
        ],
        "warnings": [],
    }

    for size in ((80, 24), (100, 48)):
        cfg = AppConfig(project_path=tmp_path)
        with patch("gemio.core.client.genai.Client"):
            ctrl = GemioController(cfg)
            app = GemioApp(ctrl)
            async with app.run_test(size=size) as pilot:
                modals = [
                    ApprovalModal("write_file: a.py"),
                    AskUserModal("続行しますか？", ["はい", "いいえ"]),
                    IterationLimitModal(),
                    TrustConsentModal(),
                    ApiKeyModal(),
                    ModelSelectModal("gemini-flash-latest"),
                    SandboxStatusModal(ctrl.inspect_sandbox()),
                    SandboxStatusModal(dev_small),
                    TokenUsageModal(ctrl.get_token_usage()),
                    HelpModal(),
                ]
                for modal in modals:
                    app.push_screen(modal)
                    await pilot.pause()
                    dlg = modal.query_one("Vertical")
                    btns = modal.query_one(".modal-buttons")
                    assert dlg.region.contains_region(btns.region)
                    if size == (100, 48) and not isinstance(modal, HelpModal):
                        # 内容が少ないダイアログが大画面一杯(92%=44行)に引き伸ばされていないこと
                        assert dlg.region.height <= 26
                    modal.dismiss(None)
                    await pilot.pause()
