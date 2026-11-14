import ast
import fnmatch
import hashlib
import re
import shutil
import sys
from pathlib import Path
from typing import Callable

from gemio.core.exceptions import AgentExecutionAborted
from gemio.core.tools.registry import Toolset


# 読み取り・書き込みともに完全拒否する機密ファイルパターン（多言語・クラウド対応）
SECRET_FILE_PATTERNS = (
    # 環境変数・シークレット定義ファイル
    ".env",
    ".env.*",
    ".envrc",
    ".secrets",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    # 秘密鍵・証明書・キーストア
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.ppk",
    "*.keystore",
    "*.jks",
    "*.kdbx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "id_ecdsa",
    "id_ecdsa.*",
    "id_dsa",
    "id_dsa.*",
    # 各言語パッケージマネージャ・フレームワーク・Git認証情報
    ".npmrc",
    ".yarnrc.yml",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "auth.json",
    "master.key",
    "credentials.toml",
    # クラウド・インフラ・コンテナ認証情報
    "*credentials*.json",
    "*service-account*.json",
    "client_secret*.json",
    "*.tfstate",
    "*.tfstate.*",
    "*.tfvars",
    "*.tfvars.json",
    "kubeconfig",
)

# 読み取り・書き込みともに完全拒否する機密ディレクトリ名（SSH鍵・クラウドCLI認証・キーリング等）
SECRET_DIR_NAMES = {
    ".ssh",
    ".aws",
    ".gcloud",
    ".azure",
    ".kube",
    ".docker",
    ".gnupg",
}

# コンテナとホスト間でRW共有するとホスト側設定改ざんや環境汚染リスクがある管理・仮想環境ディレクトリ名
SENSITIVE_ENV_DIR_NAMES = {
    # バージョン管理・サンドボックス・Gemio拡張・IDE・CI設定
    ".git",
    ".devcontainer",
    ".gemio",
    ".vscode",
    ".idea",
    ".github",
    # 各言語の仮想環境・依存パッケージ領域
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    ".bundle",
}

# .env.* のうち例外的に読み書きを許可するテンプレート用ファイル名
ALLOWED_ENV_FILES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
}

# 構造取得（get_structure）や全文検索（search_text）で自動除外する管理・キャッシュ・依存関係ディレクトリ
IGNORED_DIRS = {
    ".git",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    # Node.js / Frontend
    "node_modules",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    # Rust / Go / Java / PHP / Ruby
    "target",
    ".gradle",
    "vendor",
    # Build / Infra / Cache
    ".terraform",
    ".cache",
}

# ワイルドカードパターンで自動除外する管理・パッケージメタデータディレクトリ
IGNORED_DIR_PATTERNS = (
    "*.egg-info",
)

DEFAULT_STRUCTURE_MAX_DEPTH = 3
MAX_STRUCTURE_ENTRIES = 200
MAX_FIND_RESULTS = 200
MAX_SEARCH_RESULTS = 100

APPROVAL_MODE_NORMAL = "normal"
APPROVAL_MODE_EDIT = "edit"
APPROVAL_MODE_TRUST = "trust"
VALID_APPROVAL_MODES = {
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
}


def summarize_text_for_display(
    text: str,
    max_lines: int = 10,
    max_chars: int = 500,
    indent: str = "    ",
) -> str:
    """
    承認要求やログ表示のために、巨大なテキストデータを安全に要約・省略（トランケート）します。

    Args:
        text: 対象のテキストデータ。
        max_lines: 表示する最大行数。
        max_chars: 表示する最大文字数。
        indent: 各行の先頭に付与するインデント文字列。

    Returns:
        統計情報と省略済みプレビューを含む文字列。
    """
    if text is None:
        return f"{indent}(空)"

    total_chars = len(text)
    lines = text.splitlines()
    total_lines = len(lines) if lines else (1 if total_chars > 0 else 0)

    preview_text = text[:max_chars]
    preview_lines = preview_text.splitlines()

    truncated_by_lines = False
    if len(preview_lines) > max_lines:
        preview_lines = preview_lines[:max_lines]
        truncated_by_lines = True

    truncated_by_chars = total_chars > max_chars

    formatted_lines = [f"{indent}{line}" for line in preview_lines]
    body = "\n".join(formatted_lines) if formatted_lines else f"{indent}(空の内容)"

    if truncated_by_lines or truncated_by_chars:
        shown_lines = len(preview_lines)
        remaining_lines = max(0, total_lines - shown_lines)
        if remaining_lines > 0:
            body += f"\n{indent}... (以降 {remaining_lines} 行 / 全体 {total_chars} 文字を省略)"
        else:
            body += f"\n{indent}... (全体 {total_chars} 文字のうち先頭 {max_chars} 文字のみ表示)"

    return f"規模: {total_chars} 文字 / {total_lines} 行\n{body}"


