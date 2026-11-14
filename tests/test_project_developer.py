import json
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from gemio.project.base import AgentExecutionAborted
from gemio.project.developer import (
    DeveloperProject,
    SandboxSecurityError,
    classify_workspace_entry,
    is_sensitive_env_key,
    parse_proc_mountinfo,
    scrub_environment,
    truncate_command_output,
)


def test_scrub_environment_removes_secrets():
    raw_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/vscode",
        "GEMINI_API_KEY": "DUMMY_GEMINI_SECRET_KEY",
        "GOOGLE_API_KEY": "GoogleSecret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json",
        "OPENAI_API_KEY": "DUMMY_OPENAI_KEY_12345",
        "NPM_TOKEN": "npm_secret",
        "CARGO_REGISTRY_TOKEN": "cargo_secret",
        "COMPOSER_AUTH": "composer_secret",
        "MY_CUSTOM_TOKEN": "tok-abc",
        "DB_PASSWORD": "pass",
        "LANG": "C.UTF-8",
    }
    cleaned = scrub_environment(raw_env)
    assert "PATH" in cleaned
    assert "HOME" in cleaned
    assert "LANG" in cleaned
    assert "GEMINI_API_KEY" not in cleaned
    assert "GOOGLE_API_KEY" not in cleaned
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in cleaned
    assert "OPENAI_API_KEY" not in cleaned
    assert "NPM_TOKEN" not in cleaned
    assert "CARGO_REGISTRY_TOKEN" not in cleaned
    assert "COMPOSER_AUTH" not in cleaned
    assert "MY_CUSTOM_TOKEN" not in cleaned
    assert "DB_PASSWORD" not in cleaned


def test_is_running_in_container_strict_check():
    """環境変数だけではコンテナ内と誤認せず、Linuxかつ実体ファイルがある場合のみTrueとなること"""
    # Windows や macOS では、仮に環境変数やファイルがあっても必ず False
    with patch("gemio.project.developer.sys.platform", "win32"), \
         patch.dict("os.environ", {"DEVCONTAINER": "true", "REMOTE_CONTAINERS": "true"}), \
         patch("gemio.project.developer.Path.exists", return_value=True):
        assert DeveloperProject._is_running_in_container() is False

    # Linux ホストであっても、/.dockerenv や /run/.containerenv が無ければ環境変数があっても False
    with patch("gemio.project.developer.sys.platform", "linux"), \
         patch.dict("os.environ", {"DEVCONTAINER": "true", "REMOTE_CONTAINERS": "true"}), \
         patch("gemio.project.developer.Path.exists", return_value=False):
        assert DeveloperProject._is_running_in_container() is False

    # Linux かつ /.dockerenv が存在する場合は True
    with patch("gemio.project.developer.sys.platform", "linux"), \
         patch("gemio.project.developer.Path.exists", return_value=True):
        assert DeveloperProject._is_running_in_container() is True


def test_truncate_command_output():
    short_out = "line 1\nline 2\nline 3"
    assert truncate_command_output(short_out, max_chars=500, max_lines=20) == short_out

    many_lines = "\n".join([f"log line {i}" for i in range(100)])
    truncated = truncate_command_output(many_lines, max_chars=5000, max_lines=10)
    assert "log line 0" in truncated
    assert "log line 99" in truncated
    assert "中略" in truncated
    assert "log line 50" not in truncated


def test_in_container_initialization_and_command_execution(tmp_path):
    """コンテナ内起動（In-Container）と判定され安全検査を通過した場合の動作テスト"""
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)
        assert dev_proj.execution_mode == "in_container"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"4 passed in 0.12s\n", b"")
        mock_proc.returncode = 0

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch.dict("os.environ", {"GEMINI_API_KEY": "secret-key-123", "PATH": "/usr/bin"}):
            res = dev_proj.run_sandbox_command("pytest -v")
            assert "成功 (終了コード: 0)" in res
            assert "4 passed in 0.12s" in res

            # 子プロセスに渡された環境変数から GEMINI_API_KEY がスクラブされていること
            called_kwargs = mock_popen.call_args.kwargs
            assert "GEMINI_API_KEY" not in called_kwargs["env"]


