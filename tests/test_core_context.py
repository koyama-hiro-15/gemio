from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from google.genai import types

from gemio.core.client import GenAIClient, TokenUsageTracker
from gemio.core.context import (
    ChatHistory,
    ChatSession,
    ChatTurn,
    ContextCompactor,
    MaterialKind,
    MaterialStore,
    ReferenceMaterial,
    compute_sha256,
)


def _build_sample_turn(
    turn_id: str,
    user_prompt: str,
    assistant_response: str,
    tool_name: str | None = None,
    thoughts: list[str] | None = None,
    created_at: str = "12:00:00",
) -> ChatTurn:
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])]
    tool_cnt = 0
    if tool_name:
        contents.append(
            types.Content(
                role="model",
                parts=[types.Part.from_function_call(name=tool_name, args={"target_dir": "."})],
            )
        )
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name=tool_name, response={"result": "ok"})],
            )
        )
        tool_cnt = 1
    contents.append(
        types.Content(role="model", parts=[types.Part.from_text(text=assistant_response)])
    )
    return ChatTurn(
        turn_id=turn_id,
        user_prompt=user_prompt,
        contents=contents,
        assistant_response=assistant_response,
        thoughts=list(thoughts or []),
        tool_call_count=tool_cnt,
        created_at=created_at,
    )


def test_chat_history_turn_management_rewind_and_summary_expansion():
    """
    ChatHistory がターン（ChatTurn）単位で複数メッセージ（ツール呼び出し往復を含む）を保持し、
    rewind でロール規約やペア整合性を壊さずに巻き戻せること、および summary_text が先頭展開されること
    """
    history = ChatHistory()
    t1 = _build_sample_turn(history.next_turn_id(), "最初の質問", "最初の回答")
    t2 = _build_sample_turn(
        history.next_turn_id(),
        "ファイルを調べて",
        "構成を確認しました。",
        tool_name="get_structure",
    )

    history.append_turn(t1)
    history.append_turn(t2)
    assert history.turn_count == 2
    assert history.message_count == 6
    assert history.last_turn is t2
    assert t2.extract_tool_names() == ["get_structure"]

    # 1ターン巻き戻し -> ツール呼び出し4メッセージ分のターン2が丸ごと除去され、ターン1のみが残ること
    removed = history.rewind(steps=1)
    assert len(removed) == 1 and removed[0].user_prompt == "ファイルを調べて"
    assert history.turn_count == 1
    assert history.message_count == 2
    flat = history.to_contents()
    assert [c.role for c in flat] == ["user", "model"]

    # summary_text 設定時は先頭に user/model ペアが自動展開されること
    history.summary_text = "これまでに基盤設計を完了した。"
    flat_with_summary = history.to_contents()
    assert len(flat_with_summary) == 4
    assert "【これまでの対話の要約】" in flat_with_summary[0].parts[0].text
    assert flat_with_summary[1].role == "model"


def test_chat_history_range_selection_and_clipboard_markdown_extraction():
    """
    プロンプト・回答の単体取得、ターン番号・IDによる参照、範囲/直近N件の取得、
    およびクリップボードコピー・再表示用 Markdown 生成（思考テキスト有無含む）が正しく動作すること
    """
    history = ChatHistory()
    assert history.get_last_prompt_text() == ""
    assert history.get_last_response_text() == ""
    assert history.get_turn(1) is None

    for i in range(1, 5):
        history.append_turn(
            _build_sample_turn(
                history.next_turn_id(),
                f"質問 {i}",
                f"回答 {i}",
                thoughts=[f"思考プロセス {i}"],
                created_at=f"10:0{i}:00",
            )
        )

    assert history.get_last_prompt_text() == "質問 4"
    assert history.get_last_response_text() == "回答 4"
    assert history.get_turn(2).user_prompt == "質問 2"
    assert history.get_turn(-1).user_prompt == "質問 4"
    assert history.get_turn("t3").user_prompt == "質問 3"
    assert history.get_turn(99) is None

    # 範囲指定 (start=2, end=3) および直近指定 (last_n=2)
    ranged = history.get_turns(start=2, end=3)
    assert [t.turn_id for t in ranged] == ["t2", "t3"]

    recent = history.get_turns(last_n=2)
    assert [t.turn_id for t in recent] == ["t3", "t4"]

    # Markdown 整形出力の検証
    history.summary_text = "事前要約サマリ"
    md_out = history.format_turns_markdown(
        turns=recent,
        include_summary=True,
        include_thoughts=True,
    )
    assert "## これまでの対話の要約\n\n事前要約サマリ" in md_out
    assert "### Turn 1 (t3) [10:03:00]" in md_out
    assert "**User:**\n質問 3" in md_out
    assert "**Thoughts:**\n思考プロセス 3" in md_out
    assert "**Gemio:**\n回答 3" in md_out


