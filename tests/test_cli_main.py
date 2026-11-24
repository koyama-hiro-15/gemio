import ast
import io
from pathlib import Path
import tomllib
from unittest.mock import MagicMock, patch

import pytest

from gemio.app import APP_MATERIALS_GUIDANCE_INSTRUCTION
from gemio.cli import (
    check_tty_available,
    create_controller_from_args,
    main,
    parse_args,
    run_cli,
)
from gemio.project import DeveloperProject, SandboxSecurityError


class _FakeTTYStream(io.StringIO):
    def __init__(self, is_tty: bool = True) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_no_prompt_toolkit_dependency_in_codebase_and_top_level_gemio_is_ui_free():
    """
    1. src/gemio/ 配下の全モジュールおよび pyproject.toml から prompt_toolkit 依存が完全撤去されていること
    2. src/gemio/__init__.py が textual をインポートせず軽量ライブラリファサードを維持していること
    """
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_data = tomllib.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    deps = pyproject_data["project"]["dependencies"]
    assert "textual" in deps
    assert "prompt_toolkit" not in deps

    src_dir = repo_root / "src" / "gemio"
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        assert "prompt_toolkit" not in imported_roots, (
            f"{py_file} に旧 prompt_toolkit の import が残っています"
        )

    top_init_tree = ast.parse(
        (src_dir / "__init__.py").read_text(encoding="utf-8")
    )
    top_imports = {
        node.module.split(".")[1]
        for node in ast.walk(top_init_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("gemio.")
    }
    assert "tui" not in top_imports
    assert "cli" not in top_imports


def test_parse_args_all_options(tmp_path):
    """コマンドライン引数の解析（デフォルト値、--model、--dev、--container、--edit、--trust/--auto、--dynamic-tools）が正確であること"""
    default_args = parse_args([])
    assert default_args.project_dir == "."
    assert default_args.dev is False
    assert default_args.edit is False
    assert default_args.trust is False
    assert default_args.dynamic_tools is False

    custom_args = parse_args(
        [
            str(tmp_path),
            "-m",
            "argon",
            "--dev",
            "--container",
            "my-box",
            "--edit",
            "--auto",
            "--dynamic-tools",
        ]
    )
    assert custom_args.project_dir == str(tmp_path)
    assert custom_args.model == "argon"
    assert custom_args.dev is True
    assert custom_args.container == "my-box"
    assert custom_args.edit is True
    assert custom_args.trust is True
    assert custom_args.dynamic_tools is True


def test_cli_preflight_rejects_non_tty_invalid_dir_security_error_and_missing_api_key(
    tmp_path,
    capsys,
):
    """
    プリフライト検証:
    1. 非TTY環境ではエラー案内を stderr に出して終了コード 1 を返すこと
    2. 存在しないディレクトリ指定時は終了コード 1 を返すこと
    3. SandboxSecurityError 発生時は終了コード 1 を返すこと
    4. APIキー未設定による ValueError 時は親切な案内を stderr に出して終了コード 1 を返すこと
    """
    # 1. 非TTY
    with patch("sys.stdin", _FakeTTYStream(False)), \
         patch("sys.stdout", _FakeTTYStream(True)):
        assert run_cli([str(tmp_path)]) == 1
        err_out = capsys.readouterr().err
        assert "TTY" in err_out

    # 2. 存在しないディレクトリ
    with patch("sys.stdin", _FakeTTYStream(True)), \
         patch("sys.stdout", _FakeTTYStream(True)):
        assert run_cli([str(tmp_path / "no_such_dir")]) == 1
        err_out = capsys.readouterr().err
        assert "ディレクトリが存在しません" in err_out

    # 3. SandboxSecurityError
    with patch("sys.stdin", _FakeTTYStream(True)), \
         patch("sys.stdout", _FakeTTYStream(True)), \
         patch.object(
             DeveloperProject,
             "_is_running_in_container",
             side_effect=SandboxSecurityError("docker.sock detected"),
         ):
        assert run_cli([str(tmp_path), "--dev"]) == 1
        err_out = capsys.readouterr().err
        assert "docker.sock detected" in err_out

    # 4. ホスト環境での APIキー未設定エラー
    with patch("sys.stdin", _FakeTTYStream(True)), \
         patch("sys.stdout", _FakeTTYStream(True)), \
         patch(
             "gemio.core.client.genai.Client",
             side_effect=ValueError("Missing key inputs argument!"),
         ):
        assert run_cli([str(tmp_path)]) == 1
        err_out = capsys.readouterr().err
        assert "GEMINI_API_KEY" in err_out


def test_cli_runs_gemio_app_propagates_exit_code_and_closes_controller(tmp_path):
    """
    TTY環境で GemioApp が起動され、終了コード伝播および controller.close() の確実な実行が行われること
    """
    mock_app = MagicMock()
    mock_app.run.return_value = 0
    mock_app.return_code = 0

    with patch("sys.stdin", _FakeTTYStream(True)), \
         patch("sys.stdout", _FakeTTYStream(True)), \
         patch("gemio.core.client.genai.Client"), \
         patch("gemio.tui.GemioApp", return_value=mock_app) as mock_app_cls:
        code = run_cli([str(tmp_path), "-m", "3.8", "--edit"])
        assert code == 0
        mock_app_cls.assert_called_once()
        injected_ctrl = mock_app_cls.call_args.args[0]
        assert injected_ctrl.current_model == "gemini-3.8-flash"
        assert injected_ctrl.project.edit_mode is True

    # 異常終了コード (1) 時は main() が SystemExit(1) を送出すること
    mock_app_fail = MagicMock()
    mock_app_fail.run.return_value = 1
    mock_app_fail.return_code = 1

    with patch("sys.stdin", _FakeTTYStream(True)), \
         patch("sys.stdout", _FakeTTYStream(True)), \
         patch("gemio.core.client.genai.Client"), \
         patch("gemio.tui.GemioApp", return_value=mock_app_fail):
        with pytest.raises(SystemExit) as exc_info:
            main([str(tmp_path)])
        assert exc_info.value.code == 1


def test_controller_from_args_composes_three_layer_system_instructions(tmp_path):
    """
    create_controller_from_args が core 基本指示・Project/DeveloperProject 指示・
    アプリ層の作業前提資料案内指示を適切に合成すること
    """
    with patch("gemio.core.client.genai.Client"):
        ctrl_std = create_controller_from_args(parse_args([str(tmp_path)]))
        assert ctrl_std.chat_agent is not None
        std_inst = ctrl_std.chat_agent.instruction
        assert "【外部情報および不確実な事実に関するルール】" in std_inst
        assert "【ユーザーへの確認・質問（ask_userツール）に関するルール】" in std_inst
        assert APP_MATERIALS_GUIDANCE_INSTRUCTION.strip() in std_inst
        assert "run_sandbox_command" not in std_inst
        ctrl_std.close()

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"), \
         patch.dict("os.environ", {"GEMINI_API_KEY": "DUMMY_ENV_KEY"}, clear=True), \
         patch("gemio.core.client.genai.Client"):
        ctrl_dev = create_controller_from_args(
            parse_args([str(tmp_path), "--dev"])
        )
        assert ctrl_dev.chat_agent is not None
        dev_inst = ctrl_dev.chat_agent.instruction
        assert "run_sandbox_command" in dev_inst
        assert APP_MATERIALS_GUIDANCE_INSTRUCTION.strip() in dev_inst
        ctrl_dev.close()