def test_in_container_rejects_docker_sock(tmp_path):
    """コンテナ内に /var/run/docker.sock が存在する場合は SandboxSecurityError で拒否されること"""
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch("gemio.project.developer.Path.exists", return_value=True):
        with pytest.raises(SandboxSecurityError) as exc_info:
            DeveloperProject(str(tmp_path), require_approval=False)
        assert "docker.sock" in str(exc_info.value)


def test_in_container_allows_startup_and_warns_when_api_keys_in_environ(tmp_path):
    """
    コンテナ内起動時に環境変数へ各種APIキーが設定されている場合、
    起動は拒否せず、inspect_sandbox_status で値を保持せずにキー名のみを検出し danger 警告を出すこと
    """
    assert is_sensitive_env_key("GEMINI_API_KEY") is True
    assert is_sensitive_env_key("CUSTOM_SERVICE_TOKEN") is True
    assert is_sensitive_env_key("PATH") is False

    secret_val = "DUMMY_SUPER_SECRET_VALUE_DO_NOT_LEAK"
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch("gemio.project.developer.Path.exists", return_value=False), \
         patch.dict(
             "os.environ",
             {
                 "GEMINI_API_KEY": secret_val,
                 "OPENAI_API_KEY": secret_val,
                 "EMPTY_API_KEY": "   ",
                 "PATH": "/usr/bin",
             },
             clear=True,
         ):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)
        assert dev_proj.execution_mode == "in_container"

        status = dev_proj.inspect_sandbox_status()
        assert status["sensitive_env_keys"] == ["GEMINI_API_KEY", "OPENAI_API_KEY"]

        # 強い警告 (danger) が含まれ、かつシークレットの値そのものは一切含まれないこと
        danger_warns = [w for w in status["warnings"] if w["level"] == "danger"]
        assert len(danger_warns) == 1
        assert "GEMINI_API_KEY" in danger_warns[0]["path"]
        assert "OPENAI_API_KEY" in danger_warns[0]["path"]
        assert secret_val not in json.dumps(status, ensure_ascii=False)


def test_host_docker_detects_sensitive_env_keys_and_masks_values(tmp_path):
    """
    host_docker モードにおいて、接続先コンテナの環境変数に各種APIキーが設定されている場合、
    値をマスクしてメモリに残さず、キー名のみを検出して強い警告 (danger) を表示すること
    """
    secret_val_1 = "DUMMY_GEMINI_SECRET_VAL_999"
    secret_val_2 = "DUMMY_GITHUB_TOKEN_VAL_888"
    inspect_data = [
        {
            "Name": "/gemio-sandbox-py",
            "State": {"Running": True},
            "HostConfig": {"Privileged": False, "NetworkMode": "none"},
            "Mounts": [
                {"Type": "bind", "Source": str(tmp_path), "Destination": "/app", "RW": True},
            ],
            "Config": {
                "WorkingDir": "/app",
                "User": "1000:1000",
                "Env": [
                    "PATH=/usr/local/bin:/usr/bin",
                    f"GEMINI_API_KEY={secret_val_1}",
                    "EMPTY_SECRET_KEY=",
                ],
            },
        }
    ]
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = json.dumps(inspect_data)

    probe_stdout = (
        "uid=1000(sandbox) gid=1000(sandbox)\n"
        "===GEMIO_MOUNTINFO===\n"
        "40 35 0:38 / /app rw,relatime - virtiofs workspace rw\n"
        "===GEMIO_STAT===\n"
        "drwxr-xr-x|1000|1000|4096|0:0|directory|src\n"
        "===GEMIO_ENVKEYS===\n"
        "PATH\n"
        "GEMINI_API_KEY\n"
        "GITHUB_TOKEN\n"
    )
    mock_probe = MagicMock()
    mock_probe.returncode = 0
    mock_probe.stdout = probe_stdout

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", side_effect=[mock_inspect, mock_inspect, mock_probe]):
        dev_proj = DeveloperProject(
            str(tmp_path),
            require_approval=False,
            container_name="gemio-sandbox-py",
        )
        # _last_inspect_info 内の環境変数値が即座にマスクされ、平文値が保持されていないこと
        assert secret_val_1 not in json.dumps(dev_proj._last_inspect_info, ensure_ascii=False)
        assert "GEMINI_API_KEY=<redacted>" in dev_proj._last_inspect_info["Config"]["Env"]

        status = dev_proj.inspect_sandbox_status()
        assert status["sensitive_env_keys"] == ["GEMINI_API_KEY", "GITHUB_TOKEN"]

        danger_warns = [w for w in status["warnings"] if w["level"] == "danger"]
        assert len(danger_warns) == 1
        assert "コンテナ環境変数 (GEMINI_API_KEY, GITHUB_TOKEN)" == danger_warns[0]["path"]
        assert secret_val_1 not in json.dumps(status, ensure_ascii=False)
        assert secret_val_2 not in json.dumps(status, ensure_ascii=False)