def test_material_store_no_local_paths_hash_management_and_display_format():
    """
    MaterialStore が物理ファイルパス属性を一切持たず、
    SHA-256 content_hash 重複防止、source_hash 差し替え更新、get 参照、remove 個別削除、
    およびプロンプト用・再表示用フォーマットを行うこと
    """
    field_names = {f.name for f in fields(ReferenceMaterial)}
    assert "file_path" not in field_names
    assert "source_path" not in field_names
    assert "mtime" not in field_names

    store = MaterialStore()
    redirect_url = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AQqDxyz123=="
    m1, added1 = store.add_search_result(
        query="Python 3.15 新機能",
        response_text="PEP 810 の解説本文",
        sources=[{"title": "PEP 810 | peps.python.org", "url": redirect_url}],
        created_at="10:00:00",
    )
    assert added1 is True and m1.kind == MaterialKind.WEB_SEARCH

    # 同一内容の重複追加防止
    m1_dup, added_dup = store.add_search_result("別クエリ", "  PEP 810 の解説本文  ", [])
    assert added_dup is False and m1_dup.material_id == m1.material_id

    # ユーザー資料の追加・重複防止・source_hash 更新
    opaque_source_hash = compute_sha256("logical_doc_identifier_1")
    m2, st_add = store.add_user_document(
        title="アーキテクチャ仕様メモ",
        content="初期仕様バージョン1",
        source_hash=opaque_source_hash,
        created_at="10:05:00",
    )
    assert st_add == "added"
    assert store.get(2) is m2
    assert store.get(m1.material_id) is m1

    _, st_dup = store.add_user_document("別名", "初期仕様バージョン1", source_hash=opaque_source_hash)
    assert st_dup == "duplicate"

    m2_upd, st_upd = store.add_user_document(
        title="アーキテクチャ仕様メモ",
        content="改訂仕様バージョン2",
        source_hash=opaque_source_hash,
        created_at="10:10:00",
    )
    assert st_upd == "updated" and m2_upd.content == "改訂仕様バージョン2"
    assert store.total_char_count == len("PEP 810 の解説本文") + len("改訂仕様バージョン2")

    # プロンプト用およびユーザー再表示用フォーマット検証
    formatted_prompt = store.format_for_prompt()
    assert "【作業前提資料】" in formatted_prompt
    assert "vertexaisearch" not in formatted_prompt
    assert opaque_source_hash not in formatted_prompt

    formatted_disp = store.format_for_display()
    assert "[Web検索] Python 3.15 新機能" in formatted_disp
    assert "[ユーザー資料] アーキテクチャ仕様メモ" in formatted_disp
    assert "vertexaisearch" not in formatted_disp

    # 個別削除
    assert store.remove("1").material_id == m1.material_id
    assert store.remove(m2.material_id) is m2
    assert len(store) == 0


