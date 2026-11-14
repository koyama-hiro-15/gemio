import fnmatch
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from gemio.core.exceptions import AgentExecutionAborted
from gemio.core.tools.registry import Toolset
from gemio.project.base import (
    ALLOWED_ENV_FILES,
    IGNORED_DIRS,
    SECRET_DIR_NAMES,
    SECRET_FILE_PATTERNS,
    SENSITIVE_ENV_DIR_NAMES,
    Project,
)


class SandboxSecurityError(PermissionError):
    """
    サンドボックス環境の隔離条件やセキュリティ要件が満たされていない場合に送出される例外。
    """
    pass


# 子プロセス（シェル実行）へ引き継がない機密環境変数名（多言語・クラウド対応）
SCRUBBED_ENV_KEYS = {
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITLAB_TOKEN",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "PYPI_TOKEN",
    "CARGO_REGISTRY_TOKEN",
    "GEM_HOST_API_KEY",
    "COMPOSER_AUTH",
}

# 末尾一致で自動除去する機密環境変数サフィックス
SCRUBBED_ENV_SUFFIXES = (
    "_API_KEY",
    "_SECRET",
    "_SECRET_KEY",
    "_TOKEN",
    "_PASSWORD",
    "_PRIVATE_KEY",
)

DEFAULT_COMMAND_TIMEOUT = 60
MAX_COMMAND_TIMEOUT = 300
MAX_COMMAND_DESCRIPTION_CHARS = 150
MAX_OUTPUT_CHARS = 10000
MAX_OUTPUT_LINES = 200


def is_sensitive_env_key(key: str) -> bool:
    """
    指定された環境変数名がAPIキー・トークン・シークレット等の機密環境変数に該当するかを判定します。
    """
    upper_key = key.strip().upper()
    if not upper_key:
        return False
    if upper_key in SCRUBBED_ENV_KEYS:
        return True
    if any(upper_key.endswith(suffix) for suffix in SCRUBBED_ENV_SUFFIXES):
        return True
    return False


def scrub_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """
    サンドボックス内で実行する子プロセス向けに、APIキーやトークン等の機密環境変数を除去した環境変数辞書を返します。
    """
    source = base_env if base_env is not None else os.environ
    cleaned: dict[str, str] = {}
    for key, val in source.items():
        if is_sensitive_env_key(key):
            continue
        cleaned[key] = val
    return cleaned