def test_in_container_security_passes_when_clean(tmp_path):
    """コンテナ内起動時に docker.sock・特権モード・環境変数APIキーのいずれも存在しない場合は正常に初期化されること"""
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch("gemio.project.developer.Path.exists", return_value=False), \
         patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)
        assert dev_proj.execution_mode == "in_container"


def test_host_os_rejects_when_no_container_available(tmp_path):
    """ホストOS上で稼働中のコンテナが見つからない場合、ホスト直接実行せず拒否されること"""
    mock_ps_result = MagicMock()
    mock_ps_result.returncode = 0
    mock_ps_result.stdout = ""
    mock_ps_result.stderr = ""

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", return_value=mock_ps_result):
        with pytest.raises(SandboxSecurityError) as exc_info:
            DeveloperProject(str(tmp_path), require_approval=False)
        assert "ホストOS上で直接シェルコマンドを実行することは禁止" in str(exc_info.value)


def test_host_os_rejects_privileged_container(tmp_path):
    """ホストOS起動時、対象のDockerコンテナが --privileged の場合は拒否されること"""
    inspect_data = [
        {
            "Name": "/unsafe-container",
            "State": {"Running": True},
            "HostConfig": {"Privileged": True, "NetworkMode": "bridge"},
            "Mounts": [],
            "Config": {"WorkingDir": "/workspaces/app"},
        }
    ]
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = json.dumps(inspect_data)

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", return_value=mock_inspect):
        with pytest.raises(SandboxSecurityError) as exc_info:
            DeveloperProject(str(tmp_path), require_approval=False, container_name="unsafe-container")
        assert "特権モード" in str(exc_info.value)


def test_host_os_rejects_host_network_container(tmp_path):
    """ホストOS起動時、対象のDockerコンテナが --network host の場合は拒否されること"""
    inspect_data = [
        {
            "Name": "/hostnet-container",
            "State": {"Running": True},
            "HostConfig": {"Privileged": False, "NetworkMode": "host"},
            "Mounts": [],
            "Config": {"WorkingDir": "/workspaces/app"},
        }
    ]
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = json.dumps(inspect_data)

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", return_value=mock_inspect):
        with pytest.raises(SandboxSecurityError) as exc_info:
            DeveloperProject(str(tmp_path), require_approval=False, container_name="hostnet-container")
        assert "--network host" in str(exc_info.value)


