# 🏗️ Gemio アーキテクチャ & 内部仕様リファレンスマニュアル

**作成日:** 2026年11月14日  
**最終更新日:** 2026年11月14日  
**ステータス:** 運用中  
**対象システム:** Gemio 全パッケージ (`src/gemio/core/`, `src/gemio/project/`, `src/gemio/cli/`, `src/gemio/__init__.py`)  

### 【要約】
本マニュアルは、Gemioリポジトリのコントリビューターおよびライブラリ組み込み開発者向けに、Gemioの全体アーキテクチャ、3層レイヤー依存ルール、全モジュールの責務マップ、各サブパッケージ（`core` / `project` / `cli`・`cli.ui`）の内部仕様と状態遷移、主要処理シーケンス、およびPythonライブラリとしての公開APIを体系的に解説する技術リファレンスです。
（※機能追加手順・テスト作成・ドキュメント規約については対になる **[`08_CONTRIBUTING_GUIDE.md`](./08_CONTRIBUTING_GUIDE.md)** を参照してください。）

---

## 目次
1. [アーキテクチャ全体像と基本設計原則](#1-アーキテクチャ全体像と基本設計原則)
   - [1.1 3層レイヤー構造と単方向依存の原則](#11-3層レイヤー構造と単方向依存の原則)
   - [1.2 全体コンポーネント連携図](#12-全体コンポーネント連携図)
   - [1.3 セキュリティ＆プライバシーの4大設計原則](#13-セキュリティプライバシーの4大設計原則)
2. [リポジトリ・全モジュール完全マップ](#2-リポジトリ全モジュール完全マップ)
   - [2.1 ディレクトリツリー](#21-ディレクトリツリー)
   - [2.2 パッケージ・モジュール責務一覧表](#22-パッケージモジュール責務一覧表)
3. [`gemio.core` パッケージ詳細仕様](#3-gemiocore-パッケージ詳細仕様)
   - [3.1 基盤・通信・トークン計測 (`exceptions.py`, `models.py`, `client.py`)](#31-基盤通信トークン計測-exceptionspy-modelspy-clientpy)
   - [3.2 コンテキスト・セッション管理 (`core/context/`)](#32-コンテキストセッション管理-corecontext)
   - [3.3 実行エンジン (`agent.py` vs `search.py` の完全分離)](#33-実行エンジン-agentpy-vs-searchpy-の完全分離)
   - [3.4 プロンプト合成・Skills・ツール管理 (`prompts.py`, `skills/`, `tools/`)](#34-プロンプト合成skillsツール管理-promptspy-skills-tools)
4. [`gemio.project` パッケージ詳細仕様](#4-gemioproject-パッケージ詳細仕様)
   - [4.1 `Project`（ファイル境界保護・BFS探索・3段階承認・サーキットブレーカー）](#41-projectファイル境界保護bfs探索3段階承認サーキットブレーカー)
   - [4.2 `DeveloperProject`（サンドボックス2方式・3段階セキュリティ境界・診断プローブ）](#42-developerprojectサンドボックス2方式3段階セキュリティ境界診断プローブ)
5. [`gemio.cli` & `gemio.cli.ui` パッケージ詳細仕様](#5-gemiocli--gemiocliui-パッケージ詳細仕様)
   - [5.1 `GemioCLI`（セッション制御・DI・コマンドディスパッチ）](#51-gemiocliセッション制御diコマンドディスパッチ)
   - [5.2 4層UIアーキテクチャ (`cli/ui/`) と将来の Textual TUI 移行設計](#52-4層uiアーキテクチャ-cliui-と将来の-textual-tui-移行設計)
6. [主要処理のシーケンス・データフロー](#6-主要処理のシーケンスデータフロー)
   - [6.1 起動・初期化・サンドボックス検証フロー](#61-起動初期化サンドボックス検証フロー)
   - [6.2 通常対話・Function Calling・承認/Abort・`ChatTurn` 確定フロー](#62-通常対話function-calling承認abortchatturn-確定フロー)
   - [6.3 `/search` Web検索〜`MaterialStore` 蓄積〜プロンプト先頭インジェクションフロー](#63-search-web検索materialstore-蓄積プロンプト先頭インジェクションフロー)
7. [Pythonライブラリとしての公開APIリファレンス](#7-pythonライブラリとしての公開apiリファレンス)
   - [7.1 `gemio` トップレベル公開シンボル一覧](#71-gemio-トップレベル公開シンボル一覧)
   - [7.2 ライブラリ組み込み最小サンプルコード](#72-ライブラリ組み込み最小サンプルコード)
8. [更新履歴](#8-更新履歴)

---

## 1. アーキテクチャ全体像と基本設計原則

### 1.1 3層レイヤー構造と単方向依存の原則

Gemioは標準の `src` レイアウト（`src/gemio/`）を採用し、責務に応じて明確に分離された3つのトップレベルサブパッケージで構成されています。パッケージ間の依存は **`cli` ➔ `project` ➔ `core`** の厳格な単方向に制限されています。

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 1. アプリケーション・UI層 (gemio.cli / gemio.cli.ui)                 │
│    ・CLI引数解析、REPL制御、スラッシュコマンド処理 (GemioCLI)        │
│    ・抽象UI契約 (GemioUI) と Rich/prompt_toolkit 実装 (RichConsoleUI)│
└──────────────────┬───────────────────────────────────┬───────────────┘
                   │ 依存                              │ 依存
                   ▼                                   │
┌─────────────────────────────────────────┐            │
│ 2. プロジェクト・サンドボックス層       │            │
│    (gemio.project)                      │            │
│    ・パス境界/機密保護、3段階承認制御   │            │
│    ・サーキットブレーカー (Project)     │            │
│    ・Docker/DevContainer検証とコマンド  │            │
│      委譲実行 (DeveloperProject)        │            │
└──────────────────┬──────────────────────┘            │
                   │ 依存 (exceptions, tools.Toolset)  │
                   ▼                                   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. コアエンジン・コンテキスト層 (gemio.core)                         │
│    ・API通信/リトライ/トークン集計 (GenAIClient, TokenUsageTracker)  │
│    ・ターン履歴/作業前提資料/要約圧縮 (context/)                     │
│    ・通常対話エンジン (ChatAgent) / Web検索エンジン (SearchAgent)    │
│    ・動的プロンプト合成、Skills、ツールレジストリ (prompts, skills,  │
│      tools)                                                          │
└──────────────────────────────────────────────────────────────────────┘
```

#### 厳守すべき依存分離ルール
1. **`core` および `project` のUI完全非依存**:
   `gemio.core` および `gemio.project` は、`rich` や `prompt_toolkit` 等のUIライブラリを一切インポートしません。承認確認・ユーザー質問・上限継続確認・ログ出力はすべて**コールバック関数注入（DI）**によって疎結合に連携します。
2. **`cli/main.py` のUIライブラリ直接インポート禁止**:
   `src/gemio/cli/main.py` は抽象クラス `GemioUI` のメソッドのみを呼び出し、`rich` / `prompt_toolkit` を直接インポートしません（単体テスト `test_cli_main.py` でAST静的に検証されています）。
3. **軽量定数・例外モジュールのSDK非依存**:
   `core/exceptions.py` および `core/models.py` は外部SDK（`google-genai`）をインポートしません。これにより、`project` 層や `cli/ui` 層が例外やモデル定数を参照しても重いSDK依存が伝播しません。

---

### 1.2 全体コンポーネント連携図

```text
                       ┌──────────────────────────────────────────────────┐
                       │                 GemioCLI (main.py)               │
                       │  └── self.ui: GemioUI (RichConsoleUI / Textual*) │
                       └──┬────────────────────┬───────────────────────┬──┘
       [コールバック注入] │                    │通常プロンプト         │Web検索 (/search)
  ・approval_callback     │                    │                       │
  ・ask_user_callback     │                    │                       │
                          ▼                    ▼                       ▼
┌───────────────────────────────────┐   ┌─────────────┐         ┌─────────────┐
│  Project / DeveloperProject       │   │  ChatAgent  │         │ SearchAgent │
│  ・ファイル操作ツール群           │   └──────┬──────┘         └──────┬──────┘
│  ・run_sandbox_command (--dev)    │          │                       │SearchResult返却
│  ・3段階承認 & サーキットブレーカ │          │参照・記録             │(成功時のみCLIが登録)
└─────────────────┬─────────────────┘          ▼                       │
                  │Toolset登録      ┌────────────────────────┐         │
                  │                 │      ChatSession       │◄────────┘
                  │                 │  ├── ChatHistory       │  (※SearchAgent自体は
                  │                 │  │   (ChatTurn列+要約) │   ChatSessionへの参照を
                  │                 │  └── MaterialStore     │   一切持たない完全隔離構造)
                  │                 │      (SHA-256管理)     │
                  ▼                 └──────────▲─────────────┘
         ┌────────────────────────┐            │要約圧縮 (基盤API)
         │ ・ToolRegistry         │   ┌────────┴─────────┐
         │   └─ ToolProvider      │   │ ContextCompactor │
         │      (Static/Dynamic)  │   └────────┬─────────┘
         │ ・SkillManager         │            │
         │ ・SystemPromptComposer │            │
         └────────┬───────────────┘            │
                  │毎ループ動的評価            │
                  ▼                            ▼
         ┌────────────────────────────────────────────────────────────────┐
         │       GenAIClient (API通信・指数バックオフリトライ)            │
         │         └── TokenUsageTracker (chat / search / compaction 集計)│
         └────────────────────────────────────────────────────────────────┘
```

---

### 1.3 セキュリティ＆プライバシーの4大設計原則

1. **ホストOSシェル直接実行の100%禁止**:
   基本モード（`Project`）はサブプロセス実行機能を一切持たず、開発者モード（`DeveloperProject`）も安全なLinuxコンテナ内（`in_container`）または稼働中Dockerコンテナへの `docker exec` 委譲（`host_docker`）が確認できない限り `SandboxSecurityError` で起動を拒否します。
2. **ローカル環境情報（物理パス・OS例外・暗号化URL）のプロンプト非露出**:
   `ReferenceMaterial`、`SkillSpec`、`ToolSpec` はホストOSの物理ファイルパス属性を一切保持しません。エラーメッセージからも物理パスを除去し、Web検索の参照元からも長大な暗号化リダイレクトURL（`vertexaisearch...`）を除外してトークン浪費と環境露出を防止します。
3. **Web検索コンテキストの完全隔離**:
   `SearchAgent` はローカルの対話履歴（`ChatHistory`）、作業前提資料（`MaterialStore`）、およびファイル操作ツールへの参照を一切持たず、作業中のソースコードや機密情報が検索クエリ経由で外部送信される経路を構造的に遮断しています。
4. **AI操作権限と人間専用操作権限の厳格な分離**:
   AIツールから `MaterialStore` を直接書き換える手段は提供せず、Web検索結果以外の資料追加・削除はユーザー操作専用としています。また、`.env` 等の機密領域、`.git/`、既存 `.devcontainer/`、`.gemio/`、`*.egg-info/` は承認モードを問わずAIからの変更をハードブロックします。

---

## 2. リポジトリ・全モジュール完全マップ

### 2.1 ディレクトリツリー

```text
gemio/
├── pyproject.toml                       # パッケージ定義・依存関係・CLIエントリポイント (gemio)
├── requirements.txt                     # 開発・テスト用依存パッケージ一覧
├── Dockerfile / compose.yaml / .dockerignore  # 標準サンドボックスコンテナ構成
├── README.md                            # プロジェクト概要・クイックスタート
├── manuals/                             # エンドユーザー & コントリビューター向けマニュアル群 (01〜09)
├── docs/                                # 開発履歴 (PROJECT_SUMMARY.md)、運用規約、個別機能設計書 (design/)
├── src/gemio/                           # メインパッケージルート
│   ├── __init__.py                      # コア・プロジェクト層の公開APIファサード（UI非依存）
│   ├── __main__.py                      # `python -m gemio` 起動エントリポイント
│   ├── core/                            # 【コア層】通信・コンテキスト・エージェント・Skills・Tools
│   │   ├── __init__.py
│   │   ├── exceptions.py                # 共通例外 (AgentExecutionAborted) ※SDK非依存
│   │   ├── models.py                    # モデル定義・エイリアス・閾値・AgentLogCategory ※SDK非依存
│   │   ├── client.py                    # GenAIClient & TokenUsageTracker
│   │   ├── prompts.py                   # システムプロンプト定数 & SystemPromptComposer
│   │   ├── agent.py                     # ChatAgent (通常対話 & Function Calling ループ)
│   │   ├── search.py                    # SearchAgent (Google Search Grounding 専用)
│   │   ├── context/                     # コンテキスト・前提資料管理サブパッケージ
│   │   │   ├── __init__.py
│   │   │   ├── history.py               # ChatTurn & ChatHistory (ターン単位履歴・rewind・抽出)
│   │   │   ├── materials.py             # MaterialKind, ReferenceMaterial, MaterialStore (SHA-256管理)
│   │   │   ├── compactor.py             # ContextCompactor (古いターンの構造化要約圧縮)
│   │   │   └── session.py               # ChatSession (履歴+前提資料の統合・リクエスト先頭注入)
│   │   ├── skills/                      # Skills（段階的開示）サブパッケージ
│   │   │   ├── __init__.py
│   │   │   ├── builtin/                 # パッケージ標準組み込みスキル配置用固定ディレクトリ
│   │   │   ├── loader.py                # SkillSpec, SkillLoader, parse_skill_markdown
│   │   │   └── manager.py               # SkillManager (カタログ生成・activate_skill メタツール)
│   │   └── tools/                       # ツール・動的ツールセット管理サブパッケージ
│   │       ├── __init__.py
│   │       ├── builtin.py               # 標準日時取得ツール (get_current_date, get_current_time)
│   │       ├── registry.py              # ToolSpec, Toolset, ToolRegistry, StaticToolProvider
│   │       ├── dynamic.py               # [Experimental] DynamicToolsetProvider (request_tools)
│   │       └── loader.py                # load_project_toolsets (.gemio/tools/*.py ローダー)
│   ├── project/                         # 【プロジェクト・サンドボックス層】
│   │   ├── __init__.py
│   │   ├── base.py                      # Project (ファイル操作・BFS探索・保護定数・3段階承認・CB)
│   │   └── developer.py                 # DeveloperProject (Docker検証・診断プローブ・コマンド実行)
│   └── cli/                             # 【CLI・UI層】
│       ├── __init__.py
│       ├── main.py                      # parse_args, GemioCLI, main
│       └── ui/                          # 4層分離UIサブパッケージ
│           ├── __init__.py
│           ├── base.py                  # GemioUI 抽象基底クラス (ABC)
│           ├── renderables.py           # Rich Panel/Table/HTML 純粋生成関数群 & classify_agent_log
│           ├── prompts.py               # prompt_toolkit / rich.prompt 対話入力関数群
│           └── rich_ui.py               # RichConsoleUI 具象クラス（スピナー管理カプセル化）
└── tests/                               # 単体テストスイート（全12テストモジュール）
```

---

### 2.2 パッケージ・モジュール責務一覧表

| レイヤー | モジュールパス | 主要クラス / 関数 | 主な責務 |
| :--- | :--- | :--- | :--- |
| **Core** | `core/exceptions.py` | `AgentExecutionAborted` | Abort選択・サーキットブレーカー発動時の作業ループ強制遮断例外 |
| **Core** | `core/models.py` | `MODEL_PRESETS`, `resolve_model_name`, `AgentLogCategory` | モデルプリセット・エイリアス解決、コンテキスト警告閾値、ログ種別定数 |
| **Core** | `core/client.py` | `GenAIClient`, `TokenUsageTracker` | `google-genai` クライアント管理、503/429等の指数バックオフリトライ、ソース別トークン集計 |
| **Core** | `core/prompts.py` | `SystemPromptComposer`, `build_system_instruction` | 基本/開発者ルール・動的ツールカタログ・Skillsカタログ/手順の動的合成 |
| **Core** | `core/agent.py` | `ChatAgent`, `ChatTurnResult`, `emit_agent_log` | 通常対話・Function Callingループ、エラー回復、上限継続確認、ターン記録 |
| **Core** | `core/search.py` | `SearchAgent`, `SearchResult` | 隔離コンテキストでのGoogle Search Grounding実行、出典抽出・重複排除 |
| **Core** | `core/context/history.py` | `ChatTurn`, `ChatHistory` | ターン単位の対話履歴保持、安全な `rewind()`、Markdown抽出、要約展開 |
| **Core** | `core/context/materials.py` | `MaterialStore`, `ReferenceMaterial`, `MaterialKind` | パス非保持・SHA-256ハッシュによる前提資料の蓄積・重複防止・プロンプト整形 |
| **Core** | `core/context/compactor.py` | `ContextCompactor`, `CompactionResult` | 古い `ChatTurn` 群をLLMで4項目構造化サマリへ要約圧縮 |
| **Core** | `core/context/session.py` | `ChatSession` | `ChatHistory` と `MaterialStore` の統合、リクエスト送信時の資料先頭合成 |
| **Core** | `core/skills/loader.py` | `SkillLoader`, `SkillSpec`, `parse_skill_markdown` | 固定2箇所（`builtin/` と `.gemio/skills/`）からのフロントマター解析・本文遅延ロード |
| **Core** | `core/skills/manager.py` | `SkillManager` | スキルカタログ構築、有効化状態管理、`activate_skill` メタツール提供 |
| **Core** | `core/tools/builtin.py` | `get_builtin_toolset`, `get_current_date`, `get_current_time` | 常時有効な標準日時ユーティリティツール |
| **Core** | `core/tools/registry.py` | `ToolRegistry`, `Toolset`, `ToolSpec`, `StaticToolProvider` | ツール定義の一元管理、Docstring先頭概要抽出、安全な名前解決・実行 |
| **Core** | `core/tools/dynamic.py` | `DynamicToolsetProvider` | `[Experimental]` `request_tools` によるツールセット/個別ツールのオンデマンド有効化 |
| **Core** | `core/tools/loader.py` | `load_project_toolsets` | `.gemio/tools/*.py` からの拡張ツール読み込みとパス秘匿サニタイズ |
| **Project** | `project/base.py` | `Project`, `summarize_text_for_display` | パス境界・機密保護、ファイル操作・BFS探索ツール、3段階承認、サーキットブレーカー、`ask_user` |
| **Project** | `project/developer.py` | `DeveloperProject`, `SandboxSecurityError`, `scrub_environment` | コンテナ判定、脱獄構成ブロック、環境変数スクラブ、`/sandbox` 診断、`run_sandbox_command` |
| **CLI** | `cli/main.py` | `GemioCLI`, `parse_args`, `main` | CLI起動処理、コンポーネント結線、スラッシュコマンド振り分け、REPLループ |
| **CLI/UI** | `cli/ui/base.py` | `GemioUI` | コンソールUIと将来のTextual TUIを抽象化するUIバックエンド契約（ABC） |
| **CLI/UI** | `cli/ui/renderables.py` | `build_*_panel`, `format_prompt_header`, `classify_agent_log` | `console.print` を呼ばない純粋な Rich Renderable 生成関数群 |
| **CLI/UI** | `cli/ui/prompts.py` | `create_prompt_session`, `prompt_*` | マルチライン入力（`Esc+Enter` / `Esc+Backspace`）および各種選択・確認プロンプト |
| **CLI/UI** | `cli/ui/rich_ui.py` | `RichConsoleUI` | 現行の標準コンソールUI実装、スピナー管理・対話時自動一時停止（`pause_status`） |

---

## 3. `gemio.core` パッケージ詳細仕様

### 3.1 基盤・通信・トークン計測 (`exceptions.py`, `models.py`, `client.py`)

#### `GenAIClient` (`src/gemio/core/client.py`)
* `google.genai.Client` の生成を1箇所に集約します。`api_key` が明示指定された場合（`in_container` 起動時のマスク入力）は環境変数を介さず `genai.Client(api_key=api_key)` で初期化します。
* **自動リトライ制御 (`generate_content`)**:
  * リトライ対象: HTTP `429`, `500`, `502`, `503`, `504`、`ConnectionError`、`TimeoutError`、`OSError`、およびレート制限・過負荷系エラー文字列（`400`〜`499` のクライアントエラーは即時送出）。
  * バックオフ仕様: デフォルト最大 `3` 回、`2.0秒 ➔ 4.0秒 ➔ 8.0秒` ＋ ランダムジッター（最低待機 `1.0秒`）。待機時は `on_retry_warning` コールバックへ通知します。
* **`TokenUsageTracker` によるソース別集計**:
  * `record(response, source="chat" | "search" | "compaction")` により、呼び出し元ごとのAPIコール回数と累積トークン（`prompt`, `candidates`, `thoughts`, `cached`, `tool_use`, `total`）を加算します。
  * **重要仕様**: 現在の対話コンテキスト規模を表す `last_prompt_tokens`（プロンプトヘッダーや `/tokens` の健全性診断に使用、警告閾値 `50,000` / 危険閾値 `150,000`）は **`source == "chat"` のときのみ更新**されます。これにより、途中で軽量な `/search` や要約圧縮を実行しても対話コンテキスト量の計測値が上書きされません。

---

### 3.2 コンテキスト・セッション管理 (`core/context/`)

> **📌 CLI接続ステータスに関する補足（コントリビューター向け重要情報）**  
> `core/context/` には、現行のCLIスラッシュコマンドからすでに利用されている機能と、今後のUI拡張（Textual TUIや追加コマンド）に向けて**Core層に先行実装・単体テスト完備されている基盤API**の両方が含まれています。

| クラス・メソッド | 機能概要 | 現在のCLI接続状態 |
| :--- | :--- | :--- |
| `ChatHistory.append_turn` / `to_contents` / `clear` | ターン単位の履歴蓄積・API用展開・全消去 | ✅ **接続済**（通常対話・`/clear`） |
| `MaterialStore.add_search_result` / `clear` / `format_for_prompt` | Web検索資料のSHA-256重複排除蓄積・破棄・先頭注入 | ✅ **接続済**（`/search`・`/search --clear`・`/clear --all`） |
| `ChatHistory.rewind(steps)` | Function Calling整合性を壊さないターン単位の巻き戻し | 🔧 **Core実装済（CLI未接続）** |
| `ChatHistory.get_turn(s)` / `format_turns_markdown` | 任意範囲ターンの抽出・クリップボード/再表示用Markdown整形 | 🔧 **Core実装済（CLI未接続）** |
| `MaterialStore.add_user_document` / `remove` / `format_for_display` | ユーザー指定資料の追加（`source_hash` 更新対応）・個別削除・一覧表示 | 🔧 **Core実装済（CLI未接続）** |
| `ContextCompactor.compact` / `ChatSession.compact` | 古い対話ターン群をLLMで4項目構造化サマリへ圧縮置換 | 🔧 **Core実装済（CLI未接続）** |
| `ChatSession.export_context_markdown` | 前提資料＋要約＋選択ターンの一括Markdownエクスポート | 🔧 **Core実装済（CLI未接続）** |

#### 履歴と前提資料のデータ構造のポイント
* **`ChatTurn` による境界保護 (`history.py`)**:
  1回のユーザー指示から、途中の複数回の `function_call` / `function_response` 往復、および最終の `model` 応答までを1つの `ChatTurn` に格納します。`summary_text`（圧縮要約）が存在する場合は、`to_contents()` 呼び出し時に先頭へ `role="user"`（要約提示）と `role="model"`（把握応答）の2要素ペアを自動展開し、Gemini APIのロール交互規約を厳守します。
* **履歴本体を汚さない前提資料インジェクション (`session.py`)**:
  `ChatSession.build_request_contents(pending_contents)` は、API送信用のコピーリスト上で末尾から直近のテキスト形式 `user` メッセージを探し、その先頭に `【作業前提資料】...` を動的に合成します。`ChatHistory` 内の `ChatTurn` 本体には生のユーザー指示のみが保存されるため、毎ターン資料テキストが履歴に重複蓄積して肥大化することがありません。

---

### 3.3 実行エンジン (`agent.py` vs `search.py` の完全分離)

#### `ChatAgent` (`src/gemio/core/agent.py`)
* `automatic_function_calling` を明示的に無効化（`disable=True`）し、自前の `_execute_agent_loop` でツール呼び出しを制御します。
* **毎イテレーション動的評価**:
  ループの各ステップ先頭で `self.prompt_composer.compose()` と `self.tool_provider.get_active_tools()` を呼び出して `config` を再構築します。これにより、ループ途中でAIが `activate_skill` や `request_tools` を実行すると、**次のAPIコール（直後のイテレーション）から即座に新しい手順やツールスキーマが有効化**されます。
* **ロール整合性の徹底保護（400 Bad Request 防止）**:
  1. **サーバーエラー中断時**: ツール実行後にAPIリトライ上限で失敗した場合、実行済みの `function_response` は破棄せず保持したまま、末尾に中断案内の `role="model"` テキスト応答を補完して `ChatTurn(aborted=True)` として確定保存します。
  2. **`AgentExecutionAborted` 発生時（Abort選択 / サーキットブレーカー発動）**: モデルが1ターンに複数の `function_call`（例: `[call_1, call_2, call_3]`）を返し、`call_1` で Abort が発生した場合、`call_1` に `[ABORTED]` 応答を付与するだけでなく、**未実行の後続 `call_2`, `call_3` に対してもスキップ通知の `function_response` を自動付与**してからループを抜けます（Gemini APIの「`function_call` の数と `function_response` の数が一致しなければならない」制約を守るため）。
  3. **連続実行上限（`max_iterations`、CLIでは20回）到達時**: `on_iteration_limit` でユーザーが「中断」を選んだ場合は、途中の未完ツール往復を破棄して `[user_content]` のみにロールバックし、中断応答の `model` メッセージを添えてクリーンにターンを閉じます。

#### `SearchAgent` (`src/gemio/core/search.py`)
* `tools=[{"google_search": {}}]` 専用の設定で動作します（Gemini API仕様上、Google Search GroundingとカスタムFunction Callingの同時指定による競合もこの分離により完全回避されます）。
* 連続 `/search` による深掘り検索のために独立した `self.search_history` を持ちますが、通常プロンプトが送信された際（`GemioCLI.run_chat_prompt`）や `-n`/`--new` 指定時、`/clear` 実行時にリセットされます。
* APIエラー発生時は追加した `user` クエリを `pop()` でロールバックし、`SearchResult(succeeded=False)` を返すことで、エラーメッセージが `MaterialStore` に登録されるのを防ぎます。

---

### 3.4 プロンプト合成・Skills・ツール管理 (`prompts.py`, `skills/`, `tools/`)

* **`SystemPromptComposer` の合成順序**:
  1. `base_instruction`（`DEFAULT_SYSTEM_INSTRUCTION`）
  2. `DEVELOPER_MODE_INSTRUCTION_ADDON`（`dev_mode=True` の場合のみ）
  3. `tool_provider.get_catalog_prompt()`（`DynamicToolsetProvider` 使用時のみ動的ツールカタログを出力）
  4. `skill_manager.build_catalog_prompt()`（登録スキルが1件以上ある場合のみ一覧を出力）
  5. `skill_manager.build_active_skills_prompt()`（有効化中のスキルの Markdown 本文を出力）
  6. `extra_sections`（任意の固定文字列または `Callable[[], str]`）
* **`SkillLoader` & `SkillManager` (`core/skills/`)**:
  * 走査対象は `src/gemio/core/skills/builtin/*/SKILL.md` と `<project_root>/.gemio/skills/*/SKILL.md` の固定2箇所（深さ1階層のみ、上限256KB、シンボリックリンク不可）。同名スキルはプロジェクト側で上書きされます。
  * 外部YAMLライブラリに依存しない軽量パーサ `parse_skill_markdown()` でフロントマターを解析し、本文はクロージャ `_body_loader` 内に閉じ込めて `activate()` 時まで遅延ロードします。
* **`ToolRegistry` & `ToolProvider` (`core/tools/`)**:
  * `StaticToolProvider`（デフォルト）は全登録ツール＋`_extra_tools`（`activate_skill`）を常時公開します。
  * `DynamicToolsetProvider`（`--dynamic-tools` 指定時）は、`always_active=True` のツールセット＋`request_tools`＋`_extra_tools` のみを初期公開し、AIからの要求やスキルの `recommended_toolsets` 連動によってアクティブ集合を動的に拡張します。
  * 未知または未有効のツール名をAIが呼び出した場合でも例外で落とさず、`[ERROR] ... request_tools(...) を呼び出して有効化してください` という自己修復用の案内文字列を返します。

---

## 4. `gemio.project` パッケージ詳細仕様

### 4.1 `Project`（ファイル境界保護・BFS探索・3段階承認・サーキットブレーカー）

#### パス検証とハードブロック境界 (`_verify_and_get_path`, `_check_fatal_security_path`)
すべてのファイル・探索系ツールは実行前に必ず `_verify_and_get_path(relative_path, is_write)` を通過します。
1. シンボリックリンク解決前の名称で `_is_secret_file` を検査（たとえば `.env -> /dev/null` にマスクされている場合でもAIツール経由でのアクセスを拒否）。
2. `Path.resolve()` 後、`is_relative_to(self.project_path)` でプロジェクト外へのパストラバーサルおよび外部指しシンボリックリンクを拒否。
3. `_check_fatal_security_path` による常時ブロック（承認モード不問）:
   * 読み書きとも禁止: `SECRET_FILE_PATTERNS`（`ALLOWED_ENV_FILES` を除く）、`SECRET_DIR_NAMES`、`.git/` 配下。
   * 書き込み・削除禁止（`is_write=True`）: 既存の `.devcontainer/`、`*.egg-info/`、`.gemio/`。
4. ディレクトリごとの削除・移動（`delete_path`, `rename_path`）時は、`_verify_directory_tree_writable()` で配下ツリーを再帰走査し、保護対象ファイルが1つでも含まれていれば操作全体を拒否します。

#### 見落とし防止付きBFS探索ツール (`get_structure`, `find_files`, `search_text`)
* 深さ優先探索（DFS）で先頭の巨大サブフォルダだけで上限件数を使い切ってしまう問題を防ぐため、**幅優先探索（BFS）**で浅い階層から順に走査します。
* 上限（`get_structure`: 深さ3・200件、`find_files`: 200件、`search_text`: 100件）を超過した場合は、単に切り捨てるのではなく**「どのサブフォルダに未表示項目が何件残っているか」の内訳サマリ（`※配下に未表示 N 件` / `[省略通知]`）**を結果末尾に付与し、システムプロンプトの規約と連携してAIに `target_dir` 指定での段階的掘り下げを促します。

#### 3段階承認モードとサーキットブレーカーの状態遷移
* **承認判定 (`_confirm`)**:
  * `normal`: 全変更ツールで確認プロンプトを表示。
  * `edit`: 通常変更は自動承認、`force_confirm_in_edit=True` が指定された `delete_path` と `run_sandbox_command` のみ確認プロンプトを表示。
  * `trust`: 全変更ツールを自動承認。
* **サーキットブレーカー (`_rejection_pending`) の状態遷移**:
  ```text
  [初期状態: _rejection_pending = False]
       │
       ├── 承認プロンプトでユーザーが 'n' (拒否) を選択
       ▼
  [保留状態: _rejection_pending = True]
       │
       ├── パターン1: AIが説明・相談せず次の変更系ツールやコマンド実行を呼び出す
       │     ➔ `_check_circuit_breaker()` が自動承認判定より前に発火！
       │     ➔ `_rejection_pending = False` に戻し `AgentExecutionAborted` を送出して強制遮断
       │
       ├── パターン2: AIが `ask_user` ツールを呼び出してユーザーに相談する
       │     ➔ `_rejection_pending = False` に解除され、合意に基づく後続の変更操作が可能になる
       │
       └── パターン3: AIがテキスト応答を返してターンが終了する（`on_prompt_end`）または `/clear`
             ➔ `_rejection_pending = False` にリセットされる
  ```

---

### 4.2 `DeveloperProject`（サンドボックス2方式・3段階セキュリティ境界・診断プローブ）

#### 実行モードの厳格判定 (`_verify_and_setup_sandbox`)
1. **`in_container` モード**:
   `container_name` 未指定 かつ `sys.platform == "linux"` かつ実体ファイル（`/.dockerenv` または `/run/.containerenv`）が存在する場合のみ成立します（環境変数 `DEVCONTAINER` 等はホストOS上で偽装・誤設定され得るため使用しません）。初期化時に `prctl(PR_SET_DUMPABLE, 0)` を呼び出して子プロセスからの親メモリ読み取りを禁止します。
2. **`host_docker` モード**:
   上記以外の場合はすべて `docker` CLI 経由の委譲モードになります。`--container` 未指定時は稼働中コンテナの `devcontainer.local_folder` ラベルまたはバインドマウント `Source` パスの一致から対象コンテナを自動探索します。

#### コンテナセキュリティの3段階境界
| レベル | 判定条件 | 挙動 |
| :--- | :--- | :--- |
| **🚨 起動拒否 (`SandboxSecurityError`)** | コンテナ未稼働、`--privileged`（または `CapEff` 全ビット有効）、`/var/run/docker.sock` マウント、`--network host`、ホストルート（`/`, `C:\`）マウント | コンテナ脱獄・ホスト乗っ取りが可能なため起動を即時ブロック |
| **🔴 危険警告 (`danger`)** | コンテナ環境変数（`os.environ` / `/proc/1/environ` / `Config.Env`）への機密キー設定、未マスクの機密ファイル・機密ディレクトリ露出 | 起動は許可し、`/sandbox` 診断パネルで赤枠・強い警告と対策コマンドを表示 |
| **🟡 注意案内 (`warning`)** | コンテナの `root` (UID 0) 実行、`.git/`・`.devcontainer/`・`.gemio/`・`.venv/`・`node_modules/` 等のホストとの `rw` 共有 | `/sandbox` 診断パネルで黄色警告と `:ro` / 匿名ボリューム隔離の推奨設定を表示 |

#### 値を保持しない機密環境変数検査と子プロセス管理
* `_sanitize_inspect_info()` は `docker inspect` 直後に `Config.Env` の値を `<redacted>` に置換し、`_probe_container_via_exec()` も `awk` で非空環境変数の**変数名（キー名）のみ**を取り出すため、Gemioのメモリ上にコンテナ側のシークレット平文値が保持されることはありません。
* `run_sandbox_command` 実行時は `scrub_environment()` でホスト環境変数をスクラブした上で、POSIX環境では `start_new_session=True` でプロセスグループを分離して起動し、タイムアウト・Kill・`Ctrl+C` 時には `os.killpg(..., SIGKILL)` で孫プロセスまで確実に終了させます。

---

## 5. `gemio.cli` & `gemio.cli.ui` パッケージ詳細仕様

### 5.1 `GemioCLI`（セッション制御・DI・コマンドディスパッチ）
* `GemioCLI(args, ui_backend: GemioUI | None = None)` は、UIバックエンドをコンストラクタ注入（省略時は `RichConsoleUI()`）で受け取ります。
* 単一の `GenAIClient` インスタンス（`self.ai_client`）を `ChatAgent` と `SearchAgent` で共有しているため、`/model` コマンドで `self.ai_client.set_model(...)` を呼ぶだけで通常対話・Web検索・要約圧縮すべての使用モデルが一括で切り替わります。
* **Ctrl+C (`KeyboardInterrupt`) の2段階制御 (`run`)**:
  * `prompt_user_input`（入力待ち中）や `/model`・`/trust` の選択プロンプト中の `Ctrl+C` は、入力をキャンセルしてセッションを継続します。
  * `dispatch_input`（AI思考・ツール実行・Web検索中）の `Ctrl+C` は、`self.project.terminate_active_process()` で実行中サブプロセスを即座にキルし、中断メッセージを出してプログラムを終了します。

---

### 5.2 4層UIアーキテクチャ (`cli/ui/`) と将来の Textual TUI 移行設計

`src/gemio/cli/ui/` は、現行のコンソールUIを維持しつつ将来フルスクリーンTUI（`Textual`）へ無痛移行できるよう、以下の4層に分離されています。

1. **`base.py` (`GemioUI` 抽象基底クラス)**:
   スピナー制御コンテキストマネージャ（`thinking_status`, `search_status`, `pause_status`）、通常対話表示系、検索対話表示系、および対話入力プロンプト系の全メソッド契約を定義。
2. **`renderables.py`（純粋描画パーツ生成層）**:
   `console.print()` を一切呼ばず、データを受け取って Rich の `Panel` / `Table` / `Group` オブジェクトやヘッダーHTML文字列を返す純粋関数群（`build_*_panel`）。Textual の `Static` や `RichLog` ウィジェットにそのまま渡して描画できます。また、ログ文字列から `AgentLogCategory` を判定する `classify_agent_log()` を備えています。
3. **`prompts.py`（対話入力層）**:
   `prompt_toolkit` によるマルチライン入力セッション（`Esc+Enter` 送信、`Esc+Backspace` Undo可能クリア）と、`rich.prompt` による確認・選択プロンプトを提供します。
4. **`rich_ui.py` (`RichConsoleUI` 具象クラス)**:
   `self.console` と進行中スピナー `self._active_status` を内部保持し、`prompt_approval_choice` や `prompt_ask_user_response` 等の対話プロンプト呼び出し時には内部で自動的に `with self.pause_status():` を適用してスピナー描画崩れを防ぎます。

---

## 6. 主要処理のシーケンス・データフロー

### 6.1 起動・初期化・サンドボックス検証フロー
1. `main()` ➔ `parse_args()` でCLI引数を解析し、`GemioCLI(args)` を生成。
2. `_resolve_project_path()` で対象ディレクトリの存在を検証。
3. `_determine_initial_approval_mode()` で `--trust`（事前同意プロンプトあり、拒否時は `--edit` または `normal` へフォールバック）および `--edit` から初期承認モードを決定。
4. `--dev` 指定時は `DeveloperProject` を初期化（コンテナ判定・脱獄構成チェック。違反時は `SandboxSecurityError` パネルを表示して終了）。`in_container` かつ環境変数にAPIキー未設定なら `prompt_api_key_for_container()` でマスク入力を取得。
5. `ToolRegistry` に `project.agent_toolsets()` ＋ `get_builtin_toolset()` ＋ `load_project_toolsets()` を登録し、`--dynamic-tools` の有無に応じて `DynamicToolsetProvider` または `StaticToolProvider` を構築。
6. `SkillLoader.load_all()` でスキルを読み込み、`SkillManager` のメタツール（`activate_skill`）をプロバイダへ登録し、`SystemPromptComposer`・`GenAIClient`・`ChatSession`・`ChatAgent`・`SearchAgent` を結線。
7. `run()` でウェルカムバナー（および `--dev` 時は `/sandbox` 診断パネル）を表示し、REPLループを開始。

---

### 6.2 通常対話・Function Calling・承認/Abort・`ChatTurn` 確定フロー
```text
User入力 ➔ GemioCLI.run_chat_prompt()
  ├─ 1. search_agent.reset_history() (直前の検索深掘り文脈をクリア)
  └─ 2. with ui.thinking_status(): chat_agent.process_prompt(user_input)
        ├─ project.on_prompt_start() (_rejection_pending = False)
        ├─ [Loop: 最大 max_iterations 回]
        │    ├─ session.build_request_contents(turn_contents) (前提資料があれば先頭合成)
        │    ├─ config = {tools: provider.get_active_tools(), system_instruction: composer.compose()}
        │    ├─ client.generate_content(..., usage_source="chat") (リトライ + トークン記録)
        │    ├─ [function_calls がある場合]
        │    │    └─ 各 call について provider.execute(name, args)
        │    │         ├─ 変更系ツールなら Project._check_circuit_breaker() ➔ _confirm()
        │    │         │    └─ UI確認時は RichConsoleUI.pause_status() でスピナー自動一時停止
        │    │         └─ AgentExecutionAborted 発生時は未実行call分もダミー応答を補完しループ脱出
        │    └─ [最終テキスト応答の場合] ループ完了
        ├─ ChatTurn を構築し session.history.append_turn(turn) で確定保存
        └─ finally: project.on_prompt_end() (_rejection_pending = False)
```

---

### 6.3 `/search` Web検索〜`MaterialStore` 蓄積〜プロンプト先頭インジェクションフロー
```text
`/search <query>` ➔ GemioCLI.handle_search_command()
  ├─ 1. with ui.search_status(): result = search_agent.search(query, new_session=...)
  │      └─ GenAIClient.generate_content(contents=search_history, tools=[google_search], usage_source="search")
  ├─ 2. result.succeeded == True の場合のみ:
  │      chat_session.materials.add_search_result(query, response_text, sources)
  │      (本文の SHA-256 ハッシュで重複排除、物理パスや暗号化URLは非保持)
  └─ 3. 次回の通常プロンプト送信時:
         ChatSession.build_request_contents() が直近の user 発話先頭に【作業前提資料】を自動合成して送信
```

---

## 7. Pythonライブラリとしての公開APIリファレンス

### 7.1 `gemio` トップレベル公開シンボル一覧
`src/gemio/__init__.py` は、`cli`（`rich` や `prompt_toolkit`）をインポートせず、外部のPythonアプリケーションや自動化スクリプトから軽量にインポートできるよう設計されています。

* **エージェント & クライアント**: `GenAIClient`, `TokenUsageTracker`, `ChatAgent`, `ChatTurnResult`, `SearchAgent`, `SearchResult`
* **コンテキスト & セッション**: `ChatSession`, `ChatHistory`, `ChatTurn`, `MaterialStore`, `ReferenceMaterial`, `MaterialKind`, `ContextCompactor`, `CompactionResult`, `compute_sha256`
* **プロンプト & モデル設定**: `SystemPromptComposer`, `build_system_instruction`, `DEFAULT_SYSTEM_INSTRUCTION`, `DEVELOPER_MODE_INSTRUCTION_ADDON`, `DEFAULT_SEARCH_INSTRUCTION`, `DEFAULT_COMPACTION_INSTRUCTION`, `DEFAULT_AI_MODEL_NAME`, `MODEL_PRESETS`, `MODEL_ALIASES`, `resolve_model_name`, `AgentLogCategory`
* **Skills & ツール基盤**: `SkillLoader`, `SkillManager`, `SkillSpec`, `parse_skill_markdown`, `ToolRegistry`, `Toolset`, `ToolSpec`, `ToolProvider`, `StaticToolProvider`, `DynamicToolsetProvider`, `get_builtin_toolset`, `load_project_toolsets`, `agent_tools`, `get_current_date`, `get_current_time`
* **プロジェクト & セキュリティ**: `Project`, `DeveloperProject`, `APPROVAL_MODE_NORMAL`, `APPROVAL_MODE_EDIT`, `APPROVAL_MODE_TRUST`, `AgentExecutionAborted`, `SandboxSecurityError`, `scrub_environment`, `is_sensitive_env_key`, `truncate_command_output`, `summarize_text_for_display`

---

### 7.2 ライブラリ組み込み最小サンプルコード

CLIを介さず、独自のPythonプログラムからGemioの安全なファイル操作ガードレールと `ChatAgent` を呼び出す最小構成例です。

```python
from gemio import (
    APPROVAL_MODE_EDIT,
    ChatAgent,
    ChatSession,
    GenAIClient,
    Project,
    StaticToolProvider,
    SystemPromptComposer,
    ToolRegistry,
    get_builtin_toolset,
)

# 1. プロジェクト初期化（コールバックで独自の承認UIやログ処理を注入可能）
project = Project(
    project_path="./target_workspace",
    approval_mode=APPROVAL_MODE_EDIT,
    approval_callback=lambda desc: "y",  # 削除確認時のハンドラ
    ask_user_callback=lambda q, opts, ctx: "方針Aで進めてください",
)

# 2. ツールレジストリとプロンプト合成器の構築
registry = ToolRegistry()
for ts in project.agent_toolsets():
    registry.register_toolset(ts)
registry.register_toolset(get_builtin_toolset())

provider = StaticToolProvider(registry=registry)
composer = SystemPromptComposer(dev_mode=False, tool_provider=provider)

# 3. クライアント・セッション・エージェントの生成と実行
client = GenAIClient(model_name="flash")
session = ChatSession()
agent = ChatAgent(
    client=client,
    session=session,
    tool_provider=provider,
    prompt_composer=composer,
    on_prompt_start=project.on_prompt_start,
    on_prompt_end=project.on_prompt_end,
)

try:
    result = agent.process_prompt(
        "プロジェクトの構成を確認し、概要をまとめてください。",
        on_log_append=lambda msg, category="assistant": print(f"[{category}] {msg}"),
    )
    print("最終回答:", result.response_text)
    print("トークン統計:", agent.get_token_usage())
finally:
    client.close()
```

---

## 8. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-14 | AI Agent | 運用中 | 初版作成。3層レイヤー構造、全モジュール責務マップ、`core` / `project` / `cli` の詳細仕様、CLI接続状態マトリクス、主要シーケンス、公開ライブラリAPIを体系化。 |
