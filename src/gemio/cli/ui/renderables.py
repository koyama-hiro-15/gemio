import html
import re
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from gemio.cli.ui.base import AgentLogCategory
from gemio.core.models import (
    CONTEXT_TOKEN_DANGER_THRESHOLD,
    CONTEXT_TOKEN_WARNING_THRESHOLD,
    MODEL_PRESETS,
)
from gemio.project import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_TRUST,
    DeveloperProject,
    Project,
)

_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\r?\n")


# =====================================================================
# 1. フォーマット文字列生成 & ログ分類ヘルパー
# =====================================================================

def format_prompt_header(
    project: Project,
    token_usage: dict[str, int] | None = None,
) -> str:
    """
    プロンプト入力欄の上部に表示するHTMLフォーマット文字列を生成します。
    projectインスタンスの状態を直接参照するため、/edit や /trust 切替時も自動で最新状態が反映されます。
    token_usage が渡された場合は、現在のコンテキスト量とセッション累積トークン量もあわせて表示します。
    """
    escaped_path = html.escape(str(project.path))

    # 1. 動作モードの判定
    if isinstance(project, DeveloperProject):
        if project.execution_mode == "in_container":
            mode_html = "<ansimagenta><b>開発者モード (Dev Container内実行)</b></ansimagenta>"
        else:
            escaped_container = html.escape(str(project.container_name))
            mode_html = f"<ansimagenta><b>開発者モード (Docker: {escaped_container})</b></ansimagenta>"
    else:
        mode_html = "<ansigreen><b>基本モード (Standard)</b></ansigreen>"

    # 2. 承認モードの判定
    if project.trust_mode:
        approval_html = "<ansired><b>トラストモード (自動承認)</b></ansired>"
    elif project.edit_mode:
        approval_html = "<ansiyellow><b>エディットモード (編集自動 / 削除・コマンド確認)</b></ansiyellow>"
    else:
        approval_html = "<ansigreen><b>通常承認モード (都度確認)</b></ansigreen>"

    # 3. トークン使用量の判定（指定時のみ）
    token_line = ""
    if token_usage is not None:
        context_tokens = int(token_usage.get("last_prompt_tokens", 0))
        cumulative_tokens = int(token_usage.get("cumulative_total_tokens", 0))
        total_calls = int(token_usage.get("total_api_calls", 0))

        if context_tokens >= CONTEXT_TOKEN_DANGER_THRESHOLD:
            ctx_color = "ansired"
        elif context_tokens >= CONTEXT_TOKEN_WARNING_THRESHOLD:
            ctx_color = "ansiyellow"
        else:
            ctx_color = "ansigreen"

        token_line = (
            f"<ansiblue><b>tokens:</b></ansiblue> "
            f"context <{ctx_color}><b>{context_tokens:,}</b></{ctx_color}> / "
            f"cumulative <b>{cumulative_tokens:,}</b> (calls: {total_calls})\n"
        )

    return (
        f"<ansiblue><b>project:</b></ansiblue> {escaped_path}\n"
        f"<ansiblue><b>mode:</b></ansiblue> {mode_html}  |  "
        f"<ansiblue><b>approval:</b></ansiblue> {approval_html}\n"
        f"{token_line}"
        "<ansigreen><b>User &gt;</b></ansigreen> (Esc+Enterで送信 / Esc+Backspaceでクリア):\n"
    )


def format_dev_mode_label(project: Project) -> str:
    """ウェルカムバナー等に表示する動作モードの装飾ラベル文字列を生成します。"""
    if isinstance(project, DeveloperProject):
        if project.execution_mode == "in_container":
            return "[bold magenta]開発者モード (Dev Container内実行)[/bold magenta]"
        return f"[bold magenta]開発者モード (Docker: {escape(str(project.container_name))})[/bold magenta]"
    return "[bold green]基本モード (Standard)[/bold green]"


def classify_agent_log(log_message: str) -> str:
    """
    エージェントから渡されたログ文字列の内容を解析し、ログ種別（AgentLogCategory）を返します。
    Textual移行時に「対話ログペイン」と「ツール実行・ステータスログペイン」へ振り分ける際に使用します。
    """
    body = _TIMESTAMP_PREFIX_RE.sub("", log_message.strip(), count=1).strip()
    if body.startswith("Tool call:"):
        return AgentLogCategory.TOOL
    if body.startswith("[警告]") or body.startswith("-> "):
        return AgentLogCategory.WARNING
    if body.startswith("🚨 【作業の強制中止】"):
        return AgentLogCategory.ABORT
    if body.startswith("[Search]"):
        return AgentLogCategory.SEARCH
    return AgentLogCategory.ASSISTANT


# =====================================================================
# 2. 情報・設定変更パネル生成 (Rich Renderable Builders)
# =====================================================================

