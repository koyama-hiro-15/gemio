from dataclasses import fields
from unittest.mock import MagicMock, patch
from google.genai import types

from gemio.core.agent import ChatAgent
from gemio.core.client import GenAIClient
from gemio.core.prompts import SystemPromptComposer
from gemio.core.skills import (
    SkillLoader,
    SkillManager,
    SkillSpec,
    parse_skill_markdown,
)
from gemio.core.tools import (
    DynamicToolsetProvider,
    ToolRegistry,
    get_builtin_toolset,
)
from gemio.project import Project


def test_parse_skill_markdown_and_skill_spec_has_no_path_fields():
    """
    parse_skill_markdown がフロントマター（インライン配列・リスト記法）と本文を正しく分離し、
    SkillSpec に物理パス属性やパス露出 repr が一切存在しないこと
    """
    spec_fields = {f.name for f in fields(SkillSpec)}
    assert "source_path" not in spec_fields
    assert "file_path" not in spec_fields
    assert "path" not in spec_fields

    raw_inline = (
        "---\n"
        'name: "python-qa"\n'
        'description: "Pythonコードの静的検証とテスト手順"\n'
        "recommended_toolsets: [project_read, sandbox]\n"
        "---\n\n"
        "# 手順\n1. syntax_check を実行する\n2. pytest を実行する\n"
    )
    meta1, body1 = parse_skill_markdown(raw_inline, fallback_name="fallback")
    assert meta1["name"] == "python-qa"
    assert meta1["description"] == "Pythonコードの静的検証とテスト手順"
    assert meta1["recommended_toolsets"] == ["project_read", "sandbox"]
    assert body1.startswith("# 手順")

    raw_bullets = (
        "---\n"
        "recommended_toolsets:\n"
        "  - project_explore\n"
        "  - project_write\n"
        "---\n"
        "フロントマターにdescriptionが無い場合は本文1行目が説明になります。\n"
    )
    meta2, body2 = parse_skill_markdown(raw_bullets, fallback_name="auto-skill")
    assert meta2["name"] == "auto-skill"
    assert "本文1行目が説明になります" in meta2["description"]
    assert meta2["recommended_toolsets"] == ["project_explore", "project_write"]
    assert "本文1行目" in body2


def test_skill_loader_fixed_directories_override_and_path_privacy(tmp_path):
    """
    SkillLoader が固定2箇所（builtin/ と .gemio/skills/）のみを走査し、
    プロジェクト側優先上書き・シンボリックリンク除外・遅延ロード失敗時の物理パス秘匿を守ること
    """
    fake_builtin_root = tmp_path / "pkg_builtin"
    (fake_builtin_root / "common-doc").mkdir(parents=True)
    (fake_builtin_root / "common-doc" / "SKILL.md").write_text(
        "---\nname: doc-style\ndescription: 標準ドキュメント規約\n---\n標準の本文です。",
        encoding="utf-8",
    )

    proj_root = tmp_path / "my_secret_user_workspace"
    proj_skills = proj_root / ".gemio" / "skills"
    (proj_skills / "doc-style").mkdir(parents=True)
    (proj_skills / "doc-style" / "SKILL.md").write_text(
        "---\nname: doc-style\ndescription: プロジェクト固有ドキュメント規約\n---\n上書きされた本文です。",
        encoding="utf-8",
    )

    (proj_skills / "vanish-skill").mkdir(parents=True)
    vanish_md = proj_skills / "vanish-skill" / "SKILL.md"
    vanish_md.write_text(
        "---\nname: vanish-skill\ndescription: 後で消えるスキル\n---\n本文",
        encoding="utf-8",
    )

    # アンダースコア始まりおよびシンボリックリンクは無視されること
    (proj_skills / "_hidden").mkdir()
    (proj_skills / "_hidden" / "SKILL.md").write_text("# Hidden", encoding="utf-8")
    outside_dir = tmp_path / "outside_skill"
    outside_dir.mkdir()
    (outside_dir / "SKILL.md").write_text("# Outside", encoding="utf-8")
    try:
        (proj_skills / "linked-skill").symlink_to(outside_dir)
    except OSError:
        pass

    with patch("gemio.core.skills.loader.BUILTIN_SKILLS_DIR", fake_builtin_root):
        specs = SkillLoader.load_all(project_root=proj_root)

    by_name = {s.name: s for s in specs}
    assert set(by_name.keys()) == {"doc-style", "vanish-skill"}
    assert by_name["doc-style"].description == "プロジェクト固有ドキュメント規約"
    assert by_name["doc-style"].load_body() == "上書きされた本文です。"
    assert "my_secret_user_workspace" not in repr(by_name["doc-style"])

    # 遅延ロード直前にファイルが消えた場合でも、エラー応答に物理パスが一切露出しないこと
    vanish_md.unlink()
    mgr = SkillManager(skills=specs)
    err_msg = mgr.activate("vanish-skill")
    assert "[ERROR]" in err_msg and "vanish-skill" in err_msg
    assert "my_secret_user_workspace" not in err_msg
    assert str(tmp_path) not in err_msg


