from pathlib import Path
from unittest.mock import patch
import pytest

from gemio.project.base import (
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_TRUST,
    MAX_FIND_RESULTS,
    MAX_SEARCH_RESULTS,
    MAX_STRUCTURE_ENTRIES,
    AgentExecutionAborted,
    Project,
)


@pytest.fixture
def temp_project(tmp_path):
    """ファイル操作・探索・セキュリティ境界の検証用プロジェクト（承認確認なし）"""
    return Project(str(tmp_path), require_approval=False)


# =====================================================================
# 1. 基本ファイル・ディレクトリ操作および構文検証
# =====================================================================

def test_file_read_write_batch_and_metadata(temp_project):
    """write_file, read_file, read_files_batch, get_file_metadata が正常に動作すること"""
    content = "Hello, Gemio Project!"
    assert "成功" in temp_project.write_file("test.txt", content)
    assert temp_project.read_file("test.txt") == content

    temp_project.write_file("second.txt", "Second content")
    batch = temp_project.read_files_batch(["test.txt", "second.txt", "missing.txt"])
    assert batch["test.txt"] == content
    assert batch["second.txt"] == "Second content"
    assert "[ERROR]" in batch["missing.txt"]

    meta = temp_project.get_file_metadata("test.txt")
    assert isinstance(meta, dict)
    assert meta["size_bytes"] == len(content.encode("utf-8"))
    assert "md5" in meta and "mtime" in meta


def test_search_and_replace_and_preview_file(temp_project):
    """search_and_replace_file および preview_file が指定範囲・対象箇所を正確に処理すること"""
    temp_project.write_file("code.py", "def hello():\n    return 'old'\n")
    replace_res = temp_project.search_and_replace_file("code.py", "return 'old'", "return 'new'")
    assert "成功" in replace_res
    assert "return 'new'" in temp_project.read_file("code.py")

    temp_project.write_file("lines.txt", "line 0\nline 1\nline 2\nline 3\nline 4")
    assert temp_project.preview_file("lines.txt", start_line=1, end_line=3) == "line 1\nline 2"


def test_syntax_check_valid_and_invalid_python(temp_project):
    """syntax_check が外部プロセスを起動せずPythonファイルの構文エラー有無を正確に判定すること"""
    temp_project.write_file("valid.py", "def foo():\n    return 42\n")
    valid_res = temp_project.syntax_check("valid.py")
    assert valid_res == {"result": True, "details": None}

    temp_project.write_file("invalid.py", "def foo(\n")
    invalid_res = temp_project.syntax_check("invalid.py")
    assert invalid_res["result"] is False
    assert "lineno" in invalid_res["details"]


# =====================================================================
# 2. セキュリティガードレール（パストラバーサル・機密保護・管理領域保護）
# =====================================================================

def test_security_path_traversal_blocked(temp_project):
    """プロジェクトルート外へのパストラバーサルが PermissionError で遮断されること"""
    with pytest.raises(PermissionError):
        temp_project._verify_and_get_path("../outside.txt")


def test_security_secret_files_blocked_and_env_templates_allowed(temp_project):
    """
    .env や秘密鍵・クラウド認証情報への読み書き・検索が常時ブロックされ、
    .env.example 等のテンプレートファイルのみ許可されること
    """
    blocked_files = [
        ".env",
        ".env.local",
        "server.pem",
        "auth.json",
        "gcp-service-account.json",
        "terraform.tfstate",
        "app.keystore",
        "keystore.jks",
    ]
    for secret_name in blocked_files:
        res_write = temp_project.write_file(secret_name, "SECRET=123")
        assert "[ERROR]" in res_write and "SECURITY_ALERT" in res_write

    # 物理的に存在する .env も読み取り・全文検索から保護されること
    (Path(temp_project.path) / ".env").write_text("API_KEY=topsecret", encoding="utf-8")
    res_env_read = temp_project.read_file(".env")
    assert "[ERROR]" in res_env_read and "SECURITY_ALERT" in res_env_read
    assert temp_project.search_text("topsecret") == []

    # テンプレートファイルは読み書き可能であること
    for template_name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        assert "成功" in temp_project.write_file(template_name, "API_KEY=your_key_here")
        assert temp_project.read_file(template_name) == "API_KEY=your_key_here"