def test_host_os_delegates_to_safe_docker_container(tmp_path):
    """ホストOS起動時、安全なコンテナが確認できれば docker exec で委譲実行されること"""
    inspect_data = [
        {
            "Name": "/safe-devcontainer",
            "State": {"Running": True},
            "HostConfig": {"Privileged": False, "NetworkMode": "none"},
            "Mounts": [
                {
                    "Source": str(tmp_path),
                    "Destination": "/workspaces/project",
                }
            ],
            "Config": {"WorkingDir": "/workspaces/project"},
        }
    ]
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = json.dumps(inspect_data)

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", return_value=mock_inspect):
        dev_proj = DeveloperProject(
            str(tmp_path),
            require_approval=False,
            container_name="safe-devcontainer",
        )
        assert dev_proj.execution_mode == "host_docker"
        assert dev_proj.container_name == "safe-devcontainer"
        assert dev_proj.container_workspace_dir == "/workspaces/project"

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"Hello from container\n", b"")
        mock_proc.returncode = 0

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            res = dev_proj.run_sandbox_command("echo Hello")
            assert "成功" in res
            exec_args = mock_popen.call_args.args[0]
            assert exec_args[:4] == ["docker", "exec", "-w", "/workspaces/project"]
            assert "safe-devcontainer" in exec_args


def test_sandbox_command_timeout_kills_process(tmp_path):
    """コマンドがタイムアウトした際、プロセスがkillされタイムアウトエラーが返ること"""
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)

        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="sleep 100", timeout=5),
            (b"partial output", b""),
        ]

        with patch("subprocess.Popen", return_value=mock_proc):
            res = dev_proj.run_sandbox_command("sleep 100", timeout=5)
            assert "タイムアウト（5秒）" in res
            assert "partial output" in res
            mock_proc.kill.assert_called_once()


def test_sandbox_command_keyboard_interrupt_kills_child_process_and_reraises(tmp_path):
    """
    コマンド実行中（communicate待機中）に Ctrl+C (KeyboardInterrupt) が発生した際、
    子プロセスが即座に強制終了（kill / wait）され、KeyboardInterrupt が握りつぶされず上位へ再送出されること
    """
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.communicate.side_effect = KeyboardInterrupt()

        with patch("subprocess.Popen", return_value=mock_proc), \
             patch.object(DeveloperProject, "_kill_process_group_if_posix") as mock_kill_pg:
            with pytest.raises(KeyboardInterrupt):
                dev_proj.run_sandbox_command("pytest -v")

            mock_kill_pg.assert_called_once_with(mock_proc)
            mock_proc.kill.assert_called_once()
            mock_proc.wait.assert_called_once_with(timeout=2)
            assert dev_proj._active_process is None


def test_sandbox_command_approval_and_circuit_breaker(tmp_path):
    """シェル実行が拒否された後にAIが連続してコマンド実行を試みた場合、サーキットブレーカーが作動すること"""
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(
            str(tmp_path),
            require_approval=True,
            approval_callback=lambda _: "n",
        )

        # 1回目は拒否メッセージが返る
        res1 = dev_proj.run_sandbox_command("rm -rf build")
        assert "拒否されました" in res1

        # リセットなしに2回目を強行しようとすると AgentExecutionAborted が送出される
        with pytest.raises(AgentExecutionAborted):
            dev_proj.run_sandbox_command("pytest")


