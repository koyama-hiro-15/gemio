# #018 Web検索・Web検索支援機能の全面廃止と作業前提資料 (`/materials`) の手動専用化 設計書

**作成日:** 2026年11月24日  
**最終更新日:** 2026年11月24日  
**ステータス:** 実装完了（現行）  
**対象システム:** `src/gemio/` (`core/`, `app/`, `tui/`, `cli/`, `__init__.py`), `tests/`, `docs/`, `manuals/`  

### 【要約】
`#017` で導入した人間介在型 Web 検索支援（`request_user_search` ツール、`UserSearchModal`、`+WEB` / `-WEB` スイッチ等）について、実運用の検証の結果、ユーザーによる都度検索では十分な情報取得が難しく実用面に課題があること、および複数回の検索往復が操作負担となることから、**Web検索およびWeb検索支援に関する機能を一旦全面廃止**します。一方で、ユーザーが明示的に参照資料を手動登録・管理しプロンプト先頭へ自動注入する**作業前提資料管理（`MaterialStore` / `/materials` / `MaterialsModal`、および入力テキスト診断・無害化）は手動資料登録機能として継続維持**し、`core/context/materials.py` へ統合整理します。

---

## 1. 背景と廃止判断の理由

1. [`#016`](./016_agent_delegated_web_search.md) の Google Search Grounding 廃止後、[`#017`](./017_human_assisted_web_search.md) にて `request_user_search` ツールと `UserSearchModal` を介した人間介在型 Web 検索支援を実装した。
2. しかし実用テストの結果、以下の課題が確認された：
   * ユーザー自身がブラウザ検索結果から有用な情報過不足なく抜粋して返す運用では、意図した技術仕様が十分に得られにくい。
   * 調査・確認のたびに検索ダイアログが割り込み、何度もブラウザ検索〜コピー＆ペーストを繰り返すことは実用上困難である。
3. このため、AI主導または対話中の Web 検索支援フロー・関連ツール・スイッチ等の Web 機能はすべて廃止し、Gemio の責務をローカルプロジェクト操作・サンドボックス実行・手動提供資料に基づく支援へ集中させる。

---

## 2. 変更方針

1. **Web機能および関連コンポーネントの全面廃止**:
   * **ツール・コールバック・スイッチ**: `request_user_search` ツール、`UserSearchRequest` / `UserSearchReply`、`InteractionHandler.request_user_search`、`+WEB` / `-WEB` 状態・トグル・ヘッダーバッジ (`#hdr-web`)、`/web` コマンド、`--no-web-search` CLI オプションをすべて削除する。
   * **UI画面・ログ種別**: `UserSearchModal` (`src/gemio/tui/screens/user_search.py`)、`AgentLogCategory.SEARCH`、`ActivityGroup.add_search_info`、`TokenUsageTracker` の `search_api_calls` を削除する。
   * **システムプロンプト**: `APP_WEB_SEARCH_ENABLED_INSTRUCTION` / `APP_WEB_SEARCH_DISABLED_INSTRUCTION` / `build_web_search_instruction` を廃止し、`APP_MATERIALS_GUIDANCE_INSTRUCTION`（「Web機能は搭載されていないため、手元のファイルや資料で確認できない外部情報が必要な場合は推測せず作業を中断し、プロンプトまたは `/materials` からの情報提供を案内する」）へ整理する。
2. **作業前提資料（`/materials` / `MaterialStore`）と入力診断の統合・維持**:
   * `src/gemio/core/search.py` を廃止・削除し、汎用的な入力テキスト診断・無害化機能（`diagnose_user_pasted_text`, `sanitize_pasted_text`, `InputTextDiagnosis`, `SizeLevel`, `DIAGNOSIS_*`）を `src/gemio/core/context/materials.py` へ移設する。
   * `MaterialsModal`（`/materials`）、`GemioController` の資料管理 API（`list_materials`, `add_user_material`, `remove_material`, `clear_materials`）、ヘッダーの `M:N` カウンタ、および `/clear --all` は手動資料登録機能として維持する。

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-24 | AI Agent | 実装完了（現行） | 初版作成。Web検索支援機能の全面廃止、入力テキスト診断の `core/context/materials.py` 統合、全89件の単体・ヘッドレスTUIテスト合格および関連ドキュメント同期を完了。 |