def test_security_git_and_devcontainer_and_directory_tree_protection(temp_project):
    """
    - .git/ 配下の直接読み書き・削除がブロックされること
    - .devcontainer/devcontainer.json は新規作成のみ許可され、既存変更・削除・別ファイル追加はブロックされること
    - 配下に機密ファイルを含むディレクトリごとの削除・リネームがブロックされること
    """
    git_dir = Path(temp_project.path) / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "config").write_text("url = https://github.com/org/repo.git", encoding="utf-8")

    assert "SECURITY_ALERT" in temp_project.read_file(".git/config")
    assert "SECURITY_ALERT" in temp_project.write_file(".git/hooks/pre-commit", "#!/bin/sh\n")
    assert "SECURITY_ALERT" in temp_project.delete_path(".git/config")

    # .devcontainer/devcontainer.json の新規作成は許可、上書き・別ファイル追加・削除は拒否
    assert "成功" in temp_project.write_file(".devcontainer/devcontainer.json", '{"name": "safe"}')
    assert "SECURITY_ALERT" in temp_project.write_file(".devcontainer/devcontainer.json", '{"runArgs": ["--privileged"]}')
    assert "SECURITY_ALERT" in temp_project.write_file(".devcontainer/Dockerfile", "FROM ubuntu:latest")
    assert "SECURITY_ALERT" in temp_project.delete_path(".devcontainer/devcontainer.json")

    # .gemio/ 配下（skills, tools 等）へのAIによる新規作成・上書き・削除は常時ブロックされること
    assert "SECURITY_ALERT" in temp_project.write_file(".gemio/tools/malicious.py", "import os\n")
    assert "SECURITY_ALERT" in temp_project.write_file(".gemio/skills/custom/SKILL.md", "# Skill\n")
    gemio_dir = Path(temp_project.path) / ".gemio" / "skills" / "demo"
    gemio_dir.mkdir(parents=True, exist_ok=True)
    (gemio_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")
    assert "SECURITY_ALERT" in temp_project.delete_path(".gemio/skills/demo/SKILL.md")
    assert "SECURITY_ALERT" in temp_project.delete_path(".gemio")

    # 配下に .env を含むディレクトリの削除・移動の拒否
    sub_dir = Path(temp_project.path) / "config_dir"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / ".env").write_text("SECRET=1", encoding="utf-8")
    assert "SECURITY_ALERT" in temp_project.delete_path("config_dir")
    assert "SECURITY_ALERT" in temp_project.rename_path("config_dir", "config_dir_moved")
    assert (sub_dir / ".env").exists()


# =====================================================================
# 3. プロジェクト探索ツール（get_structure / find_files / search_text）
# =====================================================================

def test_exploration_ignores_bloat_and_protected_dirs(temp_project):
    """get_structure, find_files, search_text が .git, node_modules, .venv, *.egg-info 等を自動除外すること"""
    assert "成功" in temp_project.create_directory("sub/dir")
    temp_project.write_file("sub/dir/sample.py", "print('hi')\n")

    for ignored in (".git", "node_modules", ".venv", "target", "src/gemio.egg-info"):
        ig_dir = Path(temp_project.path) / ignored
        ig_dir.mkdir(parents=True, exist_ok=True)
        (ig_dir / "bloat.txt").write_text("find_me_if_you_can", encoding="utf-8")

    structure = [p.replace("\\", "/") for p in temp_project.get_structure()]
    assert "sub/dir" in structure and "sub/dir/sample.py" in structure
    for prefix in (".git", "node_modules", ".venv", "target"):
        assert not any(p.startswith(prefix) for p in structure)
    assert not any("egg-info" in p for p in structure)
    assert not any("egg-info" in p for p in temp_project.find_files("*"))
    assert temp_project.search_text("find_me_if_you_can") == []

    # *.egg-info への変更・削除ブロック
    assert "SECURITY_ALERT" in temp_project.write_file("src/gemio.egg-info/SOURCES.txt", "modified")
    assert "SECURITY_ALERT" in temp_project.delete_path("src/gemio.egg-info")


