from unittest.mock import patch
from prompt_toolkit.formatted_text import HTML, to_formatted_text
from rich.console import Console
from rich.panel import Panel

from gemio.cli.ui.base import AgentLogCategory
from gemio.cli.ui.renderables import (
    build_agent_log_panel,
    build_api_key_guide_panel,
    build_approval_panel,
    build_ask_user_panel,
    build_edit_mode_changed_panel,
    build_help_panel,
    build_model_changed_panel,
    build_model_selection_panel,
    build_sandbox_security_error_panel,
    build_sandbox_status_panel,
    build_search_error_panel,
    build_search_help_panel,
    build_search_materials_cleared_panel,
    build_search_response_panel,
    build_session_reset_panel,
    build_token_usage_panel,
    build_trust_mode_changed_panel,
    build_trust_mode_consent_panel,
    build_unexpected_error_panel,
    build_welcome_panel,
    classify_agent_log,
    format_dev_mode_label,
    format_prompt_header,
)
from gemio.project import DeveloperProject, Project


def _render_to_text(renderable, width: int = 120) -> str:
    """Rich Renderable オブジェクトをプレーンテキストへレンダリングするヘルパー。"""
    console = Console(width=width, record=True)
    console.print(renderable)
    return console.export_text()


def test_format_prompt_header_modes_and_approval_transitions(tmp_path):
    """基本モード・開発者モード（コンテナ内 / ホストDocker）および承認モード切替がヘッダーHTMLに反映されること"""
    project = Project(str(tmp_path), require_approval=True)

    # 1. 基本モード & 通常承認モード
    h_normal = format_prompt_header(project)
    assert f"<ansiblue><b>project:</b></ansiblue> {tmp_path}" in h_normal
    assert "<ansigreen><b>基本モード (Standard)</b></ansigreen>" in h_normal
    assert "<ansigreen><b>通常承認モード (都度確認)</b></ansigreen>" in h_normal
    assert "(Esc+Enterで送信 / Esc+Backspaceでクリア):" in h_normal
    assert len(to_formatted_text(HTML(h_normal))) > 0

    # 2. エディットモード
    project.set_edit_mode(True)
    h_edit = format_prompt_header(project)
    assert "<ansiyellow><b>エディットモード (編集自動 / 削除・コマンド確認)</b></ansiyellow>" in h_edit
    assert len(to_formatted_text(HTML(h_edit))) > 0

    # 3. トラストモード
    project.set_trust_mode(True)
    h_trust = format_prompt_header(project)
    assert "<ansired><b>トラストモード (自動承認)</b></ansired>" in h_trust
    assert len(to_formatted_text(HTML(h_trust))) > 0

    # 4. 開発者モード（in_container / host_docker）および format_dev_mode_label
    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_in = DeveloperProject(str(tmp_path), require_approval=True)
        h_in = format_prompt_header(dev_in)
        assert "開発者モード (Dev Container内実行)" in h_in
        assert "開発者モード (Dev Container内実行)" in format_dev_mode_label(dev_in)
        assert len(to_formatted_text(HTML(h_in))) > 0

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch.object(DeveloperProject, "_validate_and_configure_docker_container", return_value="/workspace"):
        dev_host = DeveloperProject(str(tmp_path), require_approval=False, container_name="my-dev-sandbox")
        h_host = format_prompt_header(dev_host)
        assert "開発者モード (Docker: my-dev-sandbox)" in h_host
        assert "開発者モード (Docker: my-dev-sandbox)" in format_dev_mode_label(dev_host)
        assert len(to_formatted_text(HTML(h_host))) > 0