def test_context_compactor_summarizes_old_turns_and_preserves_recent_turns():
    """
    ContextCompactor（および ChatSession.compact）が：
    1. ターン数が keep_recent_turns 以下の場合はAPIを呼ばずスキップすること
    2. 古いターン群と既存要約を統合して要約圧縮し、compaction_api_calls としてトークン計上すること
    3. 万一APIが空テキストを返した場合は既存の履歴を破壊せずスキップすること
    """
    with patch("google.genai.Client") as mock_client_cls:
        mock_sdk = MagicMock()
        mock_client_cls.return_value = mock_sdk

        client = GenAIClient()
        session = ChatSession()
        session.materials.add_user_document("前提ルール", "テスト必須")

        # ターン2件のみ -> keep_recent_turns=2 ではAPI呼び出しなしでスキップされること
        session.history.append_turn(_build_sample_turn("t1", "調査して", "調査完了", tool_name="find_files"))
        session.history.append_turn(_build_sample_turn("t2", "設計して", "設計完了"))

        skip_res = session.compact(client, keep_recent_turns=2)
        assert skip_res.compacted is False
        assert skip_res.summarized_turns == 0
        assert mock_sdk.models.generate_content.call_count == 0

        # ターン4件に増加 -> 古い2件(t1, t2)が要約され、直近2件(t3, t4)が生のまま残ること
        session.history.append_turn(_build_sample_turn("t3", "実装して", "実装完了", tool_name="write_file"))
        session.history.append_turn(_build_sample_turn("t4", "テストして", "全件パス"))

        mock_sdk.models.generate_content.return_value = MagicMock(
            text="1. **目的**: リファクタリング\n2. **実施済み**: 調査と設計を完了",
            usage_metadata=SimpleNamespace(
                prompt_token_count=900,
                candidates_token_count=150,
                thoughts_token_count=50,
                cached_content_token_count=0,
                tool_use_prompt_token_count=0,
                total_token_count=1100,
            ),
        )

        comp_res = session.compact(client, keep_recent_turns=2)
        assert comp_res.compacted is True
        assert comp_res.summarized_turns == 2
        assert comp_res.retained_turns == 2
        assert "調査と設計を完了" in session.history.summary_text
        assert [t.turn_id for t in session.history.turns] == ["t3", "t4"]

        # 要約プロンプトにツール実行情報が含まれていたことを検証
        sent_prompt = mock_sdk.models.generate_content.call_args.kwargs["contents"][0].parts[0].text
        assert "find_files" in sent_prompt
        assert "調査して" in sent_prompt and "設計して" in sent_prompt
        assert "テストして" not in sent_prompt  # 直近ターンは圧縮対象に含めない

        # トークン統計で compaction_api_calls に記録されていること
        snap = client.token_tracker.get_snapshot()
        assert snap["compaction_api_calls"] == 1
        assert snap["cumulative_total_tokens"] == 1100

        # 2回目の圧縮で万一空応答が返った場合は履歴が破壊されないこと
        session.history.append_turn(_build_sample_turn("t5", "ドキュメント更新", "完了"))
        mock_sdk.models.generate_content.return_value = MagicMock(text="   ", usage_metadata=None)
        empty_res = ContextCompactor(client).compact(session.history, keep_recent_turns=1)
        assert empty_res.compacted is False
        assert session.history.turn_count == 3

        # export_context_markdown で前提資料＋要約＋選択ターンが一括出力できること
        exported = session.export_context_markdown(last_n=1, include_summary=True, include_materials=True)
        assert "## 作業前提資料" in exported
        assert "## これまでの対話の要約" in exported
        assert "ドキュメント更新" in exported


def test_chat_session_injects_materials_without_polluting_history():
    """
    ChatSession.build_request_contents が直近のユーザー指示の先頭にのみ作業前提資料を注入し、
    履歴本体（ChatTurn）を一切肥大化させないこと、および reset が正しく動作すること
    """
    tracker = TokenUsageTracker()
    session = ChatSession()
    session.materials.add_user_document(title="設計ルール", content="依存方向は単方向とすること。")

    pending = [
        types.Content(role="user", parts=[types.Part.from_text(text="モジュールを作成して")]),
    ]
    req_contents = session.build_request_contents(pending_contents=pending)

    assert len(req_contents) == 1
    sent_text = req_contents[0].parts[0].text
    assert "【作業前提資料】" in sent_text
    assert "依存方向は単方向とすること。" in sent_text
    assert "【ユーザーの指示】\nモジュールを作成して" in sent_text
    assert pending[0].parts[0].text == "モジュールを作成して"

    session.history.append_turn(
        _build_sample_turn("t1", "モジュールを作成して", "作成しました。")
    )
    summary_keep = session.reset(clear_materials=False, token_tracker=tracker)
    assert summary_keep["cleared_messages"] == 2
    assert summary_keep["retained_materials"] == 1

    summary_clear = session.reset(clear_materials=True, token_tracker=tracker)
    assert summary_clear["cleared_materials"] == 1
    assert len(session.materials) == 0