def test_get_structure_bfs_depth_limit_and_max_entries_summary(temp_project):
    """
    get_structure が幅優先で探索し、max_depth 到達フォルダの未表示件数案内および
    MAX_STRUCTURE_ENTRIES 超過時のフォルダ別省略通知を返すこと
    """
    temp_project.write_file("a/b/c/d/deep.py", "x = 1\n")
    temp_project.write_file("z_top/readme.md", "# Readme\n")
    (Path(temp_project.path) / "a" / "b" / "c" / ".env").write_text("SECRET=1", encoding="utf-8")

    res_depth2 = [s.replace("\\", "/") for s in temp_project.get_structure(max_depth=2)]
    assert "a" in res_depth2 and "z_top" in res_depth2 and "z_top/readme.md" in res_depth2
    assert any("a/b (※配下に未表示 3 件 -> target_dir='a/b' で展開可)" in s for s in res_depth2)
    assert not any(".env" in s for s in res_depth2)

    res_sub = [s.replace("\\", "/") for s in temp_project.get_structure(target_dir="a/b", max_depth=3)]
    assert "a/b/c/d/deep.py" in res_sub
    assert not any(".env" in s for s in res_sub)

    # 件数上限超過時の省略通知検証
    root = Path(temp_project.path)
    for folder in ("folder_a", "folder_b"):
        d = root / folder
        d.mkdir()
        for i in range(130):
            (d / f"file_{i:03d}.txt").write_text("x", encoding="utf-8")

    res_many = temp_project.get_structure(max_depth=3)
    assert len(res_many) == MAX_STRUCTURE_ENTRIES + 1
    assert "folder_a" in res_many[:6] and "folder_b" in res_many[:6]
    assert "[省略通知]" in res_many[-1] and "folder_b" in res_many[-1]


def test_find_files_and_search_text_filtering_and_limits(temp_project):
    """find_files と search_text のパターン・target_dir 絞り込み・機密除外・上限超過通知が正しく動作すること"""
    temp_project.write_file("src/agent_core.py", "TARGET_TOKEN = 1\n")
    temp_project.write_file("src/utils.py", "pass\n")
    temp_project.write_file("docs/guide.md", "# Guide\n")
    temp_project.write_file(".env.example", "KEY=val\n")

    root = Path(temp_project.path)
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (root / "src" / "private.key").write_text("KEY", encoding="utf-8")

    assert [p.replace("\\", "/") for p in temp_project.find_files("*.py")] == ["src/agent_core.py", "src/utils.py"]
    assert [p.replace("\\", "/") for p in temp_project.find_files("agent")] == ["src/agent_core.py"]
    assert [p.replace("\\", "/") for p in temp_project.find_files("docs/*.md")] == ["docs/guide.md"]

    all_files = [p.replace("\\", "/") for p in temp_project.find_files("*")]
    assert ".env.example" in all_files
    assert ".env" not in all_files and "src/private.key" not in all_files

    # find_files 上限超過
    bulk_dir = root / "bulk_logs"
    bulk_dir.mkdir()
    for i in range(MAX_FIND_RESULTS + 10):
        (bulk_dir / f"log_{i:03d}.log").write_text("log", encoding="utf-8")
    log_res = temp_project.find_files("*.log")
    assert len(log_res) == MAX_FIND_RESULTS + 1
    assert "[省略通知]" in log_res[-1] and "bulk_logs" in log_res[-1]

    # search_text の target_dir 絞り込みと上限超過
    many_lines = "\n".join([f"TARGET_TOKEN = {i}" for i in range(MAX_SEARCH_RESULTS + 15)])
    temp_project.write_file("pkg_b/large.py", many_lines)

    res_src = temp_project.search_text("TARGET_TOKEN", target_dir="src")
    assert len(res_src) == 1
    assert res_src[0]["file_path"].replace("\\", "/") == "src/agent_core.py"

    res_all = temp_project.search_text("TARGET_TOKEN")
    assert len(res_all) == MAX_SEARCH_RESULTS + 1
    assert "[省略通知]" in res_all[-1]["notice"] and "pkg_b" in res_all[-1]["notice"]

    # 不正な target_dir 指定のブロック
    (root / ".ssh").mkdir(exist_ok=True)
    for bad_target in ("../", ".git", ".ssh", ".env"):
        assert "SECURITY_ALERT" in temp_project.get_structure(target_dir=bad_target)
        assert "SECURITY_ALERT" in temp_project.find_files("*", target_dir=bad_target)
        assert "SECURITY_ALERT" in temp_project.search_text("test", target_dir=bad_target)