def test_format_prompt_header_escapes_html_and_applies_token_thresholds(tmp_path):
    """特殊文字(&, <, >)のHTMLエスケープと、コンテキスト量しきい値に応じた3段階カラー表示が正しいこと"""
    special_dir = tmp_path / "dir_with_&_<tag>"
    special_dir.mkdir()

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=False), \
         patch.object(DeveloperProject, "_validate_and_configure_docker_container", return_value="/workspace"):
        dev_proj = DeveloperProject(str(special_dir), require_approval=True, container_name="c_<test>&1")
        header = format_prompt_header(dev_proj)
        assert "dir_with_&amp;_&lt;tag&gt;" in header
        assert "c_&lt;test&gt;&amp;1" in header
        plain = "".join(frag[1] for frag in to_formatted_text(HTML(header)))
        assert "dir_with_&_<tag>" in plain and "c_<test>&1" in plain

    proj = Project(str(tmp_path), require_approval=True)
    # 緑 (< 50,000)
    h_green = format_prompt_header(
        proj, {"last_prompt_tokens": 12450, "cumulative_total_tokens": 35000, "total_api_calls": 3}
    )
    assert "context <ansigreen><b>12,450</b></ansigreen> / cumulative <b>35,000</b> (calls: 3)" in h_green

    # 黄 (50,000 〜 149,999)
    h_yellow = format_prompt_header(
        proj, {"last_prompt_tokens": 68000, "cumulative_total_tokens": 180000, "total_api_calls": 10}
    )
    assert "context <ansiyellow><b>68,000</b></ansiyellow>" in h_yellow

    # 赤 (>= 150,000)
    h_red = format_prompt_header(
        proj, {"last_prompt_tokens": 162000, "cumulative_total_tokens": 450000, "total_api_calls": 22}
    )
    assert "context <ansired><b>162,000</b></ansired>" in h_red


def test_classify_agent_log_categories():
    """classify_agent_log がログ文字列から各カテゴリ（TOOL, WARNING, ABORT, SEARCH, ASSISTANT）を正確に識別すること"""
    assert classify_agent_log("12:00:00\nTool call: read_file (file_path='a.py')") == AgentLogCategory.TOOL
    assert classify_agent_log("12:00:01\n[警告] サーバー混雑を検知しました") == AgentLogCategory.WARNING
    assert classify_agent_log("-> ユーザーの指示により、追加で 10 回のループを実行します。") == AgentLogCategory.WARNING
    assert classify_agent_log("12:00:02\n🚨 【作業の強制中止】\n中断しました") == AgentLogCategory.ABORT
    assert classify_agent_log("12:00:03\n[Search] Python 3.15") == AgentLogCategory.SEARCH
    assert classify_agent_log("12:00:04\nリファクタリングが完了しました。") == AgentLogCategory.ASSISTANT


def test_build_sandbox_status_panel_standard_and_developer_modes(tmp_path):
    """
    build_sandbox_status_panel が基本モードと開発者モード（機密キー検出・RO/RW状態表示）の双方で
    正確な診断パネルを生成すること
    """
    # 1. 基本モード
    std_proj = Project(str(tmp_path), require_approval=True)
    std_text = _render_to_text(build_sandbox_status_panel(std_proj))
    assert "基本モード (Standard Mode)" in std_text

    # 2. 開発者モード（機密環境変数警告・読取専用ro・作業対象rwの表示検証）
    mock_status = {
        "execution_mode": "host_docker",
        "container_name": "gemio-sandbox-py",
        "workspace_dir": "/app",
        "workspace_rw": True,
        "user": {"user_str": "uid=1000(sandbox)", "is_root": False},
        "network": {"mode": "none", "is_isolated": True},
        "resources": {
            "readonly_rootfs": True,
            "memory_limit": "2.0 GB",
            "cpus": "2.0",
            "pids_limit": "128",
        },
        "sensitive_env_keys": ["GEMINI_API_KEY", "GITHUB_TOKEN"],
        "entries": [
            {
                "name": "pyproject.toml",
                "perms": "-rw-r--r--",
                "uid": 1000,
                "gid": 1000,
                "size": 120,
                "mount_source": "bind (ro)",
                "mount_mode": "ro",
                "risk_level": "normal",
                "status_label": "⚪ 読取専用 (ro)",
            },
            {
                "name": "src/",
                "perms": "drwxr-xr-x",
                "uid": 1000,
                "gid": 1000,
                "size": 4096,
                "mount_source": "workspace (rw)",
                "mount_mode": "rw",
                "risk_level": "normal",
                "status_label": "🔵 作業対象 (rw)",
            },
        ],
        "warnings": [
            {
                "level": "danger",
                "path": "コンテナ環境変数 (GEMINI_API_KEY, GITHUB_TOKEN)",
                "message": "コンテナ内に機密環境変数が設定されています。",
                "recommendation": "環境変数の引き渡しを解除してください。",
            }
        ],
    }

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=False)
        with patch.object(dev_proj, "inspect_sandbox_status", return_value=mock_status):
            dev_text = _render_to_text(build_sandbox_status_panel(dev_proj))

    assert "🔴 機密キー検出あり (GEMINI_API_KEY, GITHUB_TOKEN)" in dev_text
    assert "⚪ 読取専用 (ro)" in dev_text
    assert "🔵 作業対象 (rw)" in dev_text
    assert "セキュリティ・マウント診断アラート" in dev_text