def test_parse_proc_mountinfo_and_classify_workspace_entry():
    """/proc/self/mountinfo の解析と、.envの/dev/nullマスク・.gitのroマウント・未保護ファイルの診断テスト"""
    sample_mountinfo = "\n".join(
        [
            "40 35 0:38 / /app rw,relatime - virtiofs workspace rw",
            "41 40 1:3 /null /app/.env ro,nosuid,nodev,noexec,relatime - devtmpfs udev rw",
            "42 40 0:38 /git /app/.git ro,relatime - virtiofs workspace rw",
            "43 40 0:45 / /app/.venv rw,relatime - tmpfs tmpfs rw",
        ]
    )
    mounts = parse_proc_mountinfo(sample_mountinfo)
    assert mounts["/app"]["rw"] is True
    assert mounts["/app/.env"]["is_dev_null"] is True
    assert mounts["/app/.env"]["rw"] is False
    assert mounts["/app/.git"]["rw"] is False
    assert mounts["/app/.venv"]["is_isolated_volume"] is True

    # 1. /dev/null でマスクされた .env は safe (🟢) と判定され、警告が出ないこと
    env_entry, env_warn = classify_workspace_entry(
        rel_path=".env",
        name=".env",
        perms="crw-rw-rw-",
        uid=0,
        gid=0,
        size=0,
        is_dir=False,
        is_symlink=False,
        is_dev_null=True,
        mount_info=mounts["/app/.env"],
        workspace_rw=True,
        container_workspace_dir="/app",
    )
    assert env_entry["risk_level"] == "safe"
    assert "/dev/null マスク済" in env_entry["status_label"]
    assert env_warn is None

    # 2. マスクされていない秘密鍵ファイル (id_rsa) は danger (🔴) と判定され、警告が出ること
    key_entry, key_warn = classify_workspace_entry(
        rel_path="id_rsa",
        name="id_rsa",
        perms="-rw-------",
        uid=1000,
        gid=1000,
        size=1679,
        is_dir=False,
        is_symlink=False,
        is_dev_null=False,
        mount_info=None,
        workspace_rw=True,
        container_workspace_dir="/app",
    )
    assert key_entry["risk_level"] == "danger"
    assert key_warn is not None
    assert key_warn["level"] == "danger"
    assert "/dev/null:/app/id_rsa:ro" in key_warn["recommendation"]

    # 3. 読み取り専用 (ro) でマウントされた .git は safe (🟢) と判定され、警告が出ないこと
    git_entry, git_warn = classify_workspace_entry(
        rel_path=".git",
        name=".git",
        perms="drwxr-xr-x",
        uid=1000,
        gid=1000,
        size=4096,
        is_dir=True,
        is_symlink=False,
        is_dev_null=False,
        mount_info=mounts["/app/.git"],
        workspace_rw=True,
        container_workspace_dir="/app",
    )
    assert git_entry["risk_level"] == "safe"
    assert git_warn is None

    # 4. ホストと RW 共有された node_modules は warning (🟡) と判定され、推奨対策が出ること
    nm_entry, nm_warn = classify_workspace_entry(
        rel_path="node_modules",
        name="node_modules",
        perms="drwxr-xr-x",
        uid=1000,
        gid=1000,
        size=4096,
        is_dir=True,
        is_symlink=False,
        is_dev_null=False,
        mount_info=None,
        workspace_rw=True,
        container_workspace_dir="/app",
    )
    assert nm_entry["risk_level"] == "warning"
    assert nm_warn is not None
    assert nm_warn["level"] == "warning"