# =====================================================================
# 4. 承認モード（Normal / Edit / Trust）・サーキットブレーカー・ask_user
# =====================================================================

def test_approval_modes_and_transitions(tmp_path):
    """通常承認・エディット・トラストの3段階承認モードの切り替えと各ツールの承認要否が正しく制御されること"""
    called_actions: list[str] = []

    def mock_approval(desc: str) -> str:
        called_actions.append(desc)
        return "y"

    proj = Project(str(tmp_path), approval_mode=APPROVAL_MODE_NORMAL, approval_callback=mock_approval)
    assert proj.approval_mode == APPROVAL_MODE_NORMAL
    assert proj.require_approval is True
    assert proj.edit_mode is False
    assert proj.trust_mode is False

    # 1. 通常承認モード: 巨大データの書き込み時に承認要求が呼ばれ、プレビュー省略されること
    huge_content = "\n".join([f"line_{i}: " + ("x" * 50) for i in range(100)])
    assert "成功" in proj.write_file("approved.txt", huge_content)
    assert len(called_actions) == 1
    assert "規模:" in called_actions[0] and "省略" in called_actions[0]
    assert "line_99:" not in called_actions[0]

    # 2. エディットモードへ切替: 作成・編集・コピー・リネームは自動承認され、delete_path のみ承認要求されること
    called_actions.clear()
    proj.set_edit_mode(True)
    assert proj.approval_mode == APPROVAL_MODE_EDIT
    assert proj.edit_mode is True
    assert proj.require_approval is False

    assert "成功" in proj.write_file("a.txt", "hello world\n")
    assert "成功" in proj.search_and_replace_file("a.txt", "world", "gemio")
    assert "成功" in proj.copy_file("a.txt", "b.txt")
    assert "成功" in proj.rename_path("b.txt", "c.txt")
    assert len(called_actions) == 0

    assert "成功" in proj.delete_path("c.txt")
    assert len(called_actions) == 1
    assert "ファイル/ディレクトリの削除" in called_actions[0]

    # 3. トラストモードへ切替: delete_path も含めて自動承認されること
    called_actions.clear()
    proj.set_trust_mode(True)
    assert proj.approval_mode == APPROVAL_MODE_TRUST
    assert proj.trust_mode is True
    assert "成功" in proj.delete_path("a.txt")
    assert len(called_actions) == 0


