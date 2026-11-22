# 🗂️ Gemio 設計書・技術ドキュメント一覧（インデックス）

**作成日:** 2026年11月17日  
**最終更新日:** 2026年11月23日  
**ステータス:** 運用中  
**対象範囲:** `docs/` 配下の設計書（`design/`）、技術調査資料（`tech_docs/`）、運用ルール、および開発履歴サマリー  

### 【要約】
本ディレクトリ（`docs/`）には、Gemioの各機能・リファクタリングにおける**設計判断の記録（`docs/design/NNN_*.md`）**、コンテナ・セキュリティに関する**技術調査資料（`docs/tech_docs/`）**、および時系列の**開発履歴サマリー（`PROJECT_SUMMARY.md`）**が格納されています。開発者およびAIエージェントが設計経緯や関連仕様を素早く辿れるよう、全ドキュメントのステータスと概要を一覧化しています。

---

## 🧭 参照時の重要ガイド（最初に必ずご確認ください）

1. **「現在の最新アーキテクチャ・内部仕様（As-Is）」を確認したい場合**
   * 現行のパッケージ構造・全クラス仕様・公開APIの唯一の正（Single Source of Truth）は **[`manuals/07_ARCHITECTURE_REFERENCE.md`](../manuals/07_ARCHITECTURE_REFERENCE.md)** です。
   * 機能拡張の手順やテスト規約については **[`manuals/08_CONTRIBUTING_GUIDE.md`](../manuals/08_CONTRIBUTING_GUIDE.md)** を参照してください。
2. **「なぜその仕様・安全設計にしたのか（Why / 設計意図）」を確認したい場合**
   * 下表の **`docs/design/`** 各設計書を参照してください。
   * ステータスが **`一部更新`** または **`置換済み`** となっている設計書には、作成当時の旧クラス構成（旧 `AIChatAgent` や単一ファイルの旧 `ui.py` 等）が記載されています。設計の背景・意図の理解に活用し、現行のクラス配置については必ず後続の設計書（`#012`, `#013` 等）および [`manuals/07_ARCHITECTURE_REFERENCE.md`](../manuals/07_ARCHITECTURE_REFERENCE.md) を参照してください。
3. **設計書を新規作成・更新する場合**
   * **[`docs/DOCUMENTATION_RULE.md`](./DOCUMENTATION_RULE.md)** のテンプレートと連番ルールに従い、作成後は本インデックス（`docs/README.md`）にも行を追加してください。

---

## 🎯 レイヤー別クイック逆引き（現行コード直結の主要設計書）

* **Core層（通信・モデルカタログ・コンテキスト・`ChatAgent` / `SearchAgent`・Skills・Tools）**: 👉 **[`#013`](./design/013_core_package_refactoring.md)**, **[`#015`](./design/015_dynamic_model_catalog_and_thinking.md)**, **[`#016`](./design/016_agent_delegated_web_search.md)**（背景経緯: `#001`, `#002`, `#003`, `#011`）
* **Project層（ファイル探索・3段階承認・Dockerサンドボックス・`ask_user`）**: 👉 **[`#005`](./design/005_sandbox_and_developer_mode.md)**, **[`#007`](./design/007_ask_user_tool.md)**, **[`#008`](./design/008_project_exploration_tools.md)**, **[`#009`](./design/009_edit_mode.md)**
* **App / TUI / CLI層（`GemioController`・Textual `GemioApp`・モーダル・操作体系）**: 👉 **[`#014`](./design/014_textual_tui_architecture.md)**, **[`#015`](./design/015_dynamic_model_catalog_and_thinking.md)**, **[`#016`](./design/016_agent_delegated_web_search.md)**（背景経緯: `#004`, `#006`, `#011`, `#012`）

---

## 📐 1. 機能・アーキテクチャ設計書一覧（`docs/design/`）

作成順の3桁連番（ADR形式）で管理しています。