def test_dynamic_toolset_provider_and_skill_mid_loop_activation(tmp_path):
    """
    DynamicToolsetProvider と SkillManager を組み合わせたエージェントループ検証:
    1. 初期状態では always_active ツール + request_tools + activate_skill のみが公開されること
    2. 未有効ツールを直接呼んだ場合は request_tools の使い方を案内して自己修復を促すこと
    3. ループ内で request_tools や activate_skill を呼ぶと、直後のイテレーションから
       要求されたツールセットとスキルの詳細手順が有効化されて作業を完遂できること
    """
    proj = Project(str(tmp_path), require_approval=False)
    registry = ToolRegistry()
    for ts in proj.agent_toolsets():
        registry.register_toolset(ts)
    registry.register_toolset(get_builtin_toolset())

    provider = DynamicToolsetProvider(registry=registry)

    skill = SkillSpec(
        name="code-writer-guide",
        description="ファイル作成時のヘッダー規約スキル",
        recommended_toolsets=["project_read"],
        _body_loader=lambda: "ファイルの先頭には必ず `# Header` を記載すること。",
    )
    mgr = SkillManager(
        skills=[skill],
        on_skill_activated=lambda s: provider.activate_toolsets(s.recommended_toolsets),
    )
    provider.register_extra_tools(mgr.get_meta_tools())

    composer = SystemPromptComposer(
        base_instruction="基本ルール",
        tool_provider=provider,
        skill_manager=mgr,
    )

    # 1. 初期状態の確認: write_file や read_file はまだ API ツール一覧に含まれない
    initial_tools = [fn.__name__ for fn in provider.get_active_tools()]
    assert "request_tools" in initial_tools
    assert "activate_skill" in initial_tools
    assert "ask_user" in initial_tools
    assert "get_current_date" in initial_tools
    assert "write_file" not in initial_tools
    assert "read_file" not in initial_tools

    # 2. 未有効ツールを直接実行しようとした場合の案内検証
    unavail_msg = provider.execute("write_file", {"file_path": "a.py", "content": "x"})
    assert "[ERROR]" in unavail_msg
    assert "project_write" in unavail_msg and "request_tools" in unavail_msg

    # 3. ChatAgent ループ内でのオンデマンド有効化と実行
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        # Step 1: AIが activate_skill('code-writer-guide') と request_tools(toolsets=['project_write']) を呼ぶ
        fc_skill = MagicMock()
        fc_skill.name = "activate_skill"
        fc_skill.args = {"skill_name": "code-writer-guide"}

        fc_req = MagicMock()
        fc_req.name = "request_tools"
        fc_req.args = {"toolsets": ["project_write"]}

        c1 = MagicMock(
            content=types.Content(
                role="model",
                parts=[
                    types.Part.from_function_call(name="activate_skill", args=fc_skill.args),
                    types.Part.from_function_call(name="request_tools", args=fc_req.args),
                ],
            )
        )

        # Step 2: 有効化された write_file を呼んで実際にファイルを作成する
        fc_write = MagicMock()
        fc_write.name = "write_file"
        fc_write.args = {"file_path": "hello.py", "content": "# Header\nprint('ok')\n"}
        c2 = MagicMock(
            content=types.Content(
                role="model",
                parts=[types.Part.from_function_call(name="write_file", args=fc_write.args)],
            )
        )

        # Step 3: 最終テキスト応答
        mock_sdk.models.generate_content.side_effect = [
            MagicMock(function_calls=[fc_skill, fc_req], candidates=[c1]),
            MagicMock(function_calls=[fc_write], candidates=[c2]),
            MagicMock(function_calls=None, text="規約に沿って hello.py を作成しました。"),
        ]

        agent = ChatAgent(
            client=GenAIClient(),
            tool_provider=provider,
            prompt_composer=composer,
        )
        result = agent.process_prompt("規約に沿って hello.py を作って", on_log_append=lambda _: None)

        assert result.response_text == "規約に沿って hello.py を作成しました。"
        assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "# Header\nprint('ok')\n"

        api_calls = mock_sdk.models.generate_content.call_args_list
        # 1回目のリクエストではスキル本文はまだ無く、write_file も未登録
        cfg_step1 = api_calls[0].kwargs["config"]
        assert "【利用可能なSkills" in cfg_step1["system_instruction"]
        assert "【有効化中のSkills" not in cfg_step1["system_instruction"]
        assert "write_file" not in [f.__name__ for f in cfg_step1["tools"]]

        # 2回目のリクエストではスキル本文・recommended_toolsets(read_file)・要求した write_file が全て有効！
        cfg_step2 = api_calls[1].kwargs["config"]
        step2_tool_names = [f.__name__ for f in cfg_step2["tools"]]
        assert "write_file" in step2_tool_names
        assert "read_file" in step2_tool_names  # スキルの recommended_toolsets 連動
        assert "ファイルの先頭には必ず `# Header`" in cfg_step2["system_instruction"]

    # リセット後は初期の軽量状態に戻ること
    mgr.clear_active()
    provider.reset_active_tools()
    after_reset_tools = [fn.__name__ for fn in provider.get_active_tools()]
    assert "write_file" not in after_reset_tools
    assert "read_file" not in after_reset_tools
    assert mgr.active_skill_names == []