def test_rejection_abort_kill_and_circuit_breaker(tmp_path):
    """
    承認拒否(n)、強制中止(a)、プロセスキル(k)の動作と、
    拒否直後に無断で変更操作を強行した場合のサーキットブレーカー遮断（通常モード・エディットモード両方）を検証
    """
    # 1. 通常モードでの拒否とサーキットブレーカー発動
    proj_reject = Project(str(tmp_path), require_approval=True, approval_callback=lambda _: "n")
    res1 = proj_reject.write_file("first.txt", "content 1")
    assert "拒否されました" in res1 and not (tmp_path / "first.txt").exists()

    with pytest.raises(AgentExecutionAborted) as exc_info:
        proj_reject.write_file("second.txt", "content 2")
    assert "サーキットブレーカー" in str(exc_info.value)

    # 2. エディットモードで delete_path 拒否直後に write_file 等を強行した場合も遮断されること
    proj_edit = Project(str(tmp_path), approval_mode=APPROVAL_MODE_EDIT, approval_callback=lambda _: "n")
    proj_edit.write_file("target.txt", "original")
    assert "拒否されました" in proj_edit.delete_path("target.txt")
    with pytest.raises(AgentExecutionAborted):
        proj_edit.write_file("workaround.txt", "bypass")

    # 3. 'a' (Abort) と 'k' (Kill)
    proj_abort = Project(str(tmp_path), require_approval=True, approval_callback=lambda _: "a")
    with pytest.raises(AgentExecutionAborted):
        proj_abort.write_file("abort.txt", "data")

    proj_kill = Project(str(tmp_path), require_approval=True, approval_callback=lambda _: "k")
    with pytest.raises(SystemExit) as kill_exc:
        proj_kill.write_file("kill.txt", "data")
    assert kill_exc.value.code == 1


def test_ask_user_tool_callback_stdio_fallback_and_circuit_breaker_reset(tmp_path):
    """
    Project.ask_user が agent_tools() に含まれ、コールバック委譲・標準入力フォールバック・
    および承認拒否後のサーキットブレーカー保留解除を行うこと
    """
    decisions = iter(["n", "y"])
    proj = Project(
        str(tmp_path),
        require_approval=True,
        approval_callback=lambda _: next(decisions),
        ask_user_callback=lambda q, opts, ctx: f"回答:{q}",
    )
    assert proj.ask_user in proj.agent_tools()

    # 書き込み拒否で _rejection_pending が True になる
    assert "拒否されました" in proj.write_file("a.txt", "hello")
    assert proj._rejection_pending is True

    # ask_user でユーザーに相談すると _rejection_pending が False にリセットされ、次の書き込みが通ること
    reply = proj.ask_user("どうしますか？", options=["案1", "案2"], context="背景")
    assert reply == "回答:どうしますか？"
    assert proj._rejection_pending is False
    assert "成功" in proj.write_file("b.txt", "hello world")

    # コールバック未指定時の標準入力フォールバック検証
    proj_stdio = Project(str(tmp_path))
    opts = ["JSON形式", "YAML形式"]
    with patch("builtins.input", side_effect=["2"]):
        assert proj_stdio.ask_user("形式は？", options=opts) == "YAML形式"
    with patch("builtins.input", side_effect=["0", "TOML形式で"]):
        assert proj_stdio.ask_user("形式は？", options=opts) == "TOML形式で"
    with patch("builtins.input", side_effect=["直接指定テキスト"]):
        assert proj_stdio.ask_user("形式は？", options=opts) == "直接指定テキスト"
    with patch("builtins.input", side_effect=["a"]):
        with pytest.raises(AgentExecutionAborted):
            proj_stdio.ask_user("形式は？", options=opts)


def test_project_get_system_instruction_contains_project_rules_only(temp_project):
    """
    Project.get_system_instruction() が ask_user・ファイル変更・探索ルールを含み、
    DeveloperProject や CLI 固有の指示（run_sandbox_command, /search）を含まないこと
    """
    from gemio.project import PROJECT_SYSTEM_INSTRUCTION

    inst = temp_project.get_system_instruction()
    assert inst == PROJECT_SYSTEM_INSTRUCTION.strip()
    assert "【ユーザーへの確認・質問（ask_userツール）に関するルール】" in inst
    assert "【コード・ファイル変更に関するルール】" in inst
    assert "syntax_check" in inst
    assert "【コード参照に関するルール】" in inst
    assert "get_structure" in inst and "[省略通知]" in inst
    assert "run_sandbox_command" not in inst
    assert "/search" not in inst