| 連番 | 設計書ファイル | 作成日 | 対象レイヤー | ステータス | 内容の要約 |
| :---: | :--- | :---: | :---: | :--- | :--- |
| **#001** | **[001_web_search.md](./design/001_web_search.md)**<br>Web検索機能（Google Search Grounding） | 2026-10-29 | Core / CLI | 🔄 一部更新<br>(`#002`, `#013`, `#016` 参照) | `/search` 明示オプトイン方式、Function Callingとのリクエスト分離、通常履歴の機密隔離、連続深掘り検索、出典表示の基本設計。 |
| **#002** | **[002_search_material_integration.md](./design/002_search_material_integration.md)**<br>Web検索結果の「作業前提資料」分離・統合 | 2026-10-30 | Core / CLI | 🔄 一部更新<br>(`#013`, `#016` 参照) | 検索結果を会話履歴に混ぜず独立保持してプロンプト先頭へインジェクションする設計、および長大なリダイレクトURL除外によるトークン削減。 |
| **#003** | **[003_server_error_recovery.md](./design/003_server_error_recovery.md)**<br>サーバーエラー回復と対話整合性維持 | 2026-10-31 | Core / CLI | 🔄 一部更新<br>(`#013` 参照) | HTTP 503/429等の指数バックオフ自動リトライ、中断時の実行済みツール結果保持＋モデル中断発言補完によるロール整合性保護。 |
| **#004** | **[004_welcome_and_help.md](./design/004_welcome_and_help.md)**<br>ウェルカム表示 ＆ ヘルプ機能 | 2026-10-31 | CLI / UI | 🔄 一部更新<br>(`#012` 参照) | 起動時に1回表示するウェルカムバナーと、対話中に随時呼び出せる `/help` コマンドリファレンスパネルの役割分離設計。 |
| **#005** | **[005_sandbox_and_developer_mode.md](./design/005_sandbox_and_developer_mode.md)**<br>サンドボックス・トラストモード・開発者モード | 2026-11-02 | Project / CLI | 🔄 一部更新<br>(`#009`, `#012`, `#013` 参照) | 基本モード（`Project`）と開発者モード（`DeveloperProject`）の2層設計、3段階コンテナセキュリティ境界、値非保持の機密キー検査、サーキットブレーカー。 |
| **#006** | **[006_prompt_header_status.md](./design/006_prompt_header_status.md)**<br>プロンプト入力欄ヘッダーステータス表示 | 2026-11-05 | CLI / UI | 🔄 一部更新<br>(`#011`, `#012` 参照) | プロンプト入力欄上部での対象パス（`project:`）・動作モード（`mode:`）・承認モード（`approval:`）の常時色分け表示とHTMLエスケープ。 |
| **#007** | **[007_ask_user_tool.md](./design/007_ask_user_tool.md)**<br>ユーザー対話・確認ツール（`ask_user`） | 2026-11-06 | Project / Core / UI | 🔄 一部更新<br>(`#012`, `#013` 参照) | 仕様の曖昧さや環境不備時にAIから質問・選択肢を提示する双方向確認ツール、および承認拒否後のサーキットブレーカー保留解除連携。 |
| **#008** | **[008_project_exploration_tools.md](./design/008_project_exploration_tools.md)**<br>プロジェクト構造・ファイル探索ツール | 2026-11-06 | Project | ✅ **実装完了（現行）** | `get_structure` の幅優先・深さ制限、`find_files` の新設、`search_text` の範囲指定、および上限超過時の未表示フォルダ内訳サマリ機構。 |
| **#009** | **[009_edit_mode.md](./design/009_edit_mode.md)**<br>エディットモード（Edit Mode） | 2026-11-06 | Project / CLI | ✅ **実装完了（現行）** | 作成・編集は自動承認し、削除（`delete_path`）とコマンド実行のみ都度確認を残す中間承認モード（`--edit` / `/edit`）と拒否後遮断制御。 |
| **#010** | **[010_cli_architecture_refactoring.md](./design/010_cli_architecture_refactoring.md)**<br>CLIモジュール構造整理（UI分離 & `GemioCLI`化） | 2026-11-07 | CLI / UI | 📦 置換済み<br>(Superseded by `#012`, `#013`) | 肥大化した `main.py` からUI依存を分離し `GemioCLI` クラスを導入した第1段階リファクタリング記録（※構造は `#012` で4層パッケージへ発展置換）。 |
| **#011** | **[011_token_usage_and_session_reset.md](./design/011_token_usage_and_session_reset.md)**<br>累積トークン量確認 & セッションリセット | 2026-11-07 | Core / CLI / UI | 🔄 一部更新<br>(`#012`, `#013` 参照) | 現在のコンテキスト量と累積トークン消費量の2軸トラッキング、3段階健全性診断、`/tokens` 詳細パネル、および `/clear` 即時セッションリセット。 |
| **#012** | **[012_ui_package_refactoring_for_textual.md](./design/012_ui_package_refactoring_for_textual.md)**<br>Textual移行を見据えたUI 4層パッケージ刷新 | 2026-11-12 | CLI / UI | 📦 置換済み<br>(Superseded by `#014`) | Textual化に先立ち旧 `src/gemio/cli/ui/` を分離・DI化した中間リファクタリング記録（※ `#014` でTextualネイティブTUIへ完全置換）。 |
| **#013** | **[013_core_package_refactoring.md](./design/013_core_package_refactoring.md)**<br>Coreパッケージ構造全面リファクタリング | 2026-11-12 | Core / Project | ✅ **実装完了（現行）** | `GenAIClient`・`ChatAgent`・`SearchAgent` 分離、ターン履歴・SHA-256管理 `MaterialStore`・要約圧縮（`context/`）、Skills段階的開示、動的ツール基盤。 |
| **#014** | **[014_textual_tui_architecture.md](./design/014_textual_tui_architecture.md)**<br>Textual TUI一本化・4層パッケージ再編・画面/操作設計 | 2026-11-17 | App / TUI / CLI | ✅ **実装完了（現行）** | `prompt_toolkit` 廃止とTextual完全一本化、`app`/`tui`/`cli` 再編、最小 `80×24` 対応シングルカラムUI、ユーザー生テキスト表示＋AI Markdown描画、モーダル・ターン中断設計。 |
| **#015** | **[015_dynamic_model_catalog_and_thinking.md](./design/015_dynamic_model_catalog_and_thinking.md)**<br>動的モデルカタログ・スペック取得と Thinking 統合 | 2026-11-21 | Core / App / TUI | ✅ **実装完了（現行）** | `models.list` / `models.get` による動的モデル一覧・スペックキャッシュ基盤（`ModelInfo`）、Thinking 対応モデルの自動判定とリアルタイム思考ログ表示、`ModelSelectModal` の動的化。 |
| **#016** | **[016_agent_delegated_web_search.md](./design/016_agent_delegated_web_search.md)**<br>作業エージェント連携型Web検索と検索スイッチ | 2026-11-23 | Core / App / TUI | ✅ **実装完了（現行）** | `ChatAgent` からステートレス `SearchAgent` を呼ぶ `web_search` ツール統合、全モード共通の全文常時確認、`+WEB`/`-WEB` バッジ（デフォルトON）、`/search` 単発検索＋任意資料追加。 |