def truncate_command_output(
    output: str,
    max_chars: int = MAX_OUTPUT_CHARS,
    max_lines: int = MAX_OUTPUT_LINES,
) -> str:
    """
    長大なコマンド出力（ビルドログやテスト出力）でコンテキストが溢れるのを防ぐため、
    上限を超える場合は先頭と末尾を残して中央を省略します。
    """
    if not output:
        return "(出力なし)"

    total_chars = len(output)
    lines = output.splitlines()
    total_lines = len(lines)

    if total_chars <= max_chars and total_lines <= max_lines:
        return output

    # 行数ベースで先頭半分・末尾半分を抽出
    if total_lines > max_lines:
        half_lines = max(1, max_lines // 2)
        head_part = "\n".join(lines[:half_lines])
        tail_part = "\n".join(lines[-half_lines:])
        omitted_lines = total_lines - (half_lines * 2)
        combined = (
            f"{head_part}\n"
            f"... [中略: 全 {total_lines} 行 / {total_chars} 文字のうち中央 {omitted_lines} 行を省略] ...\n"
            f"{tail_part}"
        )
    else:
        combined = output

    # さらに文字数上限を超えている場合は文字数でも先頭・末尾に切り詰め
    if len(combined) > max_chars:
        half_chars = max(100, max_chars // 2)
        head_chars = combined[:half_chars]
        tail_chars = combined[-half_chars:]
        combined = (
            f"{head_chars}\n"
            f"... [中略: 全 {total_chars} 文字 / {total_lines} 行のうち中央部分を省略] ...\n"
            f"{tail_chars}"
        )

    return combined


def parse_proc_mountinfo(mountinfo_text: str) -> dict[str, dict[str, Any]]:
    """
    Linuxの `/proc/self/mountinfo` の内容を解析し、マウントポイントごとの詳細情報を辞書として返します。
    """
    mounts: dict[str, dict[str, Any]] = {}
    for line in mountinfo_text.splitlines():
        line = line.strip()
        if not line or " - " not in line:
            continue
        left_part, right_part = line.split(" - ", 1)
        left_fields = left_part.split()
        right_fields = right_part.split()
        if len(left_fields) < 6 or len(right_fields) < 2:
            continue

        dev_id = left_fields[2]
        mount_root = left_fields[3].replace("\\040", " ")
        mount_point = left_fields[4].replace("\\040", " ")
        mount_opts = left_fields[5].split(",")

        fstype = right_fields[0]
        mount_source = right_fields[1].replace("\\040", " ")
        super_opts = right_fields[2].split(",") if len(right_fields) > 2 else []

        is_ro = "ro" in mount_opts or "ro" in super_opts
        is_dev_null = (
            dev_id == "1:3"
            or mount_root in ("/null", "null")
            or mount_source == "/dev/null"
        )
        is_tmpfs = fstype == "tmpfs"

        mounts[mount_point] = {
            "destination": mount_point,
            "source": "/dev/null" if is_dev_null else mount_source,
            "mount_root": mount_root,
            "rw": not is_ro,
            "fstype": fstype,
            "dev_id": dev_id,
            "is_dev_null": is_dev_null,
            "is_isolated_volume": is_tmpfs,
        }
    return mounts


def classify_workspace_entry(
    rel_path: str,
    name: str,
    perms: str,
    uid: int,
    gid: int,
    size: int,
    is_dir: bool,
    is_symlink: bool,
    is_dev_null: bool,
    mount_info: dict[str, Any] | None,
    workspace_rw: bool,
    container_workspace_dir: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """
    ワークスペース内の単一ファイル・ディレクトリについて、マウント状態と名称パターンから
    セキュリティ分類・リスクレベル・警告メッセージを判定します。
    """
    lower_name = name.lower()
    rel_parts = tuple(p.lower() for p in PurePosixPath(rel_path).parts)

    # 1. カテゴリの判定
    if not is_dir and lower_name in ALLOWED_ENV_FILES:
        category = "template"
    elif is_dir and (lower_name in SECRET_DIR_NAMES or any(p in SECRET_DIR_NAMES for p in rel_parts)):
        category = "secret"
    elif not is_dir and (
        any(fnmatch.fnmatch(lower_name, pat) for pat in SECRET_FILE_PATTERNS)
        or any(p in SECRET_DIR_NAMES for p in rel_parts)
    ):
        category = "secret"
    elif is_dir and lower_name in SENSITIVE_ENV_DIR_NAMES:
        category = "sensitive_env"
    else:
        category = "normal"

    # 2. 実効マウント状態（ro / rw / /dev/null / 隔離ボリューム）の判定
    is_isolated_vol = False
    if mount_info is not None:
        if mount_info.get("is_dev_null"):
            is_dev_null = True
        eff_rw = bool(mount_info.get("rw", True)) and not is_dev_null
        is_isolated_vol = bool(mount_info.get("is_isolated_volume", False))
        m_mode = "ro" if not eff_rw else "rw"
        if is_dev_null:
            mount_source_label = "/dev/null マスク (ro)"
        elif is_isolated_vol:
            mount_source_label = f"専用ボリューム/tmpfs ({m_mode})"
        else:
            mount_source_label = f"個別マウント ({m_mode})"
    else:
        eff_rw = workspace_rw and not is_dev_null
        m_mode = "ro" if not eff_rw else "rw"
        if is_dev_null:
            mount_source_label = "/dev/null マスク (ro)"
        else:
            mount_source_label = f"ワークスペース ({m_mode})"

    # 3. リスクレベルとステータス表示・警告の構築
    warning_item: dict[str, str] | None = None
    target_container_path = f"{container_workspace_dir.rstrip('/')}/{rel_path}"

    if category == "template":
        risk_level = "safe"
        status_label = "🟢 安全 (公開用テンプレート)"
    elif category == "secret":
        if is_dev_null:
            risk_level = "safe"
            status_label = "🟢 安全 (/dev/null マスク済)"
        elif is_dir and is_isolated_vol and size <= 4096:
            risk_level = "safe"
            status_label = "🟢 安全 (tmpfs/専用領域で隔離済)"
        else:
            risk_level = "danger"
            status_label = "🔴 危険 (機密露出・コンテナ内から読取可)"
            if is_dir:
                warning_item = {
                    "level": "danger",
                    "path": f"{rel_path}/",
                    "message": (
                        f"機密ディレクトリ '{rel_path}/' がマスクされずにマウントされており、"
                        "コンテナ内のシェルやスクリプトから内部の鍵・認証情報を読み取れる状態です。"
                    ),
                    "recommendation": (
                        f"マウント対象から外すか、'--mount type=tmpfs,destination={target_container_path},readonly' "
                        "等で空領域としてマスクしてください。"
                    ),
                }
            else:
                warning_item = {
                    "level": "danger",
                    "path": rel_path,
                    "message": (
                        f"機密ファイル '{rel_path}' (サイズ: {size} B) がマスクされずにマウントされており、"
                        "コンテナ内のシェルやテストコードから平文で読み取れる状態です。"
                    ),
                    "recommendation": (
                        f"起動オプションに '-v /dev/null:{target_container_path}:ro' を追加して "
                        "/dev/null でマスクすることを強く推奨します。"
                    ),
                }
    elif category == "sensitive_env":
        if is_dev_null:
            risk_level = "safe"
            status_label = "🟢 安全 (/dev/null マスク済)"
        elif is_isolated_vol:
            risk_level = "safe"
            status_label = "🟢 安全 (コンテナ専用ボリューム分離)"
        elif not eff_rw:
            risk_level = "safe"
            status_label = "🟢 安全 (読取専用 ro・改ざん不可)"
        else:
            risk_level = "warning"
            status_label = "🟡 注意 (ホストとRW共有・書込可)"
            if lower_name in (".venv", "venv", "node_modules", "vendor", ".bundle"):
                warning_item = {
                    "level": "warning",
                    "path": f"{rel_path}/",
                    "message": (
                        f"仮想環境/依存ディレクトリ '{rel_path}/' がホストOSと書き込み可能 (rw) で共有されています"
                        "（コンテナ内からのパッケージ改ざんによるホスト汚染やOS間バイナリ競合のリスク）。"
                    ),
                    "recommendation": (
                        f"匿名ボリューム ('-v {target_container_path}') でコンテナ専用に分離するか、"
                        f"'-v \"$(pwd)/{rel_path}:{target_container_path}:ro\"' で読み取り専用にすることを推奨します。"
                    ),
                }
            elif lower_name == ".git":
                warning_item = {
                    "level": "warning",
                    "path": ".git/",
                    "message": (
                        "Git管理領域 '.git/' が書き込み可能 (rw) でマウントされています"
                        "（コンテナ内コマンドから .git/hooks 等を書き換え可能な状態）。"
                    ),
                    "recommendation": (
                        f"'-v \"$(pwd)/.git:{target_container_path}:ro\"' で読み取り専用 (ro) にすることを推奨します。"
                    ),
                }
            else:
                warning_item = {
                    "level": "warning",
                    "path": f"{rel_path}/",
                    "message": (
                        f"管理・設定ディレクトリ '{rel_path}/' が書き込み可能 (rw) でマウントされています。"
                    ),
                    "recommendation": (
                        f"'-v \"$(pwd)/{rel_path}:{target_container_path}:ro\"' で読み取り専用 (ro) にすることを推奨します。"
                    ),
                }
    else:
        risk_level = "normal"
        status_label = "🔵 作業対象 (rw)" if eff_rw else "⚪ 読取専用 (ro)"

    display_name = f"{rel_path}/" if is_dir else rel_path
    entry_dict = {
        "name": display_name,
        "rel_path": rel_path,
        "perms": perms,
        "uid": uid,
        "gid": gid,
        "size": size,
        "is_dir": is_dir,
        "is_symlink": is_symlink,
        "is_dev_null": is_dev_null,
        "mount_mode": m_mode,
        "mount_source": mount_source_label,
        "category": category,
        "risk_level": risk_level,
        "status_label": status_label,
    }
    return entry_dict, warning_item


class DeveloperProject(Project):
    """
    開発者モード（--dev）用のプロジェクトクラス。
    Dev Container内起動（In-Container）およびホストOSからのコンテナ委譲実行（Host-Orchestrated）の両方に対応し、
    安全性が検証されたDockerサンドボックス内でのみシェルコマンドを実行します。
    """

    def __init__(
        self,
        project_path: str | Path,
        require_approval: bool = True,
        approval_callback: Callable[[str], bool | str] | None = None,
        ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None,
        container_name: str | None = None,
        approval_mode: str | None = None,
    ):
        """
        DeveloperProjectを初期化し、サンドボックス環境の安全性検証を行います。

        Args:
            project_path: 対象プロジェクトディレクトリのパス。
            require_approval: 操作実行時に人間の承認を求めるかどうか（approval_mode 未指定時の互換フラグ）。
            approval_callback: 承認確認用のUIコールバック。
            ask_user_callback: AIからユーザーへの質問・確認（ask_userツール）実行時に呼び出されるUIコールバック。
            container_name: ホストOS起動時に委譲先とするDockerコンテナ名またはID（省略時は自動探索）。
            approval_mode: 承認モード（"normal" | "edit" | "trust"）。指定時は require_approval より優先。
        """
        super().__init__(
            project_path,
            require_approval=require_approval,
            approval_callback=approval_callback,
            ask_user_callback=ask_user_callback,
            approval_mode=approval_mode,
        )
        self.container_name: str | None = container_name
        self.execution_mode: str = ""  # "in_container" または "host_docker"
        self.container_workspace_dir: str = f"/workspaces/{self.project_path.name}"
        self._active_process: subprocess.Popen | None = None
        self._last_inspect_info: dict[str, Any] = {}
        self._last_probed_env_keys: list[str] = []

        self._verify_and_setup_sandbox()

    @staticmethod
    def _kill_process_group_if_posix(proc: subprocess.Popen) -> None:
        """POSIX環境でプロセスグループが分離されている場合、配下の孫プロセスも含めて強制終了します。"""
        if os.name == "posix" and hasattr(os, "killpg"):
            pid = getattr(proc, "pid", None)
            if isinstance(pid, int) and pid > 0:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except Exception:
                    pass

    def terminate_active_process(self) -> None:
        """
        実行中のサンドボックス子プロセス（およびそのプロセスグループ）があれば即座に強制終了します。
        """
        proc = self._active_process
        self._active_process = None
        if proc is None:
            return
        self._kill_process_group_if_posix(proc)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    def _kill_process(self) -> None:
        """
        緊急停止（Kill）が選択された際、実行中のサンドボックス子プロセスがあれば即座にキルしてから終了します。
        """
        self.terminate_active_process()
        super()._kill_process()

    @staticmethod
    def _is_running_in_container() -> bool:
        """
        現在プロセスがLinuxコンテナ（Docker / Podman）の内部で動作しているかを厳格に判定します。
        ホストOS上の環境変数による誤判定（ホストOSでの誤実行）を防ぐため、
        Linuxプラットフォームかつコンテナ実体ファイルが存在する場合のみ True を返します。
        """
        if sys.platform != "linux":
            return False
        if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
            return True
        return False

    @staticmethod
    def _disable_process_dumpable() -> None:
        """
        Linuxコンテナ内で実行時、子プロセスから親プロセス（Gemio自身）のメモリ（/proc/$PPID/mem や ptrace）を
        読み取られるのを防ぐため、prctl(PR_SET_DUMPABLE, 0) を設定します。
        """
        if sys.platform != "linux":
            return
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_DUMPABLE = 4
            libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        except Exception:
            pass

    @staticmethod
    def _validate_in_container_security() -> None:
        """
        コンテナ内部で起動されている場合の致命的なセキュリティ境界（docker.sockマウント、特権モード）を検査します。
        ※環境変数内の各種APIキーについては起動を妨げず、診断ステータスで強い警告（danger）として通知します。
        """
        # 1. Dockerソケットのマウント検査
        if Path("/var/run/docker.sock").exists():
            raise SandboxSecurityError(
                "[SECURITY_ALERT] コンテナ内に '/var/run/docker.sock' が検出されました。\n"
                "ホストOSのDockerデーモンを乗っ取られるリスクがあるため、開発者モードの実行を拒否しました。"
            )

        # 2. Linux Capabilities による特権モード (--privileged) 検査
        status_path = Path("/proc/1/status")
        if status_path.exists():
            try:
                status_text = status_path.read_text(encoding="utf-8", errors="ignore")
                for line in status_text.splitlines():
                    if line.startswith("CapEff:"):
                        cap_hex = line.split(":", 1)[1].strip().lower()
                        # 特権コンテナの全ビット有効マスク (0000003fffffffff / 000001ffffffffff / ffffffffffffffff)
                        if cap_hex in (
                            "0000003fffffffff",
                            "000001ffffffffff",
                            "ffffffffffffffff",
                        ):
                            raise SandboxSecurityError(
                                "[SECURITY_ALERT] 特権モード (--privileged) で起動されたコンテナが検出されました。\n"
                                "コンテナエスケープのリスクがあるため、開発者モードの実行を拒否しました。"
                            )
            except SandboxSecurityError:
                raise
            except Exception:
                pass

    @staticmethod
    def _sanitize_inspect_info(info: dict[str, Any]) -> dict[str, Any]:
        """
        docker inspect の結果辞書に含まれる環境変数 (Config.Env) の値部分を即座にマスクし、
        APIキー等の平文値がメモリ上に保持されないようにサニタイズします。
        """
        config = info.get("Config")
        if isinstance(config, dict) and isinstance(config.get("Env"), list):
            sanitized_env: list[str] = []
            for item in config["Env"]:
                if not isinstance(item, str) or "=" not in item:
                    continue
                k, v = item.split("=", 1)
                k_clean = k.strip()
                if not k_clean:
                    continue
                if v.strip():
                    sanitized_env.append(f"{k_clean}=<redacted>")
                else:
                    sanitized_env.append(f"{k_clean}=")
            config["Env"] = sanitized_env
        return info

    def _discover_container_for_workspace(self) -> str:
        """
        ホストOS上で動作している場合、現在の project_path に対応する稼働中のDockerコンテナを探索します。
        """
        try:
            res = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except FileNotFoundError:
            raise SandboxSecurityError(
                "[SECURITY_ALERT] Docker CLI が見つかりません。\n"
                "ホストOS上で直接シェルコマンドを実行することは禁止されています。"
                "Dev Container内で起動するか、Docker環境を準備してください。"
            )
        except Exception as e:
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] Dockerデーモンへの接続に失敗しました: {e}"
            )

        if res.returncode != 0:
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] 稼働中のDockerコンテナ一覧を取得できませんでした: {res.stderr.strip()}"
            )

        container_ids = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not container_ids:
            raise SandboxSecurityError(
                "[SECURITY_ALERT] 稼働中のサンドボックス用Dockerコンテナが見つかりません。\n"
                "ホストOS上で直接シェルコマンドを実行することは禁止されています。\n"
                "Dev Containerを起動してから再度実行するか、`--container <コンテナ名>` を指定してください。"
            )

        norm_project = str(self.project_path).replace("\\", "/").lower().rstrip("/")

        for cid in container_ids:
            try:
                insp = subprocess.run(
                    ["docker", "inspect", cid],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
                if insp.returncode != 0:
                    continue
                data_list = json.loads(insp.stdout)
                if not data_list:
                    continue
                info = data_list[0]

                # 1. devcontainer.local_folder ラベルでの一致確認
                labels = info.get("Config", {}).get("Labels") or {}
                local_folder = labels.get("devcontainer.local_folder", "")
                if local_folder and local_folder.replace("\\", "/").lower().rstrip("/") == norm_project:
                    return cid

                # 2. バインドマウントのSourceパスでの一致確認
                mounts = info.get("Mounts") or []
                for m in mounts:
                    src = (m.get("Source") or "").replace("\\", "/").lower().rstrip("/")
                    if src == norm_project:
                        return cid
            except Exception:
                continue

        raise SandboxSecurityError(
            f"[SECURITY_ALERT] プロジェクト '{self.project_path}' に対応する稼働中のコンテナが見つかりませんでした。\n"
            "ホストOSでの直接コマンド実行は禁止されています。Dev Containerを起動するか、`--container <コンテナ名>` で対象コンテナを指定してください。"
        )

    def _validate_and_configure_docker_container(self, target_container: str) -> None:
        """
        指定されたDockerコンテナの `docker inspect` 情報を検証し、特権モード、ホストネットワーク共有、
        または危険なマウントがないことを確認します。
        """
        try:
            res = subprocess.run(
                ["docker", "inspect", target_container],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except FileNotFoundError:
            raise SandboxSecurityError(
                "[SECURITY_ALERT] Docker CLI がインストールされていないか、PATHに含まれていません。"
            )
        except Exception as e:
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] コンテナ情報の検査中にエラーが発生しました: {e}"
            )

        if res.returncode != 0:
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] 指定されたコンテナ '{target_container}' が見つからないか、情報を取得できません: {res.stderr.strip()}"
            )

        try:
            info_list = json.loads(res.stdout)
            info = self._sanitize_inspect_info(info_list[0])
        except Exception as e:
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] docker inspect の出力解析に失敗しました: {e}"
            )

        # 1. 稼働状態の確認
        state = info.get("State") or {}
        if not state.get("Running", False):
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] 指定されたコンテナ '{target_container}' は現在稼働していません。"
            )

        # 2. 特権モード (--privileged) およびホストネットワーク共有 (--network host) の拒否
        host_config = info.get("HostConfig") or {}
        if host_config.get("Privileged", False):
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] コンテナ '{target_container}' は特権モード (--privileged) で実行されているため、"
                "安全保護により接続を拒否しました。"
            )

        network_mode = str(host_config.get("NetworkMode") or "").lower()
        if network_mode == "host":
            raise SandboxSecurityError(
                f"[SECURITY_ALERT] コンテナ '{target_container}' はホストネットワーク共有モード (--network host) で実行されているため、"
                "安全保護により接続を拒否しました。"
            )

        # 3. マウント検査（docker.sock やホストルートマウントの拒否、および作業ディレクトリの特定）
        mounts = info.get("Mounts") or []
        norm_project = str(self.project_path).replace("\\", "/").lower().rstrip("/")
        detected_workdir = None

        for m in mounts:
            src = (m.get("Source") or "").replace("\\", "/")
            dst = (m.get("Destination") or "").replace("\\", "/")
            src_lower = src.lower().rstrip("/")

            if "docker.sock" in src_lower or "docker.sock" in dst.lower():
                raise SandboxSecurityError(
                    f"[SECURITY_ALERT] コンテナ '{target_container}' に docker.sock がマウントされているため拒否しました。"
                )

            if src_lower in ("", "/", "c:", "c:/"):
                raise SandboxSecurityError(
                    f"[SECURITY_ALERT] コンテナ '{target_container}' にホストのルートドライブが直接マウントされているため拒否しました。"
                )

            if src_lower == norm_project and dst:
                detected_workdir = dst

        if detected_workdir:
            self.container_workspace_dir = detected_workdir
        else:
            config_workdir = (info.get("Config") or {}).get("WorkingDir")
            if config_workdir:
                self.container_workspace_dir = config_workdir

        self.container_name = (info.get("Name") or target_container).lstrip("/")
        self._last_inspect_info = info

    def _verify_and_setup_sandbox(self) -> None:
        """
        起動環境を判別し、コンテナ内実行またはホストからのDockerコンテナ委譲実行のセットアップを行います。
        安全性が確認できない場合は SandboxSecurityError を送出します。
        """
        if not self.container_name and self._is_running_in_container():
            self._validate_in_container_security()
            self._disable_process_dumpable()
            self.container_workspace_dir = str(self.project_path)
            self.execution_mode = "in_container"
            return

        target = self.container_name or self._discover_container_for_workspace()
        self._validate_and_configure_docker_container(target)
        self.execution_mode = "host_docker"

    def _collect_local_workspace_raw_entries(self, root_dir: Path) -> list[dict[str, Any]]:
        """
        ローカル（またはコンテナ内自身）のファイルシステムから `ls -la` 相当の属性情報と、
        サブディレクトリ内に存在する機密ファイル・機密ディレクトリの属性情報を収集します。
        ファイルの中身は一切読み取りません。
        """
        raw_entries: list[dict[str, Any]] = []
        seen_rel_paths: set[str] = set()

        def stat_entry(target: Path, rel_str: str) -> None:
            if rel_str in seen_rel_paths:
                return
            try:
                st = target.lstat()
            except Exception:
                return
            seen_rel_paths.add(rel_str)
            perms = stat.filemode(st.st_mode)
            is_dir = stat.S_ISDIR(st.st_mode)
            is_symlink = stat.S_ISLNK(st.st_mode)
            is_char = stat.S_ISCHR(st.st_mode)
            is_dev_null = False
            if is_char and hasattr(os, "major") and hasattr(os, "minor"):
                try:
                    if os.major(st.st_rdev) == 1 and os.minor(st.st_rdev) == 3:
                        is_dev_null = True
                except Exception:
                    pass
            raw_entries.append(
                {
                    "rel_path": rel_str,
                    "name": target.name,
                    "perms": perms,
                    "uid": getattr(st, "st_uid", 0),
                    "gid": getattr(st, "st_gid", 0),
                    "size": getattr(st, "st_size", 0),
                    "is_dir": is_dir,
                    "is_symlink": is_symlink,
                    "is_dev_null": is_dev_null,
                }
            )

        # 1. ワークスペース直下の全エントリ（隠しファイル含む）
        try:
            children = sorted(root_dir.iterdir(), key=lambda p: p.name)
        except Exception:
            children = []

        for child in children:
            stat_entry(child, child.name)

        # 2. サブディレクトリ（深さ2まで）に隠れている機密ファイル・機密ディレクトリの探索
        skip_scan_dirs = IGNORED_DIRS | {".git", ".venv", "venv", "node_modules", "target", "vendor"}
        for child in children:
            if (
                not child.is_dir()
                or child.is_symlink()
                or child.name in skip_scan_dirs
                or self._is_ignored_dir_name(child.name)
            ):
                continue
            try:
                sub_children = sorted(child.iterdir(), key=lambda p: p.name)
            except Exception:
                continue
            for sub in sub_children:
                rel_sub = f"{child.name}/{sub.name}"
                sub_lower = sub.name.lower()
                if sub.is_dir() and sub_lower in SECRET_DIR_NAMES:
                    stat_entry(sub, rel_sub)
                elif not sub.is_dir() and self._is_secret_file(sub.name):
                    stat_entry(sub, rel_sub)
                elif (
                    sub.is_dir()
                    and not sub.is_symlink()
                    and sub.name not in skip_scan_dirs
                    and not self._is_ignored_dir_name(sub.name)
                ):
                    try:
                        for sub2 in sorted(sub.iterdir(), key=lambda p: p.name):
                            rel_sub2 = f"{rel_sub}/{sub2.name}"
                            if sub2.is_dir() and sub2.name.lower() in SECRET_DIR_NAMES:
                                stat_entry(sub2, rel_sub2)
                            elif not sub2.is_dir() and self._is_secret_file(sub2.name):
                                stat_entry(sub2, rel_sub2)
                    except Exception:
                        continue

        return raw_entries

    def _probe_container_via_exec(self) -> tuple[str, str, list[dict[str, Any]] | None]:
        """
        host_docker モードにおいて、コンテナ内で軽量な読み取り専用プローブを実行し、
        (id文字列, /proc/self/mountinfo文字列, statエントリリスト) を取得します。
        併せて、コンテナ内の非空環境変数の「キー名のみ（値は出力しない）」を抽出し _last_probed_env_keys に記録します。
        """
        self._last_probed_env_keys = []
        probe_cmd = (
            "id 2>/dev/null; "
            "echo '===GEMIO_MOUNTINFO==='; "
            "cat /proc/self/mountinfo 2>/dev/null; "
            "echo '===GEMIO_STAT==='; "
            "stat -c '%A|%u|%g|%s|%t:%T|%F|%n' .* * "
            "config/master.key config/credentials/*.key .cargo/credentials* .config/gcloud .config/gh "
            "2>/dev/null || true; "
            "echo '===GEMIO_ENVKEYS==='; "
            "(env 2>/dev/null; tr '\\0' '\\n' < /proc/1/environ 2>/dev/null) "
            "| awk -F= 'index($0,\"=\")>1 {val=substr($0, index($0,\"=\")+1); "
            "gsub(/^[ \\t\\r\\n]+|[ \\t\\r\\n]+$/, \"\", val); if (val != \"\") print $1}' "
            "2>/dev/null || true"
        )
        exec_args = [
            "docker",
            "exec",
            "-w",
            self.container_workspace_dir,
            str(self.container_name),
            "/bin/sh",
            "-c",
            probe_cmd,
        ]
        try:
            res = subprocess.run(
                exec_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                env=scrub_environment(os.environ),
            )
        except Exception:
            return "", "", None

        if res.returncode != 0 and not res.stdout:
            return "", "", None

        out = res.stdout
        if "===GEMIO_MOUNTINFO===" not in out or "===GEMIO_STAT===" not in out:
            return out.strip(), "", None

        id_part, rest = out.split("===GEMIO_MOUNTINFO===", 1)
        mount_part, stat_and_env = rest.split("===GEMIO_STAT===", 1)
        if "===GEMIO_ENVKEYS===" in stat_and_env:
            stat_part, env_part = stat_and_env.split("===GEMIO_ENVKEYS===", 1)
            probed_keys: list[str] = []
            for k_line in env_part.splitlines():
                k_clean = k_line.strip()
                if k_clean and "=" not in k_clean and k_clean not in probed_keys:
                    probed_keys.append(k_clean)
            self._last_probed_env_keys = probed_keys
        else:
            stat_part = stat_and_env

        parsed_entries: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for line in stat_part.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            parts = line.split("|", 6)
            if len(parts) < 7:
                continue
            perms, uid_s, gid_s, size_s, dev_hex, ftype_s, fname = parts
            fname = fname.strip()
            if fname.startswith("./"):
                fname = fname[2:]
            if fname in ("", ".", "..") or fname in seen_names:
                continue
            seen_names.add(fname)
            try:
                uid_val = int(uid_s)
            except ValueError:
                uid_val = 0
            try:
                gid_val = int(gid_s)
            except ValueError:
                gid_val = 0
            try:
                size_val = int(size_s)
            except ValueError:
                size_val = 0

            is_dev_null = False
            if perms.startswith("c") and ":" in dev_hex:
                maj_s, min_s = dev_hex.split(":", 1)
                try:
                    if int(maj_s, 16) == 1 and int(min_s, 16) == 3:
                        is_dev_null = True
                except ValueError:
                    pass

            is_dir = perms.startswith("d") or "directory" in ftype_s.lower()
            is_symlink = perms.startswith("l") or "symbolic link" in ftype_s.lower()
            parsed_entries.append(
                {
                    "rel_path": fname,
                    "name": PurePosixPath(fname).name,
                    "perms": perms,
                    "uid": uid_val,
                    "gid": gid_val,
                    "size": size_val,
                    "is_dir": is_dir,
                    "is_symlink": is_symlink,
                    "is_dev_null": is_dev_null,
                }
            )

        return id_part.strip(), mount_part.strip(), parsed_entries

    def _detect_container_sensitive_env_keys(
        self,
        inspect_data: dict[str, Any] | None = None,
    ) -> list[str]:
        """
        コンテナ内に設定されている機密環境変数（各種APIキー・トークン・シークレット）のキー名一覧を検出します。
        セキュリティ保護のため、環境変数の「値」は読み取らず（保持・返却せず）、
        値が空でない機密環境変数の「キー名」のみをソート済みリストで返します。
        """
        detected: set[str] = set()

        if self.execution_mode == "in_container":
            # 1. 現在のプロセス環境変数 (os.environ) からキー名のみを検査
            for key in os.environ.keys():
                if is_sensitive_env_key(key) and bool(os.environ.get(key, "").strip()):
                    detected.add(key.strip())

            # 2. コンテナ初期プロセス (/proc/1/environ) からキー名のみを検査（値部分はデコード・保持しない）
            proc1_env_path = Path("/proc/1/environ")
            if proc1_env_path.exists():
                try:
                    proc1_bytes = proc1_env_path.read_bytes()
                    for env_b in proc1_bytes.split(b"\x00"):
                        if b"=" not in env_b:
                            continue
                        key_b, val_b = env_b.split(b"=", 1)
                        if not val_b.strip():
                            continue
                        key_name = key_b.decode("utf-8", errors="ignore").strip()
                        if key_name and is_sensitive_env_key(key_name):
                            detected.add(key_name)
                except Exception:
                    pass
        else:
            # host_docker モード:
            # 1. docker inspect の Config.Env（サニタイズ済み）から非空の機密キー名を抽出
            target_inspect = inspect_data if inspect_data is not None else self._last_inspect_info
            config_sec = (target_inspect or {}).get("Config") or {}
            env_list = config_sec.get("Env") or []
            if isinstance(env_list, list):
                for item in env_list:
                    if not isinstance(item, str) or "=" not in item:
                        continue
                    key_part, val_part = item.split("=", 1)
                    key_name = key_part.strip()
                    if key_name and val_part.strip() and is_sensitive_env_key(key_name):
                        detected.add(key_name)

            # 2. docker exec プローブで取得した非空環境変数キー名から機密キー名を抽出
            for key_name in self._last_probed_env_keys:
                if key_name and is_sensitive_env_key(key_name):
                    detected.add(key_name)

        return sorted(detected)

    def inspect_sandbox_status(self) -> dict[str, Any]:
        """
        サンドボックスコンテナの現在のセキュリティ設定、ネットワーク・ユーザー情報、
        マウント状態、およびワークスペース内のファイル・フォルダのアクセス権（ls -la 相当）と
        重要ファイル保護状態を診断して返します。
        """
        warnings: list[dict[str, str]] = []
        mounts_map: dict[str, dict[str, Any]] = {}
        workspace_dir = self.container_workspace_dir.rstrip("/") or "/"
        inspect_data: dict[str, Any] = {}

        user_info = {
            "uid": None,
            "gid": None,
            "user_str": "不明",
            "is_root": False,
        }
        network_info = {
            "mode": "unknown",
            "is_isolated": False,
        }
        resources_info = {
            "readonly_rootfs": False,
            "memory_limit": "無制限 (未設定)",
            "cpus": "無制限 (未設定)",
            "pids_limit": "無制限 (未設定)",
            "cap_drop": [],
            "no_new_privileges": False,
        }

        if self.execution_mode == "host_docker":
            # 1. docker inspect による最新コンテナ設定の取得（環境変数の値は即座にマスク）
            inspect_data = self._last_inspect_info
            if self.container_name:
                try:
                    res = subprocess.run(
                        ["docker", "inspect", str(self.container_name)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=10,
                    )
                    if res.returncode == 0 and res.stdout.strip():
                        data_list = json.loads(res.stdout)
                        if data_list:
                            inspect_data = self._sanitize_inspect_info(data_list[0])
                            self._last_inspect_info = inspect_data
                except Exception:
                    pass

            host_cfg = (inspect_data or {}).get("HostConfig") or {}
            config_sec = (inspect_data or {}).get("Config") or {}

            net_mode = str(host_cfg.get("NetworkMode") or "default").lower()
            network_info["mode"] = net_mode
            network_info["is_isolated"] = net_mode == "none"

            resources_info["readonly_rootfs"] = bool(host_cfg.get("ReadonlyRootfs", False))
            mem_bytes = int(host_cfg.get("Memory") or 0)
            if mem_bytes > 0:
                resources_info["memory_limit"] = f"{mem_bytes / (1024 ** 3):.2f} GB"
            nano_cpus = int(host_cfg.get("NanoCpus") or 0)
            if nano_cpus > 0:
                resources_info["cpus"] = f"{nano_cpus / 1e9:.2f} CPUs"
            pids_lim = int(host_cfg.get("PidsLimit") or 0)
            if pids_lim > 0:
                resources_info["pids_limit"] = str(pids_lim)
            resources_info["cap_drop"] = list(host_cfg.get("CapDrop") or [])
            sec_opts = [str(s) for s in (host_cfg.get("SecurityOpt") or [])]
            resources_info["no_new_privileges"] = any("no-new-privileges" in s for s in sec_opts)

            # docker inspect の Mounts をマップに反映
            norm_project = str(self.project_path).replace("\\", "/").lower().rstrip("/")
            for m in (inspect_data or {}).get("Mounts") or []:
                dst = (m.get("Destination") or "").replace("\\", "/").rstrip("/")
                if not dst:
                    continue
                src = (m.get("Source") or "").replace("\\", "/")
                src_lower = src.lower().rstrip("/")
                m_type = str(m.get("Type") or "bind").lower()
                rw_flag = bool(m.get("RW", True))
                is_null = src_lower in ("/dev/null", "nul")
                is_isolated = (
                    m_type in ("volume", "tmpfs")
                    or (dst != workspace_dir and not src_lower.startswith(norm_project) and not is_null)
                )
                mounts_map[dst] = {
                    "destination": dst,
                    "source": "/dev/null" if is_null else src,
                    "rw": rw_flag and not is_null,
                    "fstype": m_type,
                    "is_dev_null": is_null,
                    "is_isolated_volume": is_isolated,
                }

            cfg_user = str(config_sec.get("User") or "").strip()
            if cfg_user:
                user_info["user_str"] = cfg_user
                if cfg_user in ("0", "root") or cfg_user.startswith("0:"):
                    user_info["uid"] = 0
                    user_info["is_root"] = True

            # 2. docker exec プローブでコンテナ内部の実効UID/GID、/proc/self/mountinfo、statを取得
            id_str, mountinfo_text, probe_entries = self._probe_container_via_exec()
            if id_str:
                user_info["user_str"] = id_str
                if "uid=0(" in id_str or id_str.startswith("uid=0 "):
                    user_info["uid"] = 0
                    user_info["is_root"] = True
                else:
                    for token in id_str.split():
                        if token.startswith("uid="):
                            num_part = token[4:].split("(", 1)[0]
                            if num_part.isdigit():
                                user_info["uid"] = int(num_part)
                                user_info["is_root"] = int(num_part) == 0
                        elif token.startswith("gid="):
                            num_part = token[4:].split("(", 1)[0]
                            if num_part.isdigit():
                                user_info["gid"] = int(num_part)

            if mountinfo_text:
                proc_mounts = parse_proc_mountinfo(mountinfo_text)
                for m_dst, m_data in proc_mounts.items():
                    norm_dst = m_dst.rstrip("/") or "/"
                    if norm_dst in mounts_map:
                        mounts_map[norm_dst]["rw"] = mounts_map[norm_dst]["rw"] and m_data["rw"]
                        if m_data["is_dev_null"]:
                            mounts_map[norm_dst]["is_dev_null"] = True
                            mounts_map[norm_dst]["source"] = "/dev/null"
                            mounts_map[norm_dst]["rw"] = False
                        if m_data["is_isolated_volume"]:
                            mounts_map[norm_dst]["is_isolated_volume"] = True
                    elif norm_dst == "/" or norm_dst == workspace_dir or norm_dst.startswith(workspace_dir + "/"):
                        mounts_map[norm_dst] = m_data

            raw_entries = (
                probe_entries
                if probe_entries is not None
                else self._collect_local_workspace_raw_entries(self.project_path)
            )
        else:
            # In-Container モード: コンテナ内部のPythonプロセスから直接検査
            uid_val = os.getuid() if hasattr(os, "getuid") else 1000
            gid_val = os.getgid() if hasattr(os, "getgid") else 1000
            user_info = {
                "uid": uid_val,
                "gid": gid_val,
                "user_str": f"uid={uid_val} gid={gid_val}",
                "is_root": uid_val == 0,
            }
            mountinfo_path = Path("/proc/self/mountinfo")
            if mountinfo_path.exists():
                try:
                    mounts_map = parse_proc_mountinfo(
                        mountinfo_path.read_text(encoding="utf-8", errors="ignore")
                    )
                except Exception:
                    pass
            if "/" in mounts_map:
                resources_info["readonly_rootfs"] = not mounts_map["/"].get("rw", True)

            # ネットワークインターフェースの有無からオフライン状態を推定
            net_dev_path = Path("/proc/net/dev")
            if net_dev_path.exists():
                try:
                    devs = [
                        line.split(":", 1)[0].strip()
                        for line in net_dev_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                        if ":" in line
                    ]
                    non_lo = [d for d in devs if d != "lo"]
                    network_info["mode"] = "none" if not non_lo else f"container ({', '.join(non_lo)})"
                    network_info["is_isolated"] = not non_lo
                except Exception:
                    pass

            raw_entries = self._collect_local_workspace_raw_entries(self.project_path)

        # ワークスペース自体のRW判定
        ws_mount = mounts_map.get(workspace_dir)
        workspace_rw = bool(ws_mount.get("rw", True)) if ws_mount else True

        # コンテナ内の機密環境変数（各種APIキー・トークン等）のキー名検査（値は読み取らない）
        sensitive_env_keys = self._detect_container_sensitive_env_keys(inspect_data=inspect_data)
        if sensitive_env_keys:
            keys_str = ", ".join(sensitive_env_keys)
            warnings.append(
                {
                    "level": "danger",
                    "path": f"コンテナ環境変数 ({keys_str})",
                    "message": (
                        "コンテナ内の環境変数にAPIキー・機密トークンが設定されています。"
                        "AIが実行するシェルコマンドやテストコードから環境変数（env や /proc/*/environ）経由で値が読み取られるリスクがあります。"
                    ),
                    "recommendation": (
                        "テスト対象アプリで必須でない場合は、compose.yaml / devcontainer.json / docker run -e やシェルの環境変数から削除してください"
                        "（Dev Container内起動時は環境変数を削除すると起動時セキュア入力プロンプトが利用できます）。"
                    ),
                }
            )

        # コンテナ実行ユーザー (root) の警告チェック
        if user_info["is_root"]:
            warnings.append(
                {
                    "level": "warning",
                    "path": "Container User (root / UID=0)",
                    "message": (
                        "コンテナが root ユーザー (UID=0) で動作しています。"
                        "コンテナ内で作成・変更されたファイルがホスト側で root 所有になり編集不能になるリスクがあります。"
                    ),
                    "recommendation": (
                        "docker run の '-u 1000:1000' または compose.yaml の 'user: \"1000:1000\"'、"
                        "devcontainer.json の 'remoteUser' で非特権ユーザーを指定してください。"
                    ),
                }
            )

        # 各ファイル・フォルダの分類とマウント保護状態の診断
        classified_entries: list[dict[str, Any]] = []
        for raw in raw_entries:
            rel_path = raw["rel_path"]
            entry_container_path = f"{workspace_dir}/{rel_path}"
            entry_mount = mounts_map.get(entry_container_path)

            entry_dict, warn_item = classify_workspace_entry(
                rel_path=rel_path,
                name=raw["name"],
                perms=raw["perms"],
                uid=raw["uid"],
                gid=raw["gid"],
                size=raw["size"],
                is_dir=raw["is_dir"],
                is_symlink=raw["is_symlink"],
                is_dev_null=raw["is_dev_null"],
                mount_info=entry_mount,
                workspace_rw=workspace_rw,
                container_workspace_dir=workspace_dir,
            )
            classified_entries.append(entry_dict)
            if warn_item is not None:
                warnings.append(warn_item)

        # 表示順のソート: 機密(secret) -> 管理/仮想環境(sensitive_env) -> テンプレート -> 通常(読取専用 ro) -> 通常(作業対象 rw)
        category_order = {"secret": 0, "sensitive_env": 1, "template": 2, "normal": 3}
        classified_entries.sort(
            key=lambda e: (
                category_order.get(e["category"], 9),
                1 if (e["category"] == "normal" and e.get("mount_mode") == "rw") else 0,
                e["rel_path"],
            )
        )

        # ワークスペース関連のマウント一覧を抽出
        relevant_mounts = [
            m
            for dst, m in sorted(mounts_map.items())
            if dst == workspace_dir or dst.startswith(workspace_dir + "/")
        ]

        return {
            "execution_mode": self.execution_mode,
            "container_name": self.container_name or "in-container",
            "workspace_dir": workspace_dir,
            "workspace_rw": workspace_rw,
            "user": user_info,
            "network": network_info,
            "resources": resources_info,
            "sensitive_env_keys": sensitive_env_keys,
            "mounts": relevant_mounts,
            "entries": classified_entries,
            "warnings": warnings,
        }

    def run_sandbox_command(
        self,
        command: str,
        description: str = "",
        timeout: int = DEFAULT_COMMAND_TIMEOUT,
        workdir: str = ".",
    ) -> str:
        """
        安全に隔離されたサンドボックス（Dev Container / Dockerコンテナ）内でシェルコマンドを実行します。
        各言語のビルド・Linter・テスト実行（pytest, npm test, tsc, go test, cargo test 等）やスクリプトの動作検証に使用します。
        ※ .env 等の機密ファイルや .git/, .devcontainer/ をシェルコマンド経由で直接読み書き・改ざんすることは禁止です。

        Args:
            command: 実行するシェルコマンド文字列。
            description: コマンドを実行する意図・目的の短い説明（100文字以内）。
            timeout: タイムアウト秒数（デフォルト: 60秒、最大: 300秒）。
            workdir: プロジェクトルートからの相対作業ディレクトリパス（デフォルト: "."）。

        Returns:
            終了コード、標準出力（STDOUT）、標準エラー出力（STDERR）を含む実行結果テキスト。
        """
        try:
            self._check_circuit_breaker()
            if not command or not command.strip():
                return "[ERROR] 実行するコマンドが空です。"

            resolved_workdir = self._verify_and_get_path(workdir, is_write=False)
            if not resolved_workdir.exists() or not resolved_workdir.is_dir():
                return f"[ERROR] 指定された作業ディレクトリが存在しません: {workdir}"

            effective_timeout = max(1, min(int(timeout), MAX_COMMAND_TIMEOUT))
            rel_workdir = resolved_workdir.relative_to(self.project_path)
            rel_workdir_str = str(rel_workdir).replace("\\", "/") if str(rel_workdir) != "." else "."

            env_label = (
                "Dev Container (In-Container)"
                if self.execution_mode == "in_container"
                else f"Docker Container ({self.container_name})"
            )
            clean_desc = " ".join(description.strip().splitlines()) if description else ""
            if not clean_desc:
                desc_display = "(説明なし)"
            elif len(clean_desc) > MAX_COMMAND_DESCRIPTION_CHARS:
                desc_display = clean_desc[:MAX_COMMAND_DESCRIPTION_CHARS] + "..."
            else:
                desc_display = clean_desc

            formatted_command = "\n".join(f"    {line}" for line in command.splitlines())
            action_desc = (
                f"サンドボックス内シェルコマンドの実行 [{env_label}]\n"
                f"  説明: {desc_display}\n"
                f"  作業ディレクトリ: {rel_workdir_str} / タイムアウト: {effective_timeout}秒\n"
                f"  実行コマンド:\n"
                f"{formatted_command}"
            )

            # Human-in-the-loop: シェルコマンド実行の承認を求める（エディットモード時も確認必須）
            if not self._confirm(action_desc, force_confirm_in_edit=True):
                return (
                    "[ERROR] ユーザーによってシェルコマンドの実行が拒否されました。"
                    "勝手な判断で再実行や別コマンドの強行を行わず、一度作業を止めてユーザーに意図を説明し、指示を仰いでください。"
                )

            clean_env = scrub_environment(os.environ)

            if self.execution_mode == "in_container":
                shell_bin = "/bin/bash" if shutil.which("/bin/bash") else "/bin/sh"
                exec_args = [shell_bin, "-c", command]
                exec_cwd = str(resolved_workdir)
            elif self.execution_mode == "host_docker":
                if rel_workdir_str == ".":
                    container_cwd = self.container_workspace_dir
                else:
                    container_cwd = str(PurePosixPath(self.container_workspace_dir) / rel_workdir_str)
                exec_args = [
                    "docker",
                    "exec",
                    "-w",
                    container_cwd,
                    str(self.container_name),
                    "/bin/sh",
                    "-c",
                    command,
                ]
                exec_cwd = str(self.project_path)
            else:
                return "[ERROR] サンドボックス実行モードが正しく初期化されていません。"

            popen_kwargs: dict[str, Any] = {
                "cwd": exec_cwd,
                "env": clean_env,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True

            self._active_process = subprocess.Popen(
                exec_args,
                **popen_kwargs,
            )
            try:
                stdout_bytes, stderr_bytes = self._active_process.communicate(timeout=effective_timeout)
                returncode = self._active_process.returncode
            except subprocess.TimeoutExpired:
                self._kill_process_group_if_posix(self._active_process)
                self._active_process.kill()
                try:
                    stdout_bytes, stderr_bytes = self._active_process.communicate(timeout=2)
                except Exception:
                    stdout_bytes, stderr_bytes = b"", b""
                partial_out = truncate_command_output(stdout_bytes.decode("utf-8", errors="replace"))
                partial_err = truncate_command_output(stderr_bytes.decode("utf-8", errors="replace"))
                return (
                    f"[ERROR] コマンドの実行がタイムアウト（{effective_timeout}秒）しました。"
                    "無限ループや対話的な入力待ちが発生していないか確認してください。\n"
                    f"--- STDOUT (Partial) ---\n{partial_out}\n"
                    f"--- STDERR (Partial) ---\n{partial_err}"
                )
            except BaseException:
                self.terminate_active_process()
                raise
            finally:
                self._active_process = None

            stdout_str = truncate_command_output(stdout_bytes.decode("utf-8", errors="replace"))
            stderr_str = truncate_command_output(stderr_bytes.decode("utf-8", errors="replace"))

            status_header = (
                "成功 (終了コード: 0)"
                if returncode == 0
                else f"[ERROR] コマンドが異常終了しました (終了コード: {returncode})"
            )
            return (
                f"{status_header}\n"
                f"--- STDOUT ---\n{stdout_str}\n"
                f"--- STDERR ---\n{stderr_str}"
            )
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] サンドボックスコマンドの実行中にエラーが発生しました: {str(e)}"

    def agent_tools(self) -> list[Callable]:
        base_tools = super().agent_tools()
        return base_tools + [self.run_sandbox_command]

    def agent_toolsets(self) -> list[Toolset]:
        base_toolsets = super().agent_toolsets()
        sandbox_toolset = Toolset.from_callables(
            name="sandbox",
            description="隔離されたDockerサンドボックス内でビルド・テスト・検証コマンドを実行するツールセット",
            funcs=[self.run_sandbox_command],
            always_active=False,
        )
        return base_toolsets + [sandbox_toolset]