class Project:
    """
    Gemio 基本モードのプロジェクト管理クラス。
    ホストOSまたはコンテナ上の指定ディレクトリ配下に限定した安全なファイル操作と、
    通常承認モード／エディットモード／トラストモードの3段階制御および致命的領域のガードレールを提供します。
    """

    def __init__(
        self,
        project_path: str | Path,
        require_approval: bool = True,
        approval_callback: Callable[[str], bool | str] | None = None,
        ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None,
        approval_mode: str | None = None,
    ):
        """
        プロジェクトインスタンスを初期化します。

        Args:
            project_path: プロジェクトディレクトリのパス。
            require_approval: 変更操作時に人間の承認を求めるかどうか（approval_mode 未指定時の互換フラグ）。
            approval_callback: 承認を求めるUIコールバック関数（省略時は標準入出力）。
            ask_user_callback: AIからユーザーへの質問・確認（ask_userツール）実行時に呼び出されるUIコールバック関数。
            approval_mode: 承認モード（"normal" | "edit" | "trust"）。指定時は require_approval より優先。
        """
        self.project_path = Path(project_path).resolve()
        self.project_path.mkdir(parents=True, exist_ok=True)
        if approval_mode is not None:
            norm_mode = approval_mode.strip().lower()
            if norm_mode not in VALID_APPROVAL_MODES:
                raise ValueError(f"無効な approval_mode です: {approval_mode}")
            self._approval_mode: str = norm_mode
        else:
            self._approval_mode = APPROVAL_MODE_NORMAL if require_approval else APPROVAL_MODE_TRUST
        self.approval_callback = approval_callback
        self.ask_user_callback = ask_user_callback
        self._rejection_pending: bool = False

    @property
    def path(self) -> str:
        """プロジェクトディレクトリの絶対パス文字列を返します。"""
        return str(self.project_path)

    @property
    def approval_mode(self) -> str:
        """現在の承認モード（'normal', 'edit', 'trust'）を返します。"""
        return self._approval_mode

    @property
    def require_approval(self) -> bool:
        """通常承認モード（全変更操作で承認必須）かどうかを返します（後方互換プロパティ）。"""
        return self._approval_mode == APPROVAL_MODE_NORMAL

    @require_approval.setter
    def require_approval(self, value: bool) -> None:
        self._approval_mode = APPROVAL_MODE_NORMAL if value else APPROVAL_MODE_TRUST

    @property
    def edit_mode(self) -> bool:
        """現在エディットモード（ファイル編集自動承認・削除とコマンドは都度確認）が有効かどうかを返します。"""
        return self._approval_mode == APPROVAL_MODE_EDIT

    @property
    def trust_mode(self) -> bool:
        """現在トラストモード（全変更操作を自動承認）が有効かどうかを返します。"""
        return self._approval_mode == APPROVAL_MODE_TRUST

    def reset_rejection_state(self) -> None:
        """
        承認拒否の保留状態（サーキットブレーカー監視フラグ）をリセットします。
        """
        self._rejection_pending = False

    def on_prompt_start(self) -> None:
        """
        プロンプト処理開始時に呼び出されるコールバックハンドラ。
        承認拒否の保留状態（サーキットブレーカー監視フラグ）をリセットします。
        """
        self._rejection_pending = False

    def on_prompt_end(self) -> None:
        """
        プロンプト処理終了時に呼び出されるコールバックハンドラ。
        承認拒否の保留状態（サーキットブレーカー監視フラグ）をリセットします。
        """
        self._rejection_pending = False

    def set_approval_mode(self, mode: str) -> None:
        """
        承認モード（'normal' | 'edit' | 'trust'）を切り替えます。

        Args:
            mode: 設定する承認モード文字列。
        """
        norm_mode = mode.strip().lower()
        if norm_mode not in VALID_APPROVAL_MODES:
            raise ValueError(f"無効な approval_mode です: {mode}")
        self._approval_mode = norm_mode
        self._rejection_pending = False

    def set_edit_mode(self, enabled: bool) -> None:
        """
        エディットモード（ファイル作成・編集・移動・コピーは自動承認、削除とコマンド実行は都度確認）の
        有効・無効を切り替えます。

        Args:
            enabled: True の場合はエディットモード、False の場合は通常承認モード。
        """
        self.set_approval_mode(APPROVAL_MODE_EDIT if enabled else APPROVAL_MODE_NORMAL)

    def set_trust_mode(self, enabled: bool) -> None:
        """
        トラストモード（信頼済みプロジェクトにおける自動承認）の有効・無効を切り替えます。

        Args:
            enabled: True の場合はトラストモード、False の場合は通常承認モード。
        """
        self.set_approval_mode(APPROVAL_MODE_TRUST if enabled else APPROVAL_MODE_NORMAL)

    def set_autonomous_mode(self, enabled: bool) -> None:
        """後方互換用のエイリアス（set_trust_mode と同一）。"""
        self.set_trust_mode(enabled)

    def terminate_active_process(self) -> None:
        """
        実行中の外部子プロセスがあれば即座に強制終了します。
        サブプロセスを管理する派生クラス（DeveloperProject 等）でオーバーライドします。
        """
        pass

    def _kill_process(self) -> None:
        """
        緊急停止（Kill）が選択された際に、実行中の子プロセスがあれば停止した上でプロセスを即時終了します。
        """
        self.terminate_active_process()
        sys.exit(1)

    def _check_circuit_breaker(self) -> None:
        """
        直前の操作が拒否されているにもかかわらず、ユーザーへの報告・相談なしに連続して
        変更操作（ファイル書き換え等を含む）が試行された場合、サーキットブレーカーを作動させます。
        """
        if self._rejection_pending:
            self._rejection_pending = False
            raise AgentExecutionAborted(
                "承認拒否後にユーザーへの説明・確認を行わず連続して変更操作が試行されたため、"
                "安全保護（サーキットブレーカー）により作業を強制中止しました。"
            )

    def _confirm(self, action_description: str, *, force_confirm_in_edit: bool = False) -> bool:
        """
        変更操作に対する承認確認を行います。
        直前の操作が拒否されているにもかかわらず、ユーザーへの報告なしに連続して変更操作が
        呼び出された場合は、自動承認モードであってもサーキットブレーカーを作動させて作業を強制中止します。

        Args:
            action_description: 実行しようとしている操作の説明文字列。
            force_confirm_in_edit: True の場合、エディットモードであっても承認確認を要求します
                                   （delete_path や run_sandbox_command で使用）。
        """
        self._check_circuit_breaker()

        if self._approval_mode == APPROVAL_MODE_TRUST:
            return True

        if self._approval_mode == APPROVAL_MODE_EDIT and not force_confirm_in_edit:
            return True

        if self.approval_callback is not None:
            decision = self.approval_callback(action_description)
            if decision is True:
                return True
            if isinstance(decision, str):
                norm = decision.strip().lower()
                if norm in ["y", "yes", "approve"]:
                    return True
                if norm in ["a", "abort"]:
                    raise AgentExecutionAborted(
                        "ユーザーによって作業の強制中止（Abort）が選択されました。"
                    )
                if norm in ["k", "kill"]:
                    self._kill_process()
            self._rejection_pending = True
            return False

        print("\n" + "=" * 50)
        print("[Human-in-the-loop 承認要求]")
        print("Gemioが以下の操作を実行しようとしています:")
        print(f"  > {action_description}")
        print("=" * 50)

        while True:
            choice = input(
                "この操作を許可しますか？ [y: 許可 / n: 拒否 / a: 強制中止 / k: プロセス終了]: "
            ).strip().lower()
            if choice in ["y", "yes"]:
                return True
            elif choice in ["n", "no", ""]:
                print(
                    "操作が拒否されました。どのような意図でどのような操作を実行するか、"
                    "ユーザーに説明し、承認を得てください。"
                )
                self._rejection_pending = True
                return False
            elif choice in ["a", "abort"]:
                raise AgentExecutionAborted(
                    "ユーザーによって作業の強制中止（Abort）が選択されました。"
                )
            elif choice in ["k", "kill"]:
                self._kill_process()
            else:
                print("'y' (許可), 'n' (拒否), 'a' (強制中止), 'k' (プロセス終了) のいずれかを入力してください。")

    @staticmethod
    def _is_secret_file(file_name: str) -> bool:
        """ファイル名が保護対象の機密ファイル（.envや秘密鍵、認証情報など）に該当するか判定します。"""
        lower_name = file_name.lower()
        if lower_name in ALLOWED_ENV_FILES:
            return False
        for pattern in SECRET_FILE_PATTERNS:
            if fnmatch.fnmatch(lower_name, pattern):
                return True
        return False

    @staticmethod
    def _is_secret_dir(rel_parts: tuple[str, ...]) -> bool:
        """パス要素に保護対象の機密ディレクトリ（.ssh, .aws, .gcloud 等）が含まれるか判定します。"""
        return any(part.lower() in SECRET_DIR_NAMES for part in rel_parts)

    @staticmethod
    def _is_ignored_dir_name(dir_name: str) -> bool:
        """単一のディレクトリ名が走査除外対象（IGNORED_DIRS または IGNORED_DIR_PATTERNS）に該当するか判定します。"""
        if dir_name in IGNORED_DIRS:
            return True
        lower_name = dir_name.lower()
        return any(fnmatch.fnmatch(lower_name, pat) for pat in IGNORED_DIR_PATTERNS)

    @staticmethod
    def _is_ignored_dir(rel_parts: tuple[str, ...]) -> bool:
        """パス要素に走査除外対象ディレクトリ（.git, node_modules, .venv, *.egg-info 等）が含まれるか判定します。"""
        return any(
            Project._is_ignored_dir_name(part) or part.lower() in SECRET_DIR_NAMES
            for part in rel_parts
        )

    def _check_fatal_security_path(self, target_path: Path, is_write: bool = False) -> None:
        """
        トラストモード・通常承認モードを問わず、うっかり事故や機密漏洩を防ぐための
        ガードレール検査を行い、違反時は PermissionError を送出します。
        """
        if target_path == self.project_path:
            return

        rel_path = target_path.relative_to(self.project_path)
        parts = rel_path.parts
        file_name = target_path.name

        # 1. 機密ファイル・機密ディレクトリ（.env, 秘密鍵, .ssh, .aws, クラウド認証情報等）の読み書き・削除を完全ブロック
        if self._is_secret_file(file_name) or self._is_secret_dir(parts):
            raise PermissionError(
                f"[SECURITY_ALERT] 機密ファイルまたは機密領域 '{rel_path}' へのアクセスはセキュリティ保護のため禁止されています"
                "（シェルコマンド経由での読み書きも禁止です）。環境変数のひな形が必要な場合は '.env.example' 等を使用してください。"
            )

        # 2. .git ディレクトリ内部への直接ファイル操作（読み書き・削除）をブロック
        if parts and parts[0] == ".git":
            raise PermissionError(
                f"[SECURITY_ALERT] Git管理領域 '{rel_path}' への直接ファイル操作は禁止されています。"
            )

        # 3. 既存の .devcontainer 設定の改ざん・削除をブロック（初期作成時のみ許可）
        if parts and parts[0] == ".devcontainer" and is_write:
            devcontainer_json = self.project_path / ".devcontainer" / "devcontainer.json"
            if target_path.exists() or devcontainer_json.exists():
                raise PermissionError(
                    f"[SECURITY_ALERT] 既存のサンドボックス設定 '{rel_path}' の変更・削除はセキュリティ保護のため禁止されています。"
                )

        # 4. パッケージ生成メタデータ領域（*.egg-info）への直接書き込み・削除をブロック
        if is_write and any(part.lower().endswith(".egg-info") for part in parts):
            raise PermissionError(
                f"[SECURITY_ALERT] パッケージメタデータ領域 '{rel_path}' の直接変更・削除はセキュリティ保護のため禁止されています。"
            )

        # 5. Gemio拡張管理領域（.gemio/skills, .gemio/tools 等）への直接作成・変更・削除をブロック
        if is_write and parts and parts[0].lower() == ".gemio":
            raise PermissionError(
                f"[SECURITY_ALERT] Gemio拡張管理領域 '{rel_path}' の直接変更・削除はセキュリティ保護のため禁止されています。"
            )

    def _verify_and_get_path(self, relative_path: str | Path, is_write: bool = False) -> Path:
        """
        相対パスを解決し、プロジェクトルート配下であることおよびセキュリティガードレールに違反しないことを検証します。
        """
        raw_path = self.project_path / relative_path

        # シンボリックリンク解決前の名称でも機密ファイル判定を実施
        if self._is_secret_file(raw_path.name):
            raise PermissionError(
                f"[SECURITY_ALERT] 機密ファイル '{relative_path}' へのアクセスはセキュリティ保護のため禁止されています。"
            )

        try:
            resolved_target = raw_path.resolve()
        except Exception as e:
            raise PermissionError(f"[SECURITY_ALERT] パス解決エラー: {relative_path} ({e})")

        if not resolved_target.is_relative_to(self.project_path):
            raise PermissionError(
                "[SECURITY_ALERT] 不正なパス操作が検出されました。\n"
                f"要求されたパス: {relative_path}\n"
                "プロジェクトルート外へのアクセスは拒否されました。"
            )

        self._check_fatal_security_path(resolved_target, is_write=is_write)
        return resolved_target

    def _verify_directory_tree_writable(self, dir_path: Path) -> None:
        """
        ディレクトリ単位の削除・移動を行う際、その配下に保護対象ファイル（.env, .devcontainer, .git 等）が
        含まれていないか再帰的に検査します。
        """
        if not dir_path.is_dir():
            return
        for child in dir_path.rglob("*"):
            if self._is_secret_file(child.name):
                rel_child = child.relative_to(self.project_path)
                raise PermissionError(
                    f"[SECURITY_ALERT] ディレクトリ内に保護対象の機密ファイル '{rel_child}' が含まれているため、"
                    "ディレクトリごとの削除・移動は禁止されています。"
                )
            try:
                resolved_child = child.resolve()
                if not resolved_child.is_relative_to(self.project_path):
                    raise PermissionError(
                        "[SECURITY_ALERT] ディレクトリ内にプロジェクト外を指すシンボリックリンクが含まれているため操作を拒否しました。"
                    )
                self._check_fatal_security_path(resolved_child, is_write=True)
            except PermissionError:
                raise
            except Exception:
                continue

    def write_file(self, file_path: str, content: str) -> str:
        """
        ファイルにテキストを書きこみます。
        ファイルが存在しなければ作成します。
        Args:
            file_path: ファイルのパス。
            content: ファイルに書き込むテキスト。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            resolved_path = self._verify_and_get_path(file_path, is_write=True)

            operation_type = "既存ファイル上書き" if resolved_path.exists() else "新規ファイル作成"
            summary = summarize_text_for_display(content, max_lines=10, max_chars=500)
            action_desc = (
                f"ファイルの書き込み ({operation_type}): {file_path}\n"
                f"  {summary}"
            )

            if not self._confirm(action_desc):
                return (
                    "[ERROR] ユーザーによってファイルの書き込み操作が拒否されました。"
                    "勝手な判断で変更を行わず、一度作業を止めてユーザーに変更内容や意図を説明し、承認を求めてください。"
                )

            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_path.write_text(content, encoding="utf-8")
            return f"成功: '{file_path}' にファイルを書き込みました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] ファイルの書き込み中にエラーが発生しました: {str(e)}"

    def read_file(self, file_path: str) -> str:
        """
        ファイルからテキストを読み込みます。
        Args:
            file_path: ファイルのパス。
        Returns:
            ファイルのコンテンツ、またはエラーメッセージ。
        """
        try:
            resolved_path = self._verify_and_get_path(file_path, is_write=False)
            if not resolved_path.exists():
                return f"[ERROR] ファイルが存在しません: {file_path}"
            if not resolved_path.is_file():
                return f"[ERROR] 指定されたパスはファイルではありません: {file_path}"

            return resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"[ERROR] ファイルの読み込み中にエラーが発生しました: {str(e)}"

    def create_directory(self, dir_path: str) -> str:
        """
        プロジェクト内にディレクトリを作成します。
        親ディレクトリが存在しない場合は同時に作成します。
        Args:
            dir_path: 作成するディレクトリのパス。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            target_path = self._verify_and_get_path(dir_path, is_write=True)
            target_path.mkdir(parents=True, exist_ok=True)
            return f"成功: ディレクトリ '{dir_path}' を作成しました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] ディレクトリの作成中にエラーが発生しました: {str(e)}"

    def delete_path(self, target_path: str) -> str:
        """
        プロジェクト内のファイルまたはディレクトリを削除します。
        ディレクトリの場合は配下のファイルやサブディレクトリも含めて再帰的に削除します。
        Args:
            target_path: 削除するファイルまたはディレクトリのパス。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            path = self._verify_and_get_path(target_path, is_write=True)

            if path == self.project_path:
                return "[ERROR] プロジェクトルート自体の削除は許可されていません。"

            if not path.exists():
                return f"[ERROR] 指定されたパスが存在しません: {target_path}"

            if path.is_dir() and not path.is_symlink():
                self._verify_directory_tree_writable(path)

            target_kind = "ディレクトリ（配下すべて）" if path.is_dir() else "ファイル"
            action_desc = f"ファイル/ディレクトリの削除 ({target_kind}): {target_path}"

            if not self._confirm(action_desc, force_confirm_in_edit=True):
                return (
                    "[ERROR] ユーザーによって削除操作が拒否されました。"
                    "勝手な判断で変更を行わず、一度作業を止めてユーザーに変更内容や意図を説明し、承認を求めてください。"
                )

            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            return f"成功: '{target_path}' を削除しました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] 削除中にエラーが発生しました: {str(e)}"

    def rename_path(self, old_path: str, new_path: str) -> str:
        """
        プロジェクト内のファイルまたはディレクトリをリネーム（名前変更）または移動します。
        Args:
            old_path: 変更前のファイルまたはディレクトリのパス。
            new_path: 変更後（移動先）のファイルまたはディレクトリのパス。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            src_path = self._verify_and_get_path(old_path, is_write=True)
            dst_path = self._verify_and_get_path(new_path, is_write=True)

            if src_path == self.project_path:
                return "[ERROR] プロジェクトルート自体のリネームや移動は許可されていません。"

            if not src_path.exists():
                return f"[ERROR] 変更前のパスが存在しません: {old_path}"

            if src_path.is_dir() and not src_path.is_symlink():
                self._verify_directory_tree_writable(src_path)

            action_desc = f"ファイル/ディレクトリのリネーム・移動: {old_path} -> {new_path}"

            if not self._confirm(action_desc):
                return (
                    "[ERROR] ユーザーによってリネーム/移動操作が拒否されました。"
                    "勝手な判断で変更を行わず、一度作業を止めてユーザーに変更内容や意図を説明し、承認を求めてください。"
                )

            dst_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.rename(dst_path)
            return f"成功: '{old_path}' を '{new_path}' に移動/リネームしました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] パスの変更中にエラーが発生しました: {str(e)}"

    def copy_file(self, src_path: str, dst_path: str) -> str:
        """
        プロジェクト内のファイルを別のパスにコピーします。
        Args:
            src_path: コピー元ファイルのパス。
            dst_path: コピー先ファイルのパス。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            source_path = self._verify_and_get_path(src_path, is_write=False)
            destination_path = self._verify_and_get_path(dst_path, is_write=True)

            if not source_path.exists():
                return f"[ERROR] コピー元ファイルが存在しません: {src_path}"

            if not source_path.is_file():
                return f"[ERROR] コピー元がファイルではありません: {src_path}"

            action_desc = f"ファイルのコピー: {src_path} -> {dst_path}"

            if not self._confirm(action_desc):
                return (
                    "[ERROR] ユーザーによってコピー操作が拒否されました。"
                    "勝手な判断で変更を行わず、一度作業を止めてユーザーに変更内容や意図を説明し、承認を求めてください。"
                )

            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)
            return f"成功: '{src_path}' を '{dst_path}' にコピーしました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] ファイルのコピー中にエラーが発生しました: {str(e)}"

    def search_and_replace_file(self, file_path: str, find_text: str, replace_text: str) -> str:
        """
        ファイル内のテキストを検索し、置換します。
        完全に一致する文字列がファイル内に1箇所だけ存在する場合のみ実行されます。
        Args:
            file_path: 対象ファイルのパス。
            find_text: 置換したい、ファイル内に現存する正確なコードブロック（複数行可）。
            replace_text: 新しく置き換えるコードブロック。
        Returns:
            成功または失敗のメッセージ。
        """
        try:
            self._check_circuit_breaker()
            resolved_path = self._verify_and_get_path(file_path, is_write=True)
            if not resolved_path.exists():
                return f"[ERROR] ファイルが存在しません: {file_path}"

            if not resolved_path.is_file():
                return f"[ERROR] 指定されたパスはファイルではありません: {file_path}"

            content = resolved_path.read_text(encoding="utf-8")

            count = content.count(find_text)
            if count == 0:
                return f"[ERROR] 指定されたテキストがファイル内に見つかりません: {file_path}"
            elif count > 1:
                return f"[ERROR] 指定されたテキストがファイル内に複数箇所（{count}箇所）存在するため、置換できません。"

            find_summary = summarize_text_for_display(find_text, max_lines=5, max_chars=250)
            replace_summary = summarize_text_for_display(replace_text, max_lines=5, max_chars=250)
            action_desc = (
                f"ファイルの部分置換: {file_path}\n"
                f"  [置換前 (Find)] {find_summary}\n"
                f"  [置換後 (Replace)] {replace_summary}"
            )

            if not self._confirm(action_desc):
                return (
                    "[ERROR] ユーザーによって置換操作が拒否されました。"
                    "勝手な判断で変更を行わず、一度作業を止めてユーザーに変更内容や意図を説明し、承認を求めてください。"
                )

            new_content = content.replace(find_text, replace_text, 1)
            resolved_path.write_text(new_content, encoding="utf-8")
            return f"成功: '{file_path}' 内のテキストを置換しました。"
        except AgentExecutionAborted:
            raise
        except Exception as e:
            return f"[ERROR] ファイルの置換中にエラーが発生しました: {str(e)}"

    def _is_safe_visible_entry(self, entry_path: Path) -> tuple[bool, bool]:
        """
        探索系ツール（get_structure, find_files, search_text）において、
        対象エントリが機密保護・除外ルールおよびプロジェクト境界を満たす安全な項目かを判定します。

        Returns:
            (表示・検索対象として安全か, 再帰的に内部を走査してよいディレクトリか)
        """
        name = entry_path.name
        if self._is_ignored_dir_name(name) or name.lower() in SECRET_DIR_NAMES:
            return False, False
        if self._is_secret_file(name):
            return False, False

        try:
            is_symlink = entry_path.is_symlink()
            resolved = entry_path.resolve()
            if not resolved.is_relative_to(self.project_path):
                return False, False
            rel_resolved = resolved.relative_to(self.project_path)
            if self._is_ignored_dir(rel_resolved.parts) or self._is_secret_dir(rel_resolved.parts):
                return False, False
            if self._is_secret_file(resolved.name):
                return False, False
            is_dir = entry_path.is_dir()
            # シンボリックリンクによる無限ループ防止のため、ディレクトリ再帰は非シンボリックリンクに限定
            return True, (is_dir and not is_symlink)
        except Exception:
            return False, False

    def _count_safe_descendants(self, dir_path: Path, include_dirs: bool = True) -> int:
        """
        指定ディレクトリ配下に存在する安全かつ可視なエントリ（除外・機密対象外）の総数を返します。
        """
        count = 0
        stack = [dir_path]
        while stack:
            curr = stack.pop()
            try:
                children = sorted(curr.iterdir(), key=lambda p: p.name)
            except Exception:
                continue
            for child in children:
                is_vis, is_trav_dir = self._is_safe_visible_entry(child)
                if not is_vis:
                    continue
                if include_dirs or not child.is_dir():
                    count += 1
                if is_trav_dir:
                    stack.append(child)
        return count

    @staticmethod
    def _format_omitted_folder_summary(folder_counts: dict[str, int], max_folders: int = 8) -> str:
        """未表示項目が残っているフォルダと件数の内訳文字列を生成します。"""
        if not folder_counts:
            return "なし"
        sorted_items = sorted(folder_counts.items(), key=lambda x: (-x[1], x[0]))
        parts = [f"{folder} ({cnt}件)" for folder, cnt in sorted_items[:max_folders]]
        if len(sorted_items) > max_folders:
            parts.append(f"他 {len(sorted_items) - max_folders} フォルダ")
        return ", ".join(parts)

    def get_structure(
        self,
        target_dir: str = ".",
        max_depth: int = DEFAULT_STRUCTURE_MAX_DEPTH,
        include_dirs: bool = True,
    ) -> list[str] | str:
        """
        プロジェクト内の指定ディレクトリ配下のファイルおよびフォルダ構成を取得します。
        （.git や node_modules, .venv 等の除外領域や機密ファイルは自動除外されます）
        Args:
            target_dir: 調査対象のディレクトリパス（プロジェクトルートからの相対パス、デフォルトは "."）。
            max_depth: 展開する階層の深さ上限（デフォルトは 3）。さらに深い階層があるフォルダには未表示件数が付記されます。
            include_dirs: ディレクトリ自体も一覧に含めるかどうか (デフォルトは True)。
        Returns:
            プロジェクト内のパス（プロジェクトルートからの相対パス）の文字列表現のリスト、またはエラーメッセージ。
        """
        try:
            base_dir = self._verify_and_get_path(target_dir, is_write=False)
            if not base_dir.exists():
                return f"[ERROR] ディレクトリが存在しません: {target_dir}"
            if not base_dir.is_file() and not base_dir.is_dir():
                return f"[ERROR] 指定されたパスはディレクトリではありません: {target_dir}"
            if base_dir.is_file():
                return f"[ERROR] 指定されたパスはディレクトリではありません: {target_dir}"

            rel_base = base_dir.relative_to(self.project_path)
            if rel_base.parts and self._is_ignored_dir(rel_base.parts):
                return f"[ERROR] 除外対象ディレクトリは探索できません: {target_dir}"

            effective_depth = max(1, int(max_depth))
            # 幅優先探索（浅い階層優先）により、上位フォルダが件数上限で消えるのを防止
            queue: list[tuple[Path, int]] = [(base_dir, 0)]
            collected: list[tuple[str, str]] = []

            while queue:
                curr_dir, depth = queue.pop(0)
                try:
                    children = sorted(curr_dir.iterdir(), key=lambda p: p.name)
                except Exception:
                    continue

                for child in children:
                    is_vis, is_trav_dir = self._is_safe_visible_entry(child)
                    if not is_vis:
                        continue

                    child_depth = depth + 1
                    rel_proj = child.relative_to(self.project_path)
                    rel_proj_str = str(rel_proj)
                    rel_base_parts = child.relative_to(base_dir).parts
                    group_dir = (
                        (base_dir / rel_base_parts[0]).relative_to(self.project_path).as_posix()
                        if len(rel_base_parts) > 1 or child.is_dir()
                        else (rel_base.as_posix() if rel_base.parts else ".")
                    )

                    if is_trav_dir:
                        if child_depth < effective_depth:
                            if include_dirs:
                                collected.append((rel_proj_str, group_dir))
                            queue.append((child, child_depth))
                        else:
                            hidden_count = self._count_safe_descendants(child, include_dirs=include_dirs)
                            if hidden_count > 0:
                                norm_rel = rel_proj.as_posix()
                                entry_text = (
                                    f"{rel_proj_str} (※配下に未表示 {hidden_count} 件 "
                                    f"-> target_dir='{norm_rel}' で展開可)"
                                )
                                collected.append((entry_text, group_dir))
                            elif include_dirs:
                                collected.append((rel_proj_str, group_dir))
                    else:
                        if include_dirs or not child.is_dir():
                            collected.append((rel_proj_str, group_dir))

            if len(collected) <= MAX_STRUCTURE_ENTRIES:
                return [item[0] for item in collected]

            shown = [item[0] for item in collected[:MAX_STRUCTURE_ENTRIES]]
            omitted = collected[MAX_STRUCTURE_ENTRIES:]
            folder_counts: dict[str, int] = {}
            for _, grp in omitted:
                folder_counts[grp] = folder_counts.get(grp, 0) + 1
            folder_summary = self._format_omitted_folder_summary(folder_counts)
            shown.append(
                f"[省略通知] 表示上限（{MAX_STRUCTURE_ENTRIES}件）に達したため、残り {len(omitted)} 件を省略しました。"
                f"未表示項目が残っているフォルダ: {folder_summary} "
                "-> 確認するには get_structure(target_dir='フォルダパス') を指定してください。"
            )
            return shown
        except Exception as e:
            return f"[ERROR] ディレクトリ構成の取得中にエラーが発生しました: {str(e)}"

    def find_files(
        self,
        pattern: str = "*",
        target_dir: str = ".",
    ) -> list[str] | str:
        """
        プロジェクト内の指定ディレクトリ配下から、ファイル名またはパスパターン（ワイルドカード可）に
        一致するファイルを検索します。（.git や node_modules 等の除外領域や機密ファイルはスキップされます）
        Args:
            pattern: 検索するファイル名またはグロブパターン（例: "*.py", "*test*.py", "docs/*.md"）。
            target_dir: 検索開始ディレクトリ（プロジェクトルートからの相対パス、デフォルトは "."）。
        Returns:
            一致したファイルの相対パス文字列のリスト（上限超過時は末尾に未表示フォルダ内訳を付与）、
            またはエラーメッセージ文字列。
        """
        try:
            base_dir = self._verify_and_get_path(target_dir, is_write=False)
            if not base_dir.exists():
                return f"[ERROR] ディレクトリが存在しません: {target_dir}"
            if not base_dir.is_dir():
                return f"[ERROR] 指定されたパスはディレクトリではありません: {target_dir}"

            rel_base = base_dir.relative_to(self.project_path)
            if rel_base.parts and self._is_ignored_dir(rel_base.parts):
                return f"[ERROR] 除外対象ディレクトリは探索できません: {target_dir}"

            clean_pat = (pattern or "*").strip().replace("\\", "/")
            pat_lower = clean_pat.lower()
            has_slash = "/" in clean_pat
            has_wildcard = any(ch in pat_lower for ch in ("*", "?", "["))

            queue: list[Path] = [base_dir]
            matched_files: list[tuple[str, str]] = []

            while queue:
                curr_dir = queue.pop(0)
                try:
                    children = sorted(curr_dir.iterdir(), key=lambda p: p.name)
                except Exception:
                    continue

                for child in children:
                    is_vis, is_trav_dir = self._is_safe_visible_entry(child)
                    if not is_vis:
                        continue
                    if is_trav_dir:
                        queue.append(child)
                        continue
                    if not child.is_file():
                        continue

                    rel_proj = child.relative_to(self.project_path)
                    rel_proj_posix = rel_proj.as_posix()
                    rel_base_child = child.relative_to(base_dir)
                    rel_base_posix = rel_base_child.as_posix()
                    fname_lower = child.name.lower()

                    if has_slash:
                        is_match = (
                            fnmatch.fnmatchcase(rel_proj_posix.lower(), pat_lower)
                            or fnmatch.fnmatchcase(rel_base_posix.lower(), pat_lower)
                        )
                    elif has_wildcard:
                        is_match = fnmatch.fnmatchcase(fname_lower, pat_lower)
                    else:
                        is_match = pat_lower in fname_lower

                    if is_match:
                        rel_parts = rel_base_child.parts
                        group_dir = (
                            (base_dir / rel_parts[0]).relative_to(self.project_path).as_posix()
                            if len(rel_parts) > 1
                            else (rel_base.as_posix() if rel_base.parts else ".")
                        )
                        matched_files.append((str(rel_proj), group_dir))

            if len(matched_files) <= MAX_FIND_RESULTS:
                return [item[0] for item in matched_files]

            shown = [item[0] for item in matched_files[:MAX_FIND_RESULTS]]
            omitted = matched_files[MAX_FIND_RESULTS:]
            folder_counts: dict[str, int] = {}
            for _, grp in omitted:
                folder_counts[grp] = folder_counts.get(grp, 0) + 1
            folder_summary = self._format_omitted_folder_summary(folder_counts)
            shown.append(
                f"[省略通知] 全 {len(matched_files)} 件中、先頭 {MAX_FIND_RESULTS} 件を表示しました。"
                f"未表示の {len(omitted)} 件が残っているフォルダ: {folder_summary} "
                f"-> 漏れなく確認するには find_files(pattern='{pattern}', target_dir='フォルダパス') を指定してください。"
            )
            return shown
        except Exception as e:
            return f"[ERROR] ファイルの検索中にエラーが発生しました: {str(e)}"

    def get_file_metadata(self, file_path: str) -> dict | str:
        """
        ファイルの更新確認や状態管理用のメタデータ（最終更新日時、サイズ、ハッシュ値など）を取得します。
        AIエージェントがファイル内容を再読み込みする必要があるか判断するのに役立ちます。
        Args:
            file_path: 対象ファイルのパス。
        Returns:
            メタデータの辞書（mtime, size, md5）、またはエラーメッセージの文字列。
        """
        try:
            resolved_path = self._verify_and_get_path(file_path, is_write=False)
            if not resolved_path.exists():
                return f"[ERROR] ファイルが存在しません: {file_path}"

            if not resolved_path.is_file():
                return f"[ERROR] 指定されたパスはファイルではありません: {file_path}"

            stat = resolved_path.stat()

            hasher = hashlib.md5()
            with open(resolved_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hasher.update(chunk)
            file_hash = hasher.hexdigest()

            return {
                "file_path": str(resolved_path.relative_to(self.project_path)),
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "md5": file_hash,
            }
        except Exception as e:
            return f"[ERROR] ファイルメタデータの取得中にエラーが発生しました: {str(e)}"

    def search_text(self, pattern: str, target_dir: str = ".") -> list[dict] | str:
        """
        プロジェクト内のファイルを対象に、指定された正規表現パターンに一致する箇所を検索します。
        （機密ファイルや .git, node_modules 等の除外ディレクトリはスキップされます）
        Args:
            pattern: 検索する正規表現パターン（Pythonのreモジュール準拠）。
            target_dir: 検索対象ディレクトリ（プロジェクトルートからの相対パス、デフォルトは "."）。
        Returns:
            マッチした箇所の情報（ファイルパス、行番号、一致した行の文字列など）のリスト
            （上限超過時は末尾に未表示フォルダ内訳の notice 辞書を含む）、またはエラーメッセージ。
        """
        try:
            base_dir = self._verify_and_get_path(target_dir, is_write=False)
            if not base_dir.exists():
                return f"[ERROR] ディレクトリが存在しません: {target_dir}"
            if not base_dir.is_dir():
                return f"[ERROR] 指定されたパスはディレクトリではありません: {target_dir}"

            rel_base = base_dir.relative_to(self.project_path)
            if rel_base.parts and self._is_ignored_dir(rel_base.parts):
                return f"[ERROR] 除外対象ディレクトリは探索できません: {target_dir}"

            compiled_pattern = re.compile(pattern)
            results: list[dict] = []
            omitted_counts: dict[str, int] = {}
            total_omitted = 0

            queue: list[Path] = [base_dir]
            while queue:
                curr_dir = queue.pop(0)
                try:
                    children = sorted(curr_dir.iterdir(), key=lambda p: p.name)
                except Exception:
                    continue

                for child in children:
                    is_vis, is_trav_dir = self._is_safe_visible_entry(child)
                    if not is_vis:
                        continue
                    if is_trav_dir:
                        queue.append(child)
                        continue
                    if not child.is_file():
                        continue

                    try:
                        resolved = child.resolve()
                        content = resolved.read_text(encoding="utf-8")
                    except Exception:
                        continue

                    rel_proj = child.relative_to(self.project_path)
                    rel_path_str = str(rel_proj)
                    rel_parts = child.relative_to(base_dir).parts
                    group_dir = (
                        (base_dir / rel_parts[0]).relative_to(self.project_path).as_posix()
                        if len(rel_parts) > 1
                        else (rel_base.as_posix() if rel_base.parts else ".")
                    )

                    lines = content.splitlines()
                    for line_num, line_str in enumerate(lines):
                        if compiled_pattern.search(line_str):
                            if len(results) < MAX_SEARCH_RESULTS:
                                results.append({
                                    "file_path": rel_path_str,
                                    "line_number": line_num,
                                    "line_str": line_str,
                                })
                            else:
                                omitted_counts[group_dir] = omitted_counts.get(group_dir, 0) + 1
                                total_omitted += 1

            if total_omitted > 0:
                total_matches = MAX_SEARCH_RESULTS + total_omitted
                folder_summary = self._format_omitted_folder_summary(omitted_counts)
                results.append({
                    "notice": (
                        f"[省略通知] 全 {total_matches} 件中、先頭 {MAX_SEARCH_RESULTS} 件を表示しました。"
                        f"未表示の {total_omitted} 件が残っているフォルダ: {folder_summary} "
                        f"-> 漏れなく確認するには search_text(pattern='{pattern}', target_dir='フォルダパス') を指定してください。"
                    )
                })

            return results
        except re.error as e:
            return f"[ERROR] 無効な正規表現パターンです: {str(e)}"
        except Exception as e:
            return f"[ERROR] テキストの検索中にエラーが発生しました: {str(e)}"

    def read_files_batch(self, file_paths: list[str]) -> dict[str, str]:
        """
        複数のファイルを一括して読み込み、ファイルパスとコンテンツの辞書を返します。
        Args:
            file_paths: 読み込むファイルパスのリスト。
        Returns:
            {ファイルパス: ファイルコンテンツ} の辞書。存在しないまたはエラーのファイルはエラーメッセージを格納。
        """
        results = {}
        for file_path in file_paths:
            try:
                resolved_path = self._verify_and_get_path(file_path, is_write=False)
                if not resolved_path.exists():
                    results[file_path] = f"[ERROR] ファイルが存在しません: {file_path}"
                elif not resolved_path.is_file():
                    results[file_path] = f"[ERROR] 指定されたパスはファイルではありません: {file_path}"
                else:
                    results[file_path] = resolved_path.read_text(encoding="utf-8")
            except Exception as e:
                results[file_path] = f"[ERROR] 読み込み中にエラーが発生しました: {str(e)}"
        return results

    def preview_file(self, file_path: str, start_line: int = 0, end_line: int = 50) -> str:
        """
        ファイルの指定した行範囲（スニペット）をプレビュー取得します。
        Args:
            file_path: 対象ファイルのパス。
            start_line: 開始行番号（0始まり、デフォルトは0）。
            end_line: 終了行番号（0始まり、デフォルトは50、この行の手前まで取得）。
        Returns:
            指定範囲のファイルコンテンツ、またはエラーメッセージ。
        """
        try:
            resolved_path = self._verify_and_get_path(file_path, is_write=False)
            if not resolved_path.exists():
                return f"[ERROR] ファイルが存在しません: {file_path}"

            if not resolved_path.is_file():
                return f"[ERROR] 指定されたパスはファイルではありません: {file_path}"

            lines = resolved_path.read_text(encoding="utf-8").splitlines()

            start = max(0, start_line)
            end = max(start, end_line)

            preview_lines = lines[start:end]
            return "\n".join(preview_lines)
        except Exception as e:
            return f"[ERROR] ファイルのプレビュー取得中にエラーが発生しました: {str(e)}"

    def syntax_check(self, module_file_path: str) -> dict | str:
        """
        指定されたPythonファイルの構文チェック（ast.parseによる静的検証）を行います。
        外部プロセスを起動せず安全に文法エラーを検査します。
        Args:
            module_file_path: 対象のPythonファイルパス。
        Returns:
            構文が正常な場合は {'result': True, 'details': None}、
            構文エラーの場合はエラー詳細情報の辞書、またはエラーメッセージ文字列。
        """
        try:
            resolved_path = self._verify_and_get_path(module_file_path, is_write=False)
            if not resolved_path.exists():
                return f"[ERROR] ファイルが存在しません: {module_file_path}"
            if not resolved_path.is_file():
                return f"[ERROR] 指定されたパスはファイルではありません: {module_file_path}"

            code = resolved_path.read_text(encoding="utf-8")
            try:
                ast.parse(code, filename=module_file_path)
                return {"result": True, "details": None}
            except SyntaxError as e:
                return {
                    "result": False,
                    "details": {
                        "error_type": type(e).__name__,
                        "message": e.msg,
                        "lineno": e.lineno,
                        "offset": e.offset,
                        "text": e.text,
                    },
                }
        except Exception as e:
            return f"[ERROR] 構文チェックの実行に失敗しました: {str(e)}"

    def ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """
        作業中に仕様の曖昧さ、複数の実装方針の選択肢、作業環境の不備（必要なツールの未インストール等）、
        または操作が承認拒否された後の代替方針について、ユーザーに質問・相談し判断や指示を取得します。
        推測や無茶な手段で作業を強行する前にこのツールを使用してください。

        Args:
            question: ユーザーに確認・質問したい具体的な内容。
            options: ユーザーに提示する選択肢の文字列リスト（任意。自由回答のみを求める場合は省略可）。
                     UI側で自動的に「その他（任意の指示を入力）」も選択可能になります。
            context: 質問の背景、現在の分析状況、またはなぜこの確認が必要なのかの説明（任意）。

        Returns:
            ユーザーが選択した選択肢、または入力した回答・指示の文字列。
        """
        # ユーザーに質問・相談を行ったため、承認拒否保留状態（サーキットブレーカー）をリセット
        self._rejection_pending = False

        if self.ask_user_callback is not None:
            return self.ask_user_callback(question, options, context)

        print("\n" + "=" * 50)
        print("[Gemioからの確認・質問]")
        if context:
            print(f"背景: {context}")
        print(f"質問: {question}")
        if options:
            print("\n選択肢:")
            for idx, opt in enumerate(options, 1):
                print(f"  [{idx}] {opt}")
            print("  [0] その他（任意の指示を入力）")
        print("  [a] 作業を強制中止 (Abort)")
        print("=" * 50)

        while True:
            ans = input("番号を選択、または直接回答・指示を入力してください: ").strip()
            if not ans:
                continue
            if ans.lower() in ["a", "abort"]:
                raise AgentExecutionAborted(
                    "ユーザーによって作業の強制中止（Abort）が選択されました。"
                )
            if options:
                if ans == "0":
                    custom = input("回答・指示を入力してください: ").strip()
                    if custom.lower() in ["a", "abort"]:
                        raise AgentExecutionAborted(
                            "ユーザーによって作業の強制中止（Abort）が選択されました。"
                        )
                    if custom:
                        return custom
                    continue
                if ans.isdigit():
                    choice_idx = int(ans)
                    if 1 <= choice_idx <= len(options):
                        return options[choice_idx - 1]
            return ans

    def agent_tools(self) -> list[Callable]:
        """AIエージェントに提供する基本ツール関数のリストを返します。"""
        return [
            self.write_file,
            self.read_file,
            self.create_directory,
            self.delete_path,
            self.rename_path,
            self.copy_file,
            self.search_and_replace_file,
            self.get_structure,
            self.find_files,
            self.get_file_metadata,
            self.search_text,
            self.read_files_batch,
            self.preview_file,
            self.syntax_check,
            self.ask_user,
        ]

    def agent_toolsets(self) -> list[Toolset]:
        """AIエージェントに提供するツール群を論理ツールセット（Toolset）のリストとして返します。"""
        return [
            Toolset.from_callables(
                name="project_interaction",
                description="仕様確認や作業方針の相談をユーザーへ行う対話ツールセット",
                funcs=[self.ask_user],
                always_active=True,
            ),
            Toolset.from_callables(
                name="project_explore",
                description="プロジェクト内のフォルダ構成・ファイル名・テキスト・メタデータを探索するツールセット",
                funcs=[
                    self.get_structure,
                    self.find_files,
                    self.search_text,
                    self.get_file_metadata,
                ],
                always_active=False,
            ),
            Toolset.from_callables(
                name="project_read",
                description="プロジェクト内ファイルの読み込み・範囲プレビュー・Python構文検証を行うツールセット",
                funcs=[
                    self.read_file,
                    self.read_files_batch,
                    self.preview_file,
                    self.syntax_check,
                ],
                always_active=False,
            ),
            Toolset.from_callables(
                name="project_write",
                description="ファイルやディレクトリの作成・編集・置換・移動・コピー・削除を行う変更ツールセット",
                funcs=[
                    self.write_file,
                    self.search_and_replace_file,
                    self.create_directory,
                    self.rename_path,
                    self.copy_file,
                    self.delete_path,
                ],
                always_active=False,
            ),
        ]