def build_welcome_panel(
    project_path: Path,
    current_model: str,
    trust_mode: bool = False,
    dev_mode_label: str = "基本モード (Standard)",
    approval_mode: str | None = None,
) -> Panel:
    """起動時のウェルカムバナーパネルを生成して返します。"""
    eff_mode = approval_mode or (APPROVAL_MODE_TRUST if trust_mode else APPROVAL_MODE_NORMAL)
    if eff_mode == APPROVAL_MODE_TRUST:
        approval_label = "[bold red]トラストモード（自動承認）[/bold red]"
    elif eff_mode == APPROVAL_MODE_EDIT:
        approval_label = "[bold yellow]エディットモード（編集自動 / 削除・コマンド確認）[/bold yellow]"
    else:
        approval_label = "[bold green]通常承認モード（都度確認）[/bold green]"

    welcome_text = (
        f"🤖 [bold green]Welcome to Gemio![/bold green] あなたの作業と開発をサポートします。\n\n"
        f"  • [bold cyan]対象プロジェクト:[/bold cyan] {project_path}\n"
        f"  • [bold cyan]動作モード      :[/bold cyan] {dev_mode_label}\n"
        f"  • [bold cyan]使用AIモデル    :[/bold cyan] {current_model}\n"
        f"  • [bold cyan]承認モード      :[/bold cyan] {approval_label}\n\n"
        "[bold yellow]【基本操作】[/bold yellow]\n"
        "  • 送信   : [bold green][Esc][/bold green] を押した後に [bold green][Enter][/bold green]\n"
        "  • 改行   : [bold green][Enter][/bold green]\n"
        "  • クリア : [bold green][Esc][/bold green] を押した後に [bold green][Backspace][/bold green]\n\n"
        "[dim]💡 コマンド一覧は [bold cyan]'/help'[/bold cyan]、トークン使用量は [bold cyan]'/tokens'[/bold cyan]、新セッション切替は [bold cyan]'/clear'[/bold cyan]、サンドボックス診断は [bold cyan]'/sandbox'[/bold cyan] で確認できます。[/dim]"
    )
    return Panel(welcome_text, title="[bold cyan]✨ Gemio AI Assistant ✨[/bold cyan]", border_style="cyan")


def build_help_panel() -> Panel:
    """詳細な操作ガイド・コマンド一覧パネルを生成して返します。"""
    help_text = (
        "[bold yellow]【基本操作】[/bold yellow]\n"
        "  • [bold]送信[/bold]   : [bold green][Esc][/bold green] を押してから [bold green][Enter][/bold green]\n"
        "  • [bold]改行[/bold]   : [bold green][Enter][/bold green] （複数行の入力が可能です）\n"
        "  • [bold]クリア[/bold] : [bold green][Esc][/bold green] を押してから [bold green][Backspace][/bold green] （入力欄を全消去します）\n"
        "  • [bold]中断[/bold]   : [bold green][Ctrl+C][/bold green] （プロンプト入力中は入力キャンセル / AI処理中は即プログラム終了）\n\n"
        "[bold yellow]【コマンド一覧】[/bold yellow]\n"
        "  • [bold cyan]/help[/bold cyan], [bold cyan]/h[/bold cyan], [bold cyan]help[/bold cyan], [bold cyan]/info[/bold cyan]\n"
        "      この操作ガイドを表示します。\n"
        "  • [bold cyan]/tokens[/bold cyan], [bold cyan]/usage[/bold cyan], [bold cyan]/token[/bold cyan]\n"
        "      現在のコンテキスト量（セッション切替の目安）と、セッション累積トークン消費量の内訳を表示します。\n"
        "  • [bold cyan]/clear[/bold cyan], [bold cyan]/reset[/bold cyan], [bold cyan]/new[/bold cyan] (または [bold cyan]/clear --all[/bold cyan] / [bold cyan]-a[/bold cyan])\n"
        "      会話履歴とトークン統計をリセットして新しい対話セッションを開始します。\n"
        "      [dim](※ --all / -a 指定時は保持している作業前提資料も同時に破棄します)[/dim]\n"
        "  • [bold cyan]/sandbox[/bold cyan], [bold cyan]/status[/bold cyan], [bold cyan]/container[/bold cyan]\n"
        "      サンドボックスコンテナの設定（ネットワーク・ユーザー・リソース制限）および\n"
        "      マウントされているファイル・フォルダのアクセス権（ls -la 相当）と保護状態を検査・表示します。\n"
        "  • [bold cyan]/model[/bold cyan] または [bold cyan]/model <モデル名>[/bold cyan]\n"
        "      AIモデルを対話メニューまたは直接指定で切り替えます。\n"
        "      (例: /model argon, /model 3.8, /model 3.5-lite)\n"
        "  • [bold cyan]/edit[/bold cyan] (または [bold cyan]/edit on[/bold cyan] / [bold cyan]/edit off[/bold cyan])\n"
        "      エディットモード（ファイル作成・編集・移動・コピーは自動承認、削除とコマンド実行は都度確認）と\n"
        "      通常承認モードを切り替えます。\n"
        "  • [bold cyan]/trust[/bold cyan] (または [bold cyan]/trust on[/bold cyan] / [bold cyan]/trust off[/bold cyan])\n"
        "      トラストモード（信頼済みプロジェクトで変更操作を自動承認）と通常承認モードを切り替えます。\n"
        "      [dim](※互換コマンドとして /auto も利用可能です)[/dim]\n"
        "  • [bold cyan]/search <内容>[/bold cyan] または [bold cyan]/s <内容>[/bold cyan]\n"
        "      Web検索を実行し、最新情報を「作業前提資料」として取得します。\n"
        "      [dim]オプション: -n/--new (新規検索), -c/--clear (資料破棄)[/dim]\n"
        "  • [bold red]/exit[/bold red], [bold red]exit[/bold red], [bold red]quit[/bold red]\n"
        "      CLIセッションを終了します。\n\n"
        "[bold yellow]【起動モード & 承認アクション】[/bold yellow]\n"
        "  • 通常起動は「基本モード（ファイル操作＋静的構文チェック）」、`--dev` 指定時は「開発者モード（サンドボックス内シェル実行対応）」で動作します。\n"
        "  • 承認確認時は以下の4つから選択できます:\n"
        "      [bold green]y[/bold green]: 許可 / [bold yellow]n[/bold yellow]: 拒否 / [bold red]a[/bold red]: 作業を強制中止(Abort) / [bold red]k[/bold red]: プロセス即時終了(Kill)\n"
        "  • 承認拒否後にAIが報告せず変更を強行しようとした場合、サーキットブレーカーにより自動中止されます。"
    )
    return Panel(help_text, title="[bold cyan]📖 Gemio 操作ガイド & コマンド一覧[/bold cyan]", border_style="cyan")