def test_build_ask_user_panel_renders_markdown_and_preserves_bracket_labels():
    """
    build_ask_user_panel において、context・question・options が Markdown として整形され、
    かつ [1], [0], [a] の角括弧ラベルが Rich マークアップに消去されず表示されること
    """
    panel = build_ask_user_panel(
        question="どちらの **実装方式** を選択しますか？",
        options=["方式A (`fast`)", "方式B (**safe**)"],
        context="`config.py` の **設計方針** の確認",
    )
    rendered = _render_to_text(panel, width=100)
    assert "config.py の 設計方針 の確認" in rendered
    assert "どちらの 実装方式 を選択しますか？" in rendered
    assert "[1] 方式A (fast)" in rendered
    assert "[2] 方式B (safe)" in rendered
    assert "**" not in rendered and "`" not in rendered
    assert "[0] その他（任意の指示を入力）" in rendered
    assert "[a] 作業を強制中止 (Abort)" in rendered


def test_all_other_panel_builders_produce_valid_panels(tmp_path):
    """その他の全 build_*_panel 純粋関数が副作用なしに有効な Panel を構築すること"""
    assert isinstance(build_welcome_panel(tmp_path, "gemini-flash-latest"), Panel)
    assert isinstance(build_help_panel(), Panel)
    assert isinstance(build_token_usage_panel({"last_prompt_tokens": 60000, "cumulative_total_tokens": 120000}), Panel)
    assert isinstance(build_session_reset_panel({"cleared_messages": 4, "retained_materials": 1}), Panel)
    assert isinstance(build_edit_mode_changed_panel(True), Panel)
    assert isinstance(build_edit_mode_changed_panel(False), Panel)
    assert isinstance(build_trust_mode_changed_panel(True), Panel)
    assert isinstance(build_trust_mode_changed_panel(False), Panel)
    assert isinstance(build_model_changed_panel("gemini-4-argon"), Panel)
    assert isinstance(build_search_materials_cleared_panel(2), Panel)
    assert isinstance(build_search_help_panel(), Panel)
    assert isinstance(
        build_search_response_panel(
            "検索結果の本文",
            [{"title": "Python Docs", "url": "https://docs.python.org"}],
            "12:00:00",
        ),
        Panel,
    )
    assert build_search_response_panel("", [], "12:00:00") is None
    assert isinstance(build_agent_log_panel("12:00:00\n完了しました"), Panel)
    assert isinstance(build_sandbox_security_error_panel(RuntimeError("sec err")), Panel)
    assert isinstance(build_search_error_panel(RuntimeError("search err")), Panel)
    assert isinstance(build_unexpected_error_panel(RuntimeError("unexpected err")), Panel)
    assert isinstance(build_trust_mode_consent_panel(), Panel)
    assert isinstance(build_api_key_guide_panel(), Panel)
    assert isinstance(build_approval_panel("write_file: main.py"), Panel)
    assert isinstance(build_model_selection_panel("gemini-flash-latest"), Panel)
