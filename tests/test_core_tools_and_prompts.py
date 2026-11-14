import re
from unittest.mock import patch

from gemio.core.prompts import (
    DEFAULT_SEARCH_INSTRUCTION,
    DEFAULT_SYSTEM_INSTRUCTION,
    DEVELOPER_MODE_INSTRUCTION_ADDON,
    SystemPromptComposer,
    build_system_instruction,
)
from gemio.core.tools import (
    StaticToolProvider,
    ToolProvider,
    ToolRegistry,
    Toolset,
    agent_tools,
    extract_tool_summary,
    get_builtin_toolset,
    get_current_date,
    get_current_time,
    load_project_toolsets,
)
from gemio.project import DeveloperProject, Project


def test_get_current_date_and_time_and_builtin_toolset():
    """日時取得ツールおよび標準 get_builtin_toolset() が正しい形式と設定を返すこと"""
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", get_current_date())
    assert re.match(r"^\d{2}:\d{2}:\d{2}$", get_current_time())
    assert agent_tools == [get_current_date, get_current_time]

    builtin_ts = get_builtin_toolset()
    assert builtin_ts.always_active is True
    assert [s.func for s in builtin_ts.tools] == [get_current_date, get_current_time]


def test_tool_registry_and_static_provider_execution():
    """
    ToolRegistry と StaticToolProvider が Docstring 先頭概要の自動抽出、ツールセット管理、
    メタツール追加、および未知/未有効ツール呼び出し時の安全エラー応答を行うこと
    """
    def calc_checksum(text: str) -> str:
        """
        入力文字列の簡易チェックサムを返します。
        Args:
            text: 対象文字列
        """
        return f"sum:{len(text)}"

    assert extract_tool_summary(calc_checksum) == "入力文字列の簡易チェックサムを返します。"

    registry = ToolRegistry()
    registry.register_functions([calc_checksum], toolset_name="crypto", description="計算補助")
    provider = StaticToolProvider(registry=registry)

    def meta_helper() -> str:
        return "meta_ok"

    provider.register_extra_tools([meta_helper])
    active_names = [fn.__name__ for fn in provider.get_active_tools()]
    assert active_names == ["calc_checksum", "meta_helper"]

    assert provider.execute("calc_checksum", {"text": "abc"}) == "sum:3"
    assert provider.execute("meta_helper", {}) == "meta_ok"

    unknown_res = provider.execute("missing_tool", {})
    assert "[ERROR]" in unknown_res and "missing_tool" in unknown_res


def test_load_project_toolsets_fixed_directory_and_path_privacy(tmp_path):
    """
    load_project_toolsets が固定ディレクトリ `.gemio/tools/*.py` のみから安全に拡張ツールを読み込み、
    アンダースコア始まり・シンボリックリンクを無視し、エラー警告にホスト物理パスを含めないこと
    """
    # ディレクトリ未作成時は空リスト
    assert load_project_toolsets(tmp_path) == []

    tools_dir = tmp_path / ".gemio" / "tools"
    tools_dir.mkdir(parents=True)

    # 1. agent_tools 形式の有効モジュール
    (tools_dir / "formatter.py").write_text(
        '"""コード整形補助ツール群"""\n'
        "def to_upper(s: str) -> str:\n"
        '    """文字列を大文字化します。"""\n'
        "    return s.upper()\n"
        "agent_tools = [to_upper]\n",
        encoding="utf-8",
    )

    # 2. TOOLSET 明示定義モジュール
    (tools_dir / "custom_set.py").write_text(
        "from gemio.core.tools import Toolset\n"
        "def ping_tool() -> str:\n"
        '    """疎通確認を返します。"""\n'
        "    return 'pong'\n"
        "TOOLSET = Toolset.from_callables('net_diag', '診断ツールセット', [ping_tool])\n",
        encoding="utf-8",
    )

    # 3. _ 始まりファイル（無視対象）および構文エラーファイル（安全スキップ＋物理パス秘匿検証）
    (tools_dir / "_private_helper.py").write_text("agent_tools = [lambda: 1]\n", encoding="utf-8")
    (tools_dir / "broken_mod.py").write_text("def broken(\n", encoding="utf-8")

    # 4. シンボリックリンク（無視対象）
    outside_py = tmp_path / "outside_tool.py"
    outside_py.write_text("agent_tools = [lambda: ' leaked ']\n", encoding="utf-8")
    try:
        (tools_dir / "linked_tool.py").symlink_to(outside_py)
    except OSError:
        pass

    warnings: list[str] = []
    loaded = load_project_toolsets(tmp_path, on_warning=warnings.append)

    loaded_names = {ts.name for ts in loaded}
    assert loaded_names == {"ext_formatter", "net_diag"}

    # 警告メッセージに broken_mod の論理名のみが含まれ、tmp_path（物理パス）が含まれないこと
    assert len(warnings) == 1
    assert "broken_mod" in warnings[0]
    assert str(tmp_path) not in warnings[0]