def build_sandbox_status_panel(project: Project) -> Panel:
    """
    サンドボックスコンテナの環境設定、マウント状態、ワークスペース内ファイル・フォルダの
    アクセス権（ls -la 相当）および機密ファイル保護状態の診断パネルを生成して返します。
    """
    if not isinstance(project, DeveloperProject):
        if project.trust_mode:
            trust_str = "トラストモード（自動承認）"
        elif project.edit_mode:
            trust_str = "エディットモード（編集自動 / 削除確認）"
        else:
            trust_str = "通常承認モード（都度確認）"
        info_text = (
            "[bold green]現在は「基本モード (Standard Mode)」で動作しています。[/bold green]\n\n"
            f"  • [bold cyan]対象プロジェクト:[/bold cyan] {escape(project.path)}\n"
            f"  • [bold cyan]承認モード      :[/bold cyan] {trust_str}\n"
            "  • [bold cyan]シェルコマンド  :[/bold cyan] [bold green]無効（外部プロセス起動なし・ファイル操作のみ）[/bold green]\n"
            "  • [bold cyan]保護ガードレール:[/bold cyan] `.env`、秘密鍵（`.ssh/`, `*.pem`, `*.key` 等）、クラウド認証情報、`.git/` へのアクセスはシステムレベルで常時ブロックされています。\n\n"
            "[dim]※ Dockerサンドボックス内でコマンド実行やマウント状態診断を行うには `--dev` オプションを付けて起動してください。[/dim]"
        )
        return Panel(info_text, title="[bold cyan]🛡️ ワークスペース保護状態 (基本モード)[/bold cyan]", border_style="cyan")

    status = project.inspect_sandbox_status()
    exec_mode = status.get("execution_mode", "")
    mode_desc = (
        f"Host-Orchestrated (Docker: {escape(str(status.get('container_name', '')))})"
        if exec_mode == "host_docker"
        else "In-Container (Dev Container内直接実行)"
    )

    user_info = status.get("user") or {}
    user_str = escape(str(user_info.get("user_str", "不明")))
    if user_info.get("is_root"):
        user_badge = f"[bold yellow]⚠️  {user_str} (root実行)[/bold yellow]"
    else:
        user_badge = f"[bold green]🟢 {user_str} (非特権ユーザー)[/bold green]"

    net_info = status.get("network") or {}
    net_mode = escape(str(net_info.get("mode", "unknown")))
    if net_info.get("is_isolated"):
        net_badge = f"[bold green]🟢 完全遮断 ({net_mode})[/bold green]"
    else:
        net_badge = f"[bold yellow]🟡 通信可能 ({net_mode})[/bold yellow]"

    res_info = status.get("resources") or {}
    rootfs_str = (
        "[bold green]🟢 Read-Only (--read-only)[/bold green]"
        if res_info.get("readonly_rootfs")
        else "[dim]Read-Write (標準)[/dim]"
    )
    mem_str = escape(str(res_info.get("memory_limit", "無制限")))
    cpu_str = escape(str(res_info.get("cpus", "無制限")))
    pids_str = escape(str(res_info.get("pids_limit", "無制限")))

    ws_dir = escape(str(status.get("workspace_dir", "")))
    ws_rw_label = "rw (読み書き可)" if status.get("workspace_rw", True) else "ro (読取専用)"

    sensitive_env_keys = status.get("sensitive_env_keys") or []
    if sensitive_env_keys:
        keys_joined = escape(", ".join(str(k) for k in sensitive_env_keys))
        env_badge = f"[bold red]🔴 機密キー検出あり ({keys_joined})[/bold red]"
    else:
        env_badge = "[bold green]🟢 機密キー検出なし[/bold green]"

    header_summary = (
        f"[bold yellow]【1. コンテナ基本設定】[/bold yellow]\n"
        f"  • [bold cyan]接続アーキテクチャ:[/bold cyan] {mode_desc}\n"
        f"  • [bold cyan]コンテナ作業パス  :[/bold cyan] {ws_dir} ({ws_rw_label})\n"
        f"  • [bold cyan]実行ユーザー (id) :[/bold cyan] {user_badge}\n"
        f"  • [bold cyan]ネットワーク設定  :[/bold cyan] {net_badge}\n"
        f"  • [bold cyan]コンテナ環境変数  :[/bold cyan] {env_badge}\n"
        f"  • [bold cyan]ルートFS / 制限   :[/bold cyan] {rootfs_str} | Mem: {mem_str} | CPU: {cpu_str} | PIDs: {pids_str}\n"
    )

    table = Table(
        title="【2. ワークスペース内のファイル・フォルダ & マウント保護状態 (ls -la 相当)】",
        title_justify="left",
        title_style="bold yellow",
        show_header=True,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("パス (Path)", style="bold")
    table.add_column("権限 (Mode)")
    table.add_column("UID:GID", justify="right")
    table.add_column("サイズ", justify="right")
    table.add_column("マウント状態")
    table.add_column("診断ステータス")

    entries = status.get("entries") or []
    for entry in entries:
        risk = entry.get("risk_level", "normal")
        mount_mode = entry.get("mount_mode", "rw")
        name_s = escape(str(entry.get("name", "")))
        perms_s = escape(str(entry.get("perms", "")))
        uid_gid_s = f"{entry.get('uid', 0)}:{entry.get('gid', 0)}"
        size_s = f"{entry.get('size', 0):,} B"
        mount_s = escape(str(entry.get("mount_source", "")))
        status_s = escape(str(entry.get("status_label", "")))
        row_style: str | None = None

        if risk == "danger":
            name_styled = f"[bold red]{name_s}[/bold red]"
            status_styled = f"[bold red]{status_s}[/bold red]"
        elif risk == "warning":
            name_styled = f"[bold yellow]{name_s}[/bold yellow]"
            status_styled = f"[bold yellow]{status_s}[/bold yellow]"
        elif risk == "safe":
            name_styled = f"[bold green]{name_s}[/bold green]"
            status_styled = f"[bold green]{status_s}[/bold green]"
        elif mount_mode == "rw":
            name_styled = f"[bold cyan]{name_s}[/bold cyan]"
            status_styled = f"[bold cyan]{status_s}[/bold cyan]"
            row_style = "cyan"
        else:
            name_styled = f"[dim]{name_s}[/dim]"
            status_styled = f"[dim]{status_s}[/dim]"
            row_style = "dim"

        table.add_row(
            name_styled,
            perms_s,
            uid_gid_s,
            size_s,
            mount_s,
            status_styled,
            style=row_style,
        )

    warnings = status.get("warnings") or []
    danger_count = sum(1 for w in warnings if w.get("level") == "danger")
    warn_count = sum(1 for w in warnings if w.get("level") == "warning")

    if not warnings:
        diag_text = (
            "\n[bold green]✅ セキュリティ診断結果: 問題なし[/bold green]\n"
            "[dim]検出された機密ファイルや管理ディレクトリはすべて `/dev/null` マスク、読み取り専用 (`ro`)、"
            "または専用ボリュームで適切に保護されています。[/dim]"
        )
        border_color = "green"
    else:
        diag_lines = [
            f"\n[bold yellow]【3. セキュリティ・マウント診断アラート "
            f"(🔴 危険: {danger_count}件 / 🟡 注意: {warn_count}件)】[/bold yellow]"
        ]
        for w in warnings:
            lvl = w.get("level", "warning")
            path_label = escape(str(w.get("path", "")))
            msg = escape(str(w.get("message", "")))
            rec = escape(str(w.get("recommendation", "")))
            if lvl == "danger":
                diag_lines.append(
                    f"  🔴 [bold red]{path_label}[/bold red]: {msg}\n"
                    f"     [dim]💡 対策: {rec}[/dim]"
                )
            else:
                diag_lines.append(
                    f"  🟡 [bold yellow]{path_label}[/bold yellow]: {msg}\n"
                    f"     [dim]💡 推奨: {rec}[/dim]"
                )
        diag_text = "\n".join(diag_lines)
        border_color = "red" if danger_count > 0 else "yellow"

    content_group = Group(
        Text.from_markup(header_summary),
        table,
        Text.from_markup(diag_text),
    )
    return Panel(
        content_group,
        title="[bold cyan]🛡️ サンドボックス環境 & マウント状態診断 (/sandbox)[/bold cyan]",
        border_style=border_color,
    )


def build_token_usage_panel(token_usage: dict[str, int]) -> Panel:
    """
    現在のコンテキスト量（セッション切替の目安）およびセッション累積トークン消費量の詳細パネルを生成して返します。
    """
    ctx_tokens = int(token_usage.get("last_prompt_tokens", 0))
    history_msgs = int(token_usage.get("chat_history_messages", 0))
    materials_cnt = int(
        token_usage.get("materials_count", token_usage.get("search_materials_count", 0))
    )

    cum_prompt = int(token_usage.get("cumulative_prompt_tokens", 0))
    cum_candidates = int(token_usage.get("cumulative_candidates_tokens", 0))
    cum_thoughts = int(token_usage.get("cumulative_thoughts_tokens", 0))
    cum_cached = int(token_usage.get("cumulative_cached_tokens", 0))
    cum_tool_use = int(token_usage.get("cumulative_tool_use_tokens", 0))
    cum_total = int(token_usage.get("cumulative_total_tokens", 0))

    chat_calls = int(token_usage.get("chat_api_calls", 0))
    search_calls = int(token_usage.get("search_api_calls", 0))
    total_calls = int(token_usage.get("total_api_calls", 0))

    if ctx_tokens >= CONTEXT_TOKEN_DANGER_THRESHOLD:
        ctx_badge = f"[bold red]🔴 {ctx_tokens:,} tokens (肥大化・新セッション切替推奨)[/bold red]"
        advice_text = (
            "\n[bold red]⚠️ コンテキストが肥大化しています（150,000 tokens 以上）。[/bold red]\n"
            "応答速度の低下や古い指示の忘却（Context Rot）を防ぐため、[bold cyan]/clear[/bold cyan]（または [bold cyan]/reset[/bold cyan]）で\n"
            "新しい対話セッションへ切り替えることを強く推奨します。"
        )
        border_color = "red"
    elif ctx_tokens >= CONTEXT_TOKEN_WARNING_THRESHOLD:
        ctx_badge = f"[bold yellow]🟡 {ctx_tokens:,} tokens (やや大・区切りでの切替を検討)[/bold yellow]"
        advice_text = (
            "\n[bold yellow]💡 会話履歴が蓄積しています（50,000 tokens 以上）。[/bold yellow]\n"
            "現在のタスクがひと段落したタイミングで、[bold cyan]/clear[/bold cyan] による新しい対話セッションへの切り替えをおすすめします。"
        )
        border_color = "yellow"
    else:
        ctx_badge = f"[bold green]🟢 {ctx_tokens:,} tokens (快適・余裕あり)[/bold green]"
        advice_text = (
            "\n[bold green]✅ コンテキスト量は十分余裕があります。[/bold green]\n"
            "[dim]そのまま対話を継続できます。別の新しい話題・タスクに移る際は [bold cyan]/clear[/bold cyan] で履歴をリセットできます。[/dim]"
        )
        border_color = "cyan"

    section1_text = (
        "[bold yellow]【1. 現在のコンテキスト状態（新セッション切替の目安）】[/bold yellow]\n"
        f"  • [bold cyan]直近コンテキスト量 :[/bold cyan] {ctx_badge}\n"
        f"  • [bold cyan]会話履歴メッセージ :[/bold cyan] {history_msgs} 件\n"
        f"  • [bold cyan]保持中の作業前提資料:[/bold cyan] {materials_cnt} 件\n"
    )

    table = Table(
        title="【2. セッション累積トークン消費量 (API総計)】",
        title_justify="left",
        title_style="bold yellow",
        show_header=True,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("内訳項目", style="bold")
    table.add_column("累計値", justify="right")
    table.add_column("説明", style="dim")

    table.add_row("入力トークン累計 (Prompt)", f"{cum_prompt:,} tokens", "全APIコールで送信された入力コンテキストの総計")
    table.add_row("出力トークン累計 (Candidates)", f"{cum_candidates:,} tokens", "AIの回答およびツール呼び出し引数の出力総計")
    table.add_row("思考トークン累計 (Thoughts)", f"{cum_thoughts:,} tokens", "推論（Thinking）プロセスで消費された総計")
    table.add_row("キャッシュヒット累計 (Cached)", f"{cum_cached:,} tokens", "コンテキストキャッシュにヒットしたトークン総計")
    if cum_tool_use > 0:
        table.add_row("ツール利用プロンプト累計", f"{cum_tool_use:,} tokens", "Google検索等の組み込みツール結果に伴うトークン")
    table.add_row(
        "[bold cyan]累積合計トークン (Total)[/bold cyan]",
        f"[bold cyan]{cum_total:,} tokens[/bold cyan]",
        "このセッションで消費された全トークンの総計",
    )
    table.add_row(
        "APIリクエスト回数 (Calls)",
        f"{total_calls:,} 回",
        f"通常対話・ツールループ: {chat_calls}回 / Web検索: {search_calls}回",
    )

    footer_guide = (
        f"{advice_text}\n"
        "[dim]  • 会話履歴とトークン統計をリセット（前提資料は維持）: [bold cyan]/clear[/bold cyan] (または [bold cyan]/reset[/bold cyan], [bold cyan]/new[/bold cyan])\n"
        "  • 作業前提資料も含めて完全に初期状態へリセット    : [bold cyan]/clear --all[/bold cyan] (または [bold cyan]/clear -a[/bold cyan])[/dim]"
    )

    content_group = Group(
        Text.from_markup(section1_text),
        table,
        Text.from_markup(footer_guide),
    )
    return Panel(
        content_group,
        title="[bold cyan]📊 トークン使用量 & コンテキスト診断 (/tokens)[/bold cyan]",
        border_style=border_color,
    )


def build_session_reset_panel(reset_summary: dict[str, int], clear_materials: bool = False) -> Panel:
    """対話セッションのリセット完了通知パネルを生成して返します。"""
    cleared_msgs = int(reset_summary.get("cleared_messages", 0))
    prev_ctx = int(reset_summary.get("previous_context_tokens", 0))
    prev_total = int(reset_summary.get("previous_total_tokens", 0))
    cleared_mats = int(reset_summary.get("cleared_materials", 0))
    retained_mats = int(reset_summary.get("retained_materials", 0))

    if clear_materials:
        mat_status = f"[bold yellow]破棄しました（{cleared_mats} 件消去）[/bold yellow]"
    elif retained_mats > 0:
        mat_status = f"[bold green]保持しています（{retained_mats} 件維持 / 消去するには '/clear --all'）[/bold green]"
    else:
        mat_status = "[dim]なし（0 件）[/dim]"

    reset_text = (
        "[bold green]✨ 対話セッションをリセットし、新しいセッションを開始しました。[/bold green]\n\n"
        f"  • [bold cyan]消去した会話履歴  :[/bold cyan] {cleared_msgs} メッセージ\n"
        f"  • [bold cyan]リセット前トークン:[/bold cyan] コンテキスト {prev_ctx:,} tokens / 累積 {prev_total:,} tokens ➔ [bold green]0 にリセット[/bold green]\n"
        f"  • [bold cyan]作業前提資料      :[/bold cyan] {mat_status}"
    )
    return Panel(
        reset_text,
        title="[bold green]🔄 新規対話セッション開始 (/clear)[/bold green]",
        border_style="green",
    )


def build_edit_mode_changed_panel(enabled: bool) -> Panel:
    """エディットモードの有効化・無効化に伴う通知パネルを生成して返します。"""
    if enabled:
        return Panel(
            "[bold yellow]✏️ エディットモードを有効化しました。[/bold yellow]\n"
            "  • ファイル作成・編集・移動・コピー : [bold green]自動承認[/bold green]\n"
            "  • ファイル削除・シェルコマンド実行 : [bold yellow]都度確認[/bold yellow]\n"
            "[dim]（通常承認モードへ戻すには `/edit off`）[/dim]",
            border_style="yellow",
        )
    return Panel(
        "[bold green]通常承認モード（都度確認）に戻しました。[/bold green]",
        border_style="green",
    )


def build_trust_mode_changed_panel(enabled: bool) -> Panel:
    """トラストモードの有効化・無効化に伴う通知パネルを生成して返します。"""
    if enabled:
        return Panel(
            "[bold red]トラストモードを有効化しました。[/bold red]（通常承認モードへ戻すには `/trust off`）",
            border_style="red",
        )
    return Panel(
        "[bold green]通常承認モード（都度確認）に戻しました。[/bold green]",
        border_style="green",
    )


def build_model_changed_panel(model_name: str) -> Panel:
    """AIモデル切替完了の通知パネルを生成して返します。"""
    return Panel(
        f"[bold green]AIモデルを切り替えました:[/bold green] [bold cyan]{escape(model_name)}[/bold cyan]",
        border_style="green",
    )


# =====================================================================
# 3. 通常対話・検索対話・エラーパネル生成
# =====================================================================

def build_search_materials_cleared_panel(cleared_count: int) -> Panel:
    """保持していた作業前提資料の破棄完了パネルを生成して返します。"""
    return Panel(
        f"[bold green]保持していた作業前提資料（{cleared_count}件）を破棄しました。[/bold green]",
        title="[bold cyan]🧹 参照資料クリア[/bold cyan]",
        border_style="green",
    )


def build_search_help_panel() -> Panel:
    """Web検索コマンド（/search）の使い方パネルを生成して返します。"""
    return Panel(
        "[bold yellow][使い方][/bold yellow] /search <調べたい内容> (または /s <調べたい内容>)\n"
        "[dim]オプション:\n"
        "  -n, --new    : 直前の検索文脈をリセットして新規検索を開始\n"
        "  -c, --clear  : セッション内に保持されている作業前提資料を破棄\n\n"
        "例: /search Python 3.15の新機能\n"
        "    /search -n FastAPI ライフスパンハンドラー\n"
        "    /search --clear\n\n"
        "※ 直前の検索結果を踏まえた追加質問・深掘り検索は、そのまま /search で続けて質問できます。\n"
        "※ 取得された検索結果は「作業前提資料」としてセッション内に保持され、以降の通常指示で自動的に参照されます。\n"
        "※ 通常のプロンプト入力ではWeb検索は実行されません。[/dim]",
        title="[bold cyan]🔍 Web検索機能[/bold cyan]",
        border_style="cyan",
    )


def build_search_response_panel(
    response_text: str,
    sources: list[dict[str, str]],
    now_time_text: str,
) -> Panel | None:
    """Web検索の回答テキストと参照元リンクを含むパネルを生成して返します（回答が空の場合は None）。"""
    if not response_text:
        return None

    content_items = [Markdown(f"{now_time_text}\n{response_text}")]

    if sources:
        link_items = []
        for s in sources:
            title = s.get("title") or "Webページ"
            url = s.get("url", "")
            link_items.append(f"[link={url} cyan underline]{escape(title)}[/link]")

        sources_markup = "     ".join(link_items)
        content_items.append(Rule(style="dim"))
        content_items.append(Text.from_markup(sources_markup))

    panel_content = Group(*content_items) if len(content_items) > 1 else content_items[0]

    return Panel(
        panel_content,
        title="[bold blue]💬 Search Response[/bold blue]",
        border_style="blue",
        expand=False,
    )


def build_agent_log_panel(
    log_message: str,
    category: str | None = None,
) -> Panel:
    """エージェントの思考・ツール実行ログをMarkdownパネルとして生成して返します。"""
    md_content = Markdown(log_message)
    return Panel(
        md_content,
        title="[bold blue]💬 Agent Log[/bold blue]",
        border_style="blue",
        expand=False,
    )


def build_sandbox_security_error_panel(error: Exception) -> Panel:
    """開発者モード起動時のサンドボックス安全性検証エラーパネルを生成して返します。"""
    return Panel(
        f"[bold red]開発者モード（--dev）の起動が拒否されました:[/bold red]\n\n{escape(str(error))}",
        title="[bold red]🚨 サンドボックス安全性検証エラー[/bold red]",
        border_style="red",
    )


def build_search_error_panel(error: Exception) -> Panel:
    """Web検索実行中の予期せぬエラーパネルを生成して返します。"""
    return Panel(
        f"[bold red]Web検索中に予期せぬエラーが発生しました:[/bold red] {escape(str(error))}\n\n"
        "[dim]通信環境やAPI設定をご確認の上、再度お試しください。[/dim]",
        title="[bold red]⚠️ 検索エラー[/bold red]",
        border_style="red",
    )


def build_unexpected_error_panel(error: Exception) -> Panel:
    """通常プロンプト実行中の予期せぬエラーパネルを生成して返します。"""
    return Panel(
        f"[bold red]予期せぬエラーが発生しました:[/bold red] {escape(str(error))}\n\n"
        "[dim]直前までのツール実行結果などの作業状態は維持されています。そのまま次の指示を入力してください。[/dim]",
        title="[bold red]⚠️ エラー[/bold red]",
        border_style="red",
    )


# =====================================================================
# 4. 対話的確認・選択プロンプト用パネル生成
# =====================================================================

def build_trust_mode_consent_panel() -> Panel:
    """トラストモード有効化前の注意事項パネルを生成して返します。"""
    return Panel(
        "[bold yellow]🛡️  トラストモード（Trust Mode）の確認[/bold yellow]\n\n"
        "トラストモードは、[bold cyan]信頼できるプロジェクト（自作コードや安全確認済みのリポジトリ）[/bold cyan]で\n"
        "AIに自由かつ高速に作業させるためのモードです。\n"
        "有効にすると、致命的なセキュリティ違反（プロジェクト外アクセスや機密ファイル操作等）を除き、\n"
        "[bold red]ファイルの書き込み・上書き・削除・移動やサンドボックス内コマンド実行がすべて事前確認なしで自動承認[/bold red]されます。\n\n"
        "[dim]※ 外部から取得した未知のプロジェクトや信用できないファイルが含まれる場合は、通常承認モードをご利用ください。\n"
        "※ Git等でいつでも変更を元に戻せる状態で実行することを強く推奨します。[/dim]",
        title="[bold red]🚨 トラストモードの許可確認[/bold red]",
        border_style="red",
    )


def build_api_key_guide_panel() -> Panel:
    """コンテナ内起動モードにおけるAPIキー入力案内パネルを生成して返します。"""
    return Panel(
        "[bold yellow]コンテナ内でのAPIキー漏洩（環境変数や /proc/*/environ の読み取り）を防ぐため、\n"
        "コンテナ内起動モードでは起動時の直接入力が必要です。[/bold yellow]\n\n"
        "ホストOS（手元のPC）で別のターミナルを開き、以下のコマンドでシステム環境変数のAPIキーを\n"
        "表示またはコピーして貼り付けてください（入力完了後、別ターミナルは閉じて構いません）。\n\n"
        "[bold cyan]【Windows (PowerShell)】[/bold cyan]\n"
        "  • 表示する場合              : [bold green]$env:GEMINI_API_KEY[/bold green]\n"
        "  • クリップボードへ直コピー  : [bold green]Set-Clipboard $env:GEMINI_API_KEY[/bold green]\n\n"
        "[bold cyan]【Windows (コマンドプロンプト)】[/bold cyan]\n"
        "  • 表示する場合              : [bold green]echo %GEMINI_API_KEY%[/bold green]\n"
        "  • クリップボードへ直コピー  : [bold green]echo | set /p=\"%GEMINI_API_KEY%\" | clip[/bold green]\n\n"
        "[bold cyan]【macOS / Linux / WSL】[/bold cyan]\n"
        "  • 表示する場合              : [bold green]echo $GEMINI_API_KEY[/bold green]\n"
        "  • クリップボードへ直コピー(macOS): [bold green]printf \"%s\" \"$GEMINI_API_KEY\" | pbcopy[/bold green]\n\n"
        "[dim]※ 貼り付けたAPIキーは画面上で '****' としてマスク表示され、確認なしでそのまま起動します。[/dim]",
        title="[bold cyan]🔐 コンテナ内起動：APIキーの安全入力[/bold cyan]",
        border_style="cyan",
    )


def build_approval_panel(action_description: str) -> Panel:
    """破壊的操作実行前のHuman-in-the-loop承認要求パネルを生成して返します。"""
    return Panel(
        f"[bold yellow]Gemioが以下の操作を実行しようとしています:[/bold yellow]\n\n"
        f"  [bold cyan]>[/bold cyan] {escape(action_description)}\n\n"
        "[dim]選択肢: [bold green]y[/bold green]=許可 / [bold yellow]n[/bold yellow]=拒否 / "
        "[bold red]a[/bold red]=作業を強制中止(Abort) / [bold red]k[/bold red]=プロセス即時終了(Kill)[/dim]",
        title="[bold red]⚠️  Human-in-the-loop 承認要求[/bold red]",
        border_style="yellow",
    )


def build_ask_user_panel(
    question: str,
    options: list[str] | None = None,
    context: str = "",
) -> Panel:
    """AIからの質問・確認要求および選択肢（Markdown対応）のパネルを生成して返します。"""
    body_renderables: list = []
    if context and context.strip():
        body_renderables.append(Text.from_markup("[bold yellow]【背景・状況】[/bold yellow]"))
        body_renderables.append(Markdown(context.strip()))
        body_renderables.append(Text(""))

    body_renderables.append(Text.from_markup("[bold cyan]【質問】[/bold cyan]"))
    body_renderables.append(Markdown(question.strip()))
    body_renderables.append(Text(""))

    if options:
        body_renderables.append(Text.from_markup("[bold green]【選択肢】[/bold green]"))
        opt_grid = Table.grid(padding=(0, 1))
        opt_grid.add_column(no_wrap=True)
        opt_grid.add_column()
        for idx, opt in enumerate(options, 1):
            opt_grid.add_row(
                Text.from_markup(f"  [bold cyan][{idx}][/bold cyan]"),
                Markdown(str(opt).strip()),
            )
        opt_grid.add_row(
            Text.from_markup("  [bold cyan][0][/bold cyan]"),
            Text("その他（任意の指示を入力）"),
        )
        opt_grid.add_row(
            Text.from_markup("  [bold red]\\[a][/bold red]"),
            Text("作業を強制中止 (Abort)"),
        )
        body_renderables.append(opt_grid)
    else:
        body_renderables.append(
            Text.from_markup("[dim]※ 回答・指示を入力してください（[bold red]a[/bold red] で作業を強制中止）[/dim]")
        )

    return Panel(
        Group(*body_renderables),
        title="[bold cyan]💬 Gemioからの確認・質問[/bold cyan]",
        border_style="cyan",
    )


def build_model_selection_panel(current_model: str) -> Panel:
    """プリセットモデル選択メニューのパネルを生成して返します。"""
    lines = [f"[bold yellow]現在のモデル:[/bold yellow] [bold cyan]{current_model}[/bold cyan]\n"]
    lines.append("[bold]プリセット一覧:[/bold]")
    for idx, (m_name, m_desc) in enumerate(MODEL_PRESETS, start=1):
        prefix = "[bold green]●[/bold green] " if m_name == current_model else "  "
        lines.append(f"{prefix}[bold cyan]{idx}.[/bold cyan] {m_name} [dim]- {m_desc}[/dim]")
    lines.append("\n番号またはモデル名（エイリアス）を入力してください (未入力でキャンセル):")

    return Panel("\n".join(lines), title="[bold cyan]🔄 AIモデル選択[/bold cyan]", border_style="cyan")