def test_inspect_sandbox_status_host_docker(tmp_path):
    """inspect_sandbox_status がコンテナの設定・マウント・ファイル状態を統合して診断結果を返すこと"""
    inspect_data = [
        {
            "Name": "/gemio-sandbox-py",
            "State": {"Running": True},
            "HostConfig": {
                "Privileged": False,
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Memory": 2 * (1024 ** 3),
                "NanoCpus": 2_000_000_000,
                "PidsLimit": 128,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
            },
            "Mounts": [
                {"Type": "bind", "Source": str(tmp_path), "Destination": "/app", "RW": True},
                {"Type": "bind", "Source": "/dev/null", "Destination": "/app/.env", "RW": False},
                {"Type": "bind", "Source": str(tmp_path / ".git"), "Destination": "/app/.git", "RW": False},
                {"Type": "bind", "Source": str(tmp_path / "pyproject.toml"), "Destination": "/app/pyproject.toml", "RW": False},
            ],
            "Config": {"WorkingDir": "/app", "User": "1000:1000"},
        }
    ]
    mock_inspect = MagicMock()
    mock_inspect.returncode = 0
    mock_inspect.stdout = json.dumps(inspect_data)

    probe_stdout = (
        "uid=1000(sandbox) gid=1000(sandbox)\n"
        "===GEMIO_MOUNTINFO===\n"
        "40 35 0:38 / /app rw,relatime - virtiofs workspace rw\n"
        "41 40 1:3 /null /app/.env ro,relatime - devtmpfs udev rw\n"
        "42 40 0:38 /git /app/.git ro,relatime - virtiofs workspace rw\n"
        "43 40 0:38 /pyproject.toml /app/pyproject.toml ro,relatime - virtiofs workspace rw\n"
        "===GEMIO_STAT===\n"
        "crw-rw-rw-|0|0|0|1:3|character special file|.env\n"
        "drwxr-xr-x|1000|1000|4096|0:0|directory|.git\n"
        "drwxr-xr-x|1000|1000|4096|0:0|directory|.venv\n"
        "drwxr-xr-x|1000|1000|4096|0:0|directory|docs\n"
        "-rw-r--r--|1000|1000|120|0:0|regular file|pyproject.toml\n"
        "drwxr-xr-x|1000|1000|4096|0:0|directory|src\n"
    )
    mock_probe = MagicMock()
    mock_probe.returncode = 0
    mock_probe.stdout = probe_stdout

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch("subprocess.run", side_effect=[mock_inspect, mock_inspect, mock_probe]):
        dev_proj = DeveloperProject(
            str(tmp_path),
            require_approval=False,
            container_name="gemio-sandbox-py",
        )
        status = dev_proj.inspect_sandbox_status()

        assert status["container_name"] == "gemio-sandbox-py"
        assert status["network"]["is_isolated"] is True
        assert status["user"]["is_root"] is False
        assert status["resources"]["readonly_rootfs"] is True

        entries_by_path = {e["rel_path"]: e for e in status["entries"]}
        assert entries_by_path[".env"]["risk_level"] == "safe"
        assert entries_by_path[".env"]["is_dev_null"] is True
        assert entries_by_path[".git"]["risk_level"] == "safe"
        assert entries_by_path[".git"]["mount_mode"] == "ro"
        assert entries_by_path[".venv"]["risk_level"] == "warning"
        assert entries_by_path["pyproject.toml"]["risk_level"] == "normal"
        assert entries_by_path["pyproject.toml"]["mount_mode"] == "ro"
        assert entries_by_path["pyproject.toml"]["status_label"] == "⚪ 読取専用 (ro)"
        assert entries_by_path["docs"]["risk_level"] == "normal"
        assert entries_by_path["docs"]["mount_mode"] == "rw"
        assert entries_by_path["docs"]["status_label"] == "🔵 作業対象 (rw)"

        # ソート順: アルファベット順では docs が pyproject.toml より先だが、
        # 読取専用(ro)の pyproject.toml が 作業対象(rw)の docs / src より先に並ぶこと
        rel_order = [e["rel_path"] for e in status["entries"]]
        assert rel_order == [".env", ".git", ".venv", "pyproject.toml", "docs", "src"]

        # 警告は .venv のRW共有に関する1件のみであること
        assert len(status["warnings"]) == 1
        assert status["warnings"][0]["path"] == ".venv/"
        assert status["warnings"][0]["level"] == "warning"