def test_project_and_developer_project_agent_toolsets_match_agent_tools(tmp_path):
    """Project および DeveloperProject の agent_toolsets() が agent_tools() と過不足なく一致すること"""
    proj = Project(str(tmp_path), require_approval=True)
    proj_toolsets = proj.agent_toolsets()
    ts_funcs = {spec.func for ts in proj_toolsets for spec in ts.tools}
    assert ts_funcs == set(proj.agent_tools())
    assert any(ts.name == "project_interaction" and ts.always_active for ts in proj_toolsets)

    with patch.object(DeveloperProject, "_is_running_in_container", return_value=True), \
         patch.object(DeveloperProject, "_validate_in_container_security"):
        dev_proj = DeveloperProject(str(tmp_path), require_approval=True)
        dev_toolsets = dev_proj.agent_toolsets()
        dev_ts_funcs = {spec.func for ts in dev_toolsets for spec in ts.tools}
        assert dev_ts_funcs == set(dev_proj.agent_tools())
        assert any(ts.name == "sandbox" for ts in dev_toolsets)


def test_build_system_instruction_and_dynamic_prompt_composer():
    """
    build_system_instruction および SystemPromptComposer（動的カタログ・Skills・動的セクション合成）が
    正しくインストラクション文字列を構築すること
    """
    std_inst = build_system_instruction(dev_mode=False)
    assert std_inst == DEFAULT_SYSTEM_INSTRUCTION
    assert "【ユーザーへの確認・質問（ask_userツール）に関するルール】" in std_inst
    assert "【作業前提資料の取り扱いに関するルール】" in std_inst
    assert "run_sandbox_command" not in std_inst

    dev_inst = build_system_instruction(dev_mode=True)
    assert DEVELOPER_MODE_INSTRUCTION_ADDON in dev_inst
    assert ".gemio/" in dev_inst

    assert "Google検索ツールを活用し" in DEFAULT_SEARCH_INSTRUCTION

    # SystemPromptComposer の動的評価テスト
    class DummyDynamicProvider(ToolProvider):
        def get_active_tools(self):
            return []

        def get_catalog_prompt(self) -> str:
            return "【ツールカタログ】\n- file_read"

    class DummySkillManager:
        def __init__(self):
            self.active_body = ""

        def build_catalog_prompt(self) -> str:
            return "【利用可能スキル】\n- python-testing"

        def build_active_skills_prompt(self) -> str:
            return self.active_body

    skill_mgr = DummySkillManager()
    state = {"phase": "init"}
    composer = SystemPromptComposer(
        base_instruction="ベース指示",
        dev_mode=True,
        tool_provider=DummyDynamicProvider(),
        skill_manager=skill_mgr,
        extra_sections=[lambda: f"現在フェーズ: {state['phase']}"],
    )

    composed_1 = composer.compose()
    assert "ベース指示" in composed_1
    assert "run_sandbox_command" in composed_1
    assert "【ツールカタログ】" in composed_1
    assert "【利用可能スキル】" in composed_1
    assert "現在フェーズ: init" in composed_1

    # 状態変化が次回の compose() に即時反映されること
    skill_mgr.active_body = "【有効スキル手順】\npytest を必ず実行すること"
    state["phase"] = "active"
    composed_2 = composer.compose()
    assert "【有効スキル手順】" in composed_2
    assert "現在フェーズ: active" in composed_2