---

## 🔬 2. 技術調査資料・セキュリティリファレンス一覧（`docs/tech_docs/`）

外部SDK仕様、サンドボックス設計の根拠となる技術調査、およびセキュリティ境界の整理資料です。

| ファイル | ステータス | 内容の要約 |
| :--- | :---: | :--- |
| **[google_genai_models_reference.md](./tech_docs/google_genai_models_reference.md)**<br>🤖 Google GenAI SDK モデル情報取得・機能判定リファレンス | 技術資料 | `google-genai` SDK の `models.list()` / `models.get()` の仕様・取得フィールド整理、テキスト生成モデル抽出および Thinking 対応判定のサンプルコード集。 |
| **[sandbox_security_responsibility_matrix.md](./tech_docs/sandbox_security_responsibility_matrix.md)**<br>🛡️ サンドボックスのセキュリティ境界と責任分界点 | 技術資料（運用中） | Gemio本体（Python側ガードレール）で保証される安全性と、Dockerコンテナ設定（OS・ランタイム層）で確保すべき安全性の責任分界点マトリクス。 |
| **[agent_sandbox_and_container_architecture.md](./tech_docs/agent_sandbox_and_container_architecture.md)**<br>🏗️ AIエージェントの設計思想とサンドボックス構造 | 技術資料 | AIコーディングエージェントの基本設計思想、OSネイティブ隔離とDocker隔離の比較、I/O最適化、脅威モデルと堅牢化ベストプラクティス。 |
| **[dev_containers_reference.md](./tech_docs/dev_containers_reference.md)**<br>📦 Dev Containers 技術資料＆AI実践ガイド | 技術資料 | Development Containers 仕様の内部メカニズムと、自律型AIエージェントの実行基盤として活用する際の構築・運用ガイド。 |
| **[docker_reference.md](./tech_docs/docker_reference.md)**<br>🐳 Docker 技術資料・運用リファレンス | 運用中 | Dockerの基本アーキテクチャ、主要オプション、`Dockerfile` ベストプラクティス、および `pytest` 実行用堅牢化サンドボックスの構築手順。 |

---

## 📋 3. 運用ルール・開発履歴サマリー

* **[`DOCUMENTATION_RULE.md`](./DOCUMENTATION_RULE.md)**: 設計書・技術ドキュメント運用ルール（3桁連番規約、ステータス定義、必須ヘッダーテンプレート）
* **[`PROJECT_SUMMARY.md`](./PROJECT_SUMMARY.md)**: プロジェクト改修作業の要約・時系列開発履歴ログ

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-17 | AI Agent | 運用中 | 初版作成。`docs/design/` の3桁連番（`001`〜`013`）およびステータス管理の導入に伴い、`docs/` 全体の目次インデックスとレイヤー別逆引きガイドを整備。 |
| 2026-11-17 | AI Agent | 運用中 | `#014`（`014_textual_tui_architecture.md`）の設計作成および実装完了に伴い、`#014` を `実装完了（現行）`、`#012` を `置換済み（Superseded by #014）` に更新。 |
| 2026-11-21 | AI Agent | 運用中 | `docs/tech_docs/google_genai_models_reference.md` の追加および `#015`（`015_dynamic_model_catalog_and_thinking.md`）の作成・実装完了に伴い一覧表を更新。 |
| 2026-11-23 | AI Agent | 運用中 | `#016`（`016_agent_delegated_web_search.md`）の作成および実装完了に伴い、`#016` を `実装完了（現行）` に更新し、`#001`・`#002` の参照情報を同期。 |