def test_developer_project_edit_mode_auto_edits_but_confirms_command_and_blocks_after_rejection(tmp_path):
    """
    DeveloperProject のエディットモードにおいて、
    ファイル書き込みは自動承認され、run_sandbox_command は承認確認を求め、
    コマンド拒否直後に write_file 等のファイル書き換えを試みた場合はサーキットブレーカーで遮断されること
    """
    called_actions = []
    decisions = iter(["y", "n"])

    def mock_approval(desc: str) -> str:
        called_actions.append(desc)
        return next(decisions)

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(
            str(tmp_path),
            approval_mode="edit",
            approval_callback=mock_approval,
        )
        assert dev_proj.edit_mode is True

        # 1. ファイル書き込みは承認要求なしで自動成功する
        assert "成功" in dev_proj.write_file("app.py", "print('ok')\n")
        assert len(called_actions) == 0

        # 2. run_sandbox_command (1回目: 'y' で許可) -> 承認コールバックが呼ばれる
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"ok\n", b"")
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            res_cmd1 = dev_proj.run_sandbox_command("pytest")
            assert "成功 (終了コード: 0)" in res_cmd1
            assert len(called_actions) == 1
            assert "サンドボックス内シェルコマンドの実行" in called_actions[0]

        # 3. run_sandbox_command (2回目: 'n' で拒否)
        res_cmd2 = dev_proj.run_sandbox_command("rm -rf dist")
        assert "拒否されました" in res_cmd2
        assert len(called_actions) == 2

        # 4. 拒否直後にユーザーへ報告せず write_file でファイル書き換えを試みるとサーキットブレーカーが作動
        with pytest.raises(AgentExecutionAborted) as exc_info:
            dev_proj.write_file("app.py", "print('hacked')\n")
        assert "サーキットブレーカー" in str(exc_info.value)
        assert dev_proj.read_file("app.py") == "print('ok')\n"


def test_run_sandbox_command_approval_displays_full_command_and_description(tmp_path):
    """
    run_sandbox_command の承認要求メッセージに以下が正しく反映されること:
    1. 300文字・5行を超える長いコマンドでも省略されず全文が表示され、前後の括弧が付かないこと
    2. description 指定時はその説明が表示されること
    3. description 未指定（または空白のみ）時は '(説明なし)' と表示されること
    4. description が150文字を超える場合は先頭150文字で切り詰められ '...' が付与されること
    """
    called_actions: list[str] = []

    def mock_approval(desc: str) -> str:
        called_actions.append(desc)
        return "y"

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(
            str(tmp_path),
            require_approval=True,
            approval_callback=mock_approval,
        )

        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (b"done\n", b"")
        mock_proc.returncode = 0

        # 5行・300文字を大きく超える複数行Python検証コマンド
        long_python_cmd = "\n".join(
            [
                "python -c \"",
                "import sys",
                "import pathlib",
                "data = [i * 2 for i in range(100)]",
                "print('step 1: initialized data list with 100 elements')",
                "print('step 2: checking sum of elements =', sum(data))",
                "print('step 3: verifying path resolution and environment')",
                "assert sum(data) == 9900, 'unexpected sum value in verification'",
                "print('FINAL_LINE_MARKER_FOR_FULL_COMMAND_VERIFICATION')",
                "\"",
            ]
        )
        assert len(long_python_cmd) > 300
        assert len(long_python_cmd.splitlines()) > 5

        with patch("subprocess.Popen", return_value=mock_proc):
            # Case 1: 通常の description 付き + 長いコマンド
            dev_proj.run_sandbox_command(
                long_python_cmd,
                description="データ集計ロジックの計算結果をPythonワンライナーで検証",
            )
            desc1 = called_actions[-1]
            assert "説明: データ集計ロジックの計算結果をPythonワンライナーで検証" in desc1
            assert "実行コマンド:\n" in desc1
            assert "実行コマンド (" not in desc1
            assert "FINAL_LINE_MARKER_FOR_FULL_COMMAND_VERIFICATION" in desc1
            assert "省略" not in desc1

            # Case 2: description 未指定（デフォルト引数）
            dev_proj.run_sandbox_command("pytest -q")
            desc2 = called_actions[-1]
            assert "説明: (説明なし)" in desc2
            assert "実行コマンド:\n    pytest -q" in desc2

            # Case 3: description が150文字を超える場合（120文字はそのまま、160文字は150文字で切り詰め）
            desc_120 = "あ" * 120
            dev_proj.run_sandbox_command("pytest", description=desc_120)
            assert f"説明: {desc_120}\n" in called_actions[-1]

            desc_160 = "い" * 160
            dev_proj.run_sandbox_command("pytest", description=desc_160)
            expected_truncated = ("い" * 150) + "..."
            assert f"説明: {expected_truncated}\n" in called_actions[-1]
