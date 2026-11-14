from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable

from gemio.core.models import AgentLogCategory
from gemio.project import Project


class GemioUI(ABC):
    """
    Gemio CLIのUIバックエンド抽象基底クラス。

    現行のストリーム型コンソールUI（RichConsoleUI）と、将来移行するフルスクリーンTUI（TextualUI）の
    双方を同一のインターフェースで切り替えられるようにします。
    「通常対話表示（ChatAgent向け）」と「検索対話表示（SearchAgent向け）」の
    インターフェースを明確に分離しています。
    """

    # =====================================================================
    # 1. ステータス・スピナー制御（コンテキストマネージャ）
    # =====================================================================

    @abstractmethod
    def thinking_status(self) -> AbstractContextManager[Any]:
        """AI思考・ツール実行中のステータス表示（スピナー等）を行うコンテキストマネージャ。"""

    @abstractmethod
    def search_status(self, is_subsequent: bool = False) -> AbstractContextManager[Any]:
        """Web検索実行中のステータス表示（スピナー等）を行うコンテキストマネージャ。"""

    @abstractmethod
    def pause_status(self) -> AbstractContextManager[None]:
        """対話プロンプト表示中に進行中ステータス（スピナー等）を一時停止するコンテキストマネージャ。"""

    # =====================================================================
    # 2. ラベル・ヘッダー整形および単発メッセージ出力
    # =====================================================================

    @abstractmethod
    def format_dev_mode_label(self, project: Project) -> str:
        """ウェルカムバナー等に表示する動作モードの装飾ラベル文字列を返します。"""

    @abstractmethod
    def print_cli_error(self, message: str) -> None:
        """起動時エラー等のCLIエラーメッセージを出力します。"""

    @abstractmethod
    def print_dim_message(self, message: str) -> None:
        """補足・キャンセル等の控えめなメッセージを出力します。"""

    @abstractmethod
    def print_exit_message(self) -> None:
        """CLI終了時の挨拶メッセージを出力します。"""

    @abstractmethod
    def print_interrupt_exit_message(self) -> None:
        """AI処理中のCtrl+C割り込みに伴う強制停止・終了メッセージを出力します。"""

    @abstractmethod
    def print_chat_divider(self) -> None:
        """対話ターン終了時の区切り線を出力します。"""

    @abstractmethod
    def print_initial_approval_mode_notice(
        self,
        mode: str,
        fallback_from_trust: bool = False,
    ) -> None:
        """起動時の初期承認モード決定に伴う通知メッセージを出力します。"""

    # =====================================================================
    # 3. 情報・設定変更パネル表示
    # =====================================================================

    @abstractmethod
    def print_welcome_banner(
        self,
        project_path: Path,
        current_model: str,
        trust_mode: bool = False,
        dev_mode_label: str = "基本モード (Standard)",
        approval_mode: str | None = None,
    ) -> None:
        """起動時のウェルカムバナーを表示します。"""

    @abstractmethod
    def print_help_panel(self) -> None:
        """詳細な操作ガイド・コマンド一覧パネルを表示します。"""

    @abstractmethod
    def print_sandbox_status_panel(self, project: Project) -> None:
        """サンドボックス環境設定およびマウント保護状態の診断パネルを表示します。"""

    @abstractmethod
    def print_token_usage_panel(self, token_usage: dict[str, int]) -> None:
        """現在のコンテキスト量およびセッション累積トークン消費量の詳細パネルを表示します。"""

    @abstractmethod
    def print_session_reset_panel(
        self,
        reset_summary: dict[str, int],
        clear_materials: bool = False,
    ) -> None:
        """対話セッションのリセット完了通知パネルを表示します。"""

    @abstractmethod
    def print_edit_mode_changed_panel(self, enabled: bool) -> None:
        """エディットモードの有効化・無効化に伴う通知パネルを表示します。"""

    @abstractmethod
    def print_trust_mode_changed_panel(self, enabled: bool) -> None:
        """トラストモードの有効化・無効化に伴う通知パネルを表示します。"""

    @abstractmethod
    def print_model_changed_panel(self, model_name: str) -> None:
        """AIモデル切替完了の通知パネルを表示します。"""

    # =====================================================================
    # 4. 通常対話ログ & エラー表示
    # =====================================================================

    @abstractmethod
    def print_agent_log_panel(
        self,
        log_message: str,
        category: str | None = None,
    ) -> None:
        """
        エージェントの対話・ツール呼び出しログを表示します。
        category が省略された場合はメッセージ内容から自動判定します。
        """

    @abstractmethod
    def print_sandbox_security_error_panel(self, error: Exception) -> None:
        """開発者モード起動時のサンドボックス安全性検証エラーパネルを表示します。"""

    @abstractmethod
    def print_unexpected_error_panel(self, error: Exception) -> None:
        """通常プロンプト実行中の予期せぬエラーパネルを表示します。"""

    # =====================================================================
    # 5. 検索対話表示 & エラー表示
    # =====================================================================

    @abstractmethod
    def print_search_materials_cleared_panel(self, cleared_count: int) -> None:
        """保持していた作業前提資料の破棄完了パネルを表示します。"""

    @abstractmethod
    def print_search_help_panel(self) -> None:
        """Web検索コマンド（/search）の使い方パネルを表示します。"""

    @abstractmethod
    def print_search_response_panel(
        self,
        response_text: str,
        sources: list[dict[str, str]],
        now_time_text: str,
        total_materials: int,
    ) -> None:
        """Web検索の回答テキスト・参照元リンク・前提資料保持案内を表示します。"""

    @abstractmethod
    def print_search_error_panel(self, error: Exception) -> None:
        """Web検索実行中の予期せぬエラーパネルを表示します。"""

    # =====================================================================
    # 6. 対話的確認・選択・プロンプト入力
    # =====================================================================

    @abstractmethod
    def create_prompt_session(self) -> Any:
        """対話プロンプト入力用のセッションオブジェクトを作成します。"""

    @abstractmethod
    def prompt_user_input(
        self,
        session: Any,
        project: Project,
        token_usage: dict[str, int] | None = None,
    ) -> str:
        """ステータス表示付きのユーザー入力欄からプロンプト文字列を取得します。"""

    @abstractmethod
    def request_trust_mode_consent(self) -> bool:
        """トラストモードを有効化する前のユーザー同意確認を行います。"""

    @abstractmethod
    def prompt_api_key_for_container(self, prompt_fn: Callable | None = None) -> str:
        """コンテナ内起動モードにおいてユーザーから安全にAPIキーを受け取ります。"""

    @abstractmethod
    def prompt_approval_choice(self, action_description: str) -> str:
        """破壊的操作実行前の承認要求を表示し、ユーザーの選択（y / n / a / k）を取得します。"""

    @abstractmethod
    def prompt_iteration_limit_continue(self) -> tuple[bool, int]:
        """連続ツール呼び出し上限到達時に、継続可否と追加ループ回数を問い合わせます。"""

    @abstractmethod
    def prompt_ask_user_response(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """AIからの質問・確認要求を表示し、ユーザーの選択または自由記述回答を取得します。"""

    @abstractmethod
    def prompt_model_selection(self, current_model: str) -> str | None:
        """プリセットモデル一覧を表示し、ユーザーが選択したモデル名を返します。"""
