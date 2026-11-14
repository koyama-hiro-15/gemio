# 🛠️ Gemio コントリビューション & 拡張開発ガイド

**作成日:** 2026年11月14日  
**最終更新日:** 2026年11月14日  
**ステータス:** 運用中  
**対象システム:** Gemio リポジトリ全体 (`src/gemio/`, `tests/`, `manuals/`, `docs/`, `Dockerfile`, `compose.yaml`)  

### 【要約】
本マニュアルは、Gemio本体への機能追加・バグ修正・コードレビューを行うコントリビューター向けの実践開発ガイドです。開発環境の構築、厳守すべきアーキテクチャ制約、よく行う機能拡張（スラッシュコマンド追加、Projectツール追加、標準スキル追加、Core基盤APIのUI接続）の具体的な実装手順、セキュリティ定数変更時の同期チェックリスト、全12モジュールの単体テスト設計、およびドキュメント運用ルールを定めます。
（※内部構造や各クラスの仕様詳細については、先に **[`07_ARCHITECTURE_REFERENCE.md`](./07_ARCHITECTURE_REFERENCE.md)** を参照してください。）

---

## 目次
1. [はじめに：開発環境のセットアップと基本ワークフロー](#1-はじめに開発環境のセットアップと基本ワークフロー)
   - [1.1 プロジェクトの運営スタンスについて](#11-プロジェクトの運営スタンスについて)
   - [1.2 開発環境の構築手順](#12-開発環境の構築手順)
   - [1.3 改修作業の標準ステップ](#13-改修作業の標準ステップ)
2. [厳守すべきコーディング・アーキテクチャ規約](#2-厳守すべきコーディングアーキテクチャ規約)
3. [機能拡張レシピ（How-To ガイド）](#3-機能拡張レシピhow-to-ガイド)
   - [3.1 新しいスラッシュコマンドを追加する](#31-新しいスラッシュコマンドを追加する)
   - [3.2 `Project` / `DeveloperProject` に新しいツールを追加する](#32-project--developerproject-に新しいツールを追加する)
   - [3.3 パッケージ標準の組み込みスキル（`builtin/`）を追加する](#33-パッケージ標準の組み込みスキルbuiltinを追加する)
   - [3.4 UIパネル・対話プロンプトを追加・変更する（4層UI規約）](#34-uiパネル対話プロンプトを追加変更する4層ui規約)
   - [3.5 先行実装済みのCoreコンテキストAPIをCLI/TUIへ接続する](#35-先行実装済みのcoreコンテキストapiをclituiへ接続する)
4. [セキュリティ・定数変更時の同期チェックリスト](#4-セキュリティ定数変更時の同期チェックリスト)
5. [単体テストスイート（`pytest`）の構成とテスト作成ガイド](#5-単体テストスイートpytestの構成とテスト作成ガイド)
   - [5.1 全12テストモジュールの責務一覧](#51-全12テストモジュールの責務一覧)
   - [5.2 単体テスト作成時の鉄則（オフライン・Docker不要・シークレット非混入）](#52-単体テスト作成時の鉄則オフラインdocker不要シークレット非混入)
6. [ドキュメント運用ルール](#6-ドキュメント運用ルール)
   - [6.1 ドキュメント体系の役割分担（`manuals/` vs `docs/`）](#61-ドキュメント体系の役割分担manuals-vs-docs)
   - [6.2 `docs/DOCUMENTATION_RULE.md` 準拠の必須ヘッダーと更新履歴](#62-docsdocumentation_rulemd-準拠の必須ヘッダーと更新履歴)
   - [6.3 `docs/PROJECT_SUMMARY.md` への追記ルール](#63-docsproject_summarymd-への追記ルール)
7. [更新履歴](#7-更新履歴)

---

## 1. はじめに：開発環境のセットアップと基本ワークフロー

### 1.1 プロジェクトの運営スタンスについて
Gemioは現在プロトタイプ版（Apache License 2.0）として公開されており、セキュリティ検証、バグ報告、テスト拡充などのフィードバックを歓迎しています。なお、本プロジェクトは個人主導で無理のないペースで開発・メンテナンスを行っておりますので、IssueやPull Requestへの応答にお時間をいただく場合がある点をご了承ください。

---

### 1.2 開発環境の構築手順

```bash
# 1. クローンと仮想環境の作成・有効化（Python 3.11 以上）
git clone https://github.com/koyama-hiro-15/gemio.git
cd gemio
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1

# 2. 依存関係と gemio の編集可能（Editable）インストール
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

# 3. 単体テストが全件パスすることを確認（APIキー・Docker不要で実行可能）
pytest
```

開発者モード（`--dev`）や自身の改修をサンドボックス内で検証する場合は、同梱の `compose.yaml` を使用できます：
```bash
docker compose up -d
pytest  # ホスト側でも docker exec gemio-sandbox pytest でも実行可能
```

---

### 1.3 改修作業の標準ステップ
1. **現状コードと設計書の確認**: 関連する `src/gemio/` の最新コードおよび `docs/design/` の設計書を確認します（推論や記憶だけで修正しないこと）。
2. **設計書の作成・更新（中規模以上の機能変更時）**: `docs/DOCUMENTATION_RULE.md` に従い、`docs/design/` に設計書を作成または更新します。
3. **実装と構文検証**: コード修正後、Pythonファイルの構文エラーがないことを確認します。
4. **単体テストの追加と全件実行**: `tests/` の対応モジュールにテストを追加し、`pytest` が全件合格することを確認します。
5. **隔離環境での手動テスト（UI・対話挙動変更時）**: [`09_MANUAL_TEST_GUIDELINE.md`](./09_MANUAL_TEST_GUIDELINE.md) の `git worktree` 手順に従い、メインツリーを汚さずに実機確認を行います。
6. **マニュアルおよび `docs/PROJECT_SUMMARY.md` の同期**: 利用者・開発者への影響に応じてマニュアルとサマリーを更新します。

---

## 2. 厳守すべきコーディング・アーキテクチャ規約

コントリビューション時は、以下のアーキテクチャ制約を必ず守ってください（一部は単体テストで自動検査されています）。

1. **レイヤー単方向依存（`cli` ➔ `project` ➔ `core`）の維持**:
   * `core` から `project` や `cli` をインポートしてはいけません。
   * `project` から `cli` をインポートしてはいけません。
2. **UI依存の完全隔離**:
   * `core`、`project`、および `src/gemio/__init__.py` に `rich` や `prompt_toolkit` をインポートさせないでください。
   * `src/gemio/cli/main.py` にも `rich` や `prompt_toolkit` を直接インポートしてはいけません（`test_main_py_has_no_direct_prompt_toolkit_or_rich_imports` で検証）。必ず `GemioUI` インターフェース経由で呼び出してください。
3. **軽量モジュールのSDK非依存**:
   * `src/gemio/core/exceptions.py` と `src/gemio/core/models.py` には `google.genai` をインポートしないでください。
4. **ローカル物理パスの秘匿**:
   * AIに渡るシステムプロンプト、ツール応答、`ReferenceMaterial`、`SkillSpec`、`ToolSpec`、および警告メッセージに、ホストOSの絶対パス（`C:\Users\...` や `/home/...`）や生の `OSError` パス文字列を含めないでください。
5. **設定改ざんによるテスト回避の禁止**:
   * テストが失敗した際、`pyproject.toml` 等を書き換えてテスト対象から除外する行為は厳禁です。

---

## 3. 機能拡張レシピ（How-To ガイド）

### 3.1 新しいスラッシュコマンドを追加する

例として `/example` コマンドを追加する場合、以下の4ステップで実装します：

1. **純粋パネル生成関数の追加 (`src/gemio/cli/ui/renderables.py`)**:
   * `build_example_panel(...) -> Panel` を実装します（ここでは `console.print` を呼ばず `Panel` を返します）。
   * あわせて `build_help_panel()` のコマンド一覧にも `/example` の説明を追記します。
2. **UIインターフェースと具象実装への追加 (`base.py`, `rich_ui.py`, `ui/__init__.py`)**:
   * `GemioUI` (`src/gemio/cli/ui/base.py`) に `@abstractmethod def print_example_panel(...) -> None:` を追加します。
   * `RichConsoleUI` (`src/gemio/cli/ui/rich_ui.py`) で `self.console.print(build_example_panel(...))` を実装します（対話入力を伴う場合は `with self.pause_status():` で囲みます）。
3. **コマンドハンドラの実装 (`src/gemio/cli/main.py`)**:
   * `GemioCLI` に `handle_example_command(self, user_input: str) -> None` を実装し、`dispatch_input()` に分岐を追加します。
4. **単体テストの追加**:
   * `tests/test_ui_renderables.py`（パネル描画検証）、`tests/test_ui_rich.py`（出力委譲検証）、`tests/test_cli_main.py`（`dispatch_input` 検証）にテストを追加します。

---

### 3.2 `Project` / `DeveloperProject` に新しいツールを追加する

ファイル操作や探索・検証用の新しいツールメソッドを追加する際は、以下のチェックポイントをすべて満たしてください。

1. **パス境界検証の徹底**:
   * 受け取った相対パスは必ず最初に `resolved = self._verify_and_get_path(file_path, is_write=...)` で解決・検証してください（機密ファイル・パストラバーサル・保護領域が自動検査されます）。
2. **変更系ツールでのサーキットブレーカー＆承認制御**:
   * ファイルを変更・作成・削除・実行するツールでは、冒頭で `self._check_circuit_breaker()` を呼び出し、実行前に `self._confirm(action_desc, force_confirm_in_edit=...)` で承認確認を行ってください。
   * 取り返しのつきにくい破壊的操作（削除や外部コマンド実行相当）の場合は `force_confirm_in_edit=True` を指定し、エディットモードでも確認が入るようにします。
   * **`AgentExecutionAborted` 例外はキャッチして握り潰さず、必ず `except AgentExecutionAborted: raise` で外へ再送出**してください（`ChatAgent` 側でループ強制停止処理を行うため）。それ以外の一般例外は `[ERROR] ...` 文字列として返します。
3. **Docstring の記述**:
   * 先頭1行目に簡潔な機能概要（`extract_tool_summary` で動的カタログに使われます）を書き、`Args:` と `Returns:` を明記します。
4. **`agent_tools()` と `agent_toolsets()` の両方へ登録（必須）**:
   * `agent_tools()` リストに追加すると同時に、`agent_toolsets()` の適切な論理ツールセット（`project_explore`, `project_read`, `project_write`, `sandbox` 等）にも必ず追加してください。
   * ※ `tests/test_core_tools_and_prompts.py` の `test_project_and_developer_project_agent_toolsets_match_agent_tools` により、両者の関数集合が完全一致していることがテスト強制されています。

---

### 3.3 パッケージ標準の組み込みスキル（`builtin/`）を追加する

全ユーザー・全プロジェクトで共通利用できる標準スキルをGemio本体に同梱する場合：
1. `src/gemio/core/skills/builtin/<スキル名>/SKILL.md` を作成します。
2. 先頭に `---` で囲んだフロントマター（`name`, `description`, `recommended_toolsets`）を記載し、本文に手順を記述します（上限 256 KB）。
3. 追加コード不要で、`SkillLoader.load_all()` によって起動時に自動認識されます（なお、利用者が自身のプロジェクトの `.gemio/skills/<同名>/SKILL.md` を置いた場合はプロジェクト側で上書き可能です）。

---

### 3.4 UIパネル・対話プロンプトを追加・変更する（4層UI規約）

将来の `Textual` TUI 移行を妨げないため、UI変更時は以下のルールを守ってください：
* **Rich マークアップのエスケープ**: ユーザー入力値、ファイルパス、例外メッセージ、コマンド出力などの動的文字列をマークアップ文字列に埋め込む際は、必ず `from rich.markup import escape` でエスケープしてください（例: `[a]` のような角括弧がマークアップタグと誤認されて消えるバグを防ぎます）。
* **描画と出力の分離**: 色分けやテーブル構築ロジックはすべて `renderables.py` の `build_*` 関数内に書き、`rich_ui.py` はそれを `self.console.print()` するだけの薄い層に保ちます。

---

### 3.5 先行実装済みのCoreコンテキストAPIをCLI/TUIへ接続する

[`07_ARCHITECTURE_REFERENCE.md` (3.2節)](./07_ARCHITECTURE_REFERENCE.md#32-コンテキストセッション管理-corecontext) に記載の通り、以下の機能はすでに `gemio.core.context` に実装・テスト済みです。今後これらをスラッシュコマンドやTUI操作として公開する際は、以下の設計ルールに従ってください。

1. **対話の巻き戻し (`self.chat_session.rewind(steps=1)`)**:
   * `ChatTurn` 単位で安全に履歴が巻き戻されます（ローカルファイルへの変更自体はGit管理領域の責務であるため、「対話記憶のみを巻き戻した」旨をUIパネルで明示してください）。
2. **コンテキスト要約圧縮 (`self.chat_session.compact(self.ai_client, keep_recent_turns=2)`)**:
   * 実行中は `self.ui.thinking_status()` 等のスピナーを表示し、結果（`CompactionResult.compacted`, `summarized_turns`, `retained_turns`）を通知パネルで表示します。
3. **ユーザー指定ファイルの作業前提資料への追加 (`self.chat_session.materials.add_user_document(...)`)**:
   * **【最重要セキュリティ規約】** ファイルを読み込む際は生で `open()` せず、必ず `self.project.read_file(rel_path)`（または `_verify_and_get_path`）を通して**プロジェクト境界チェックと `.env` 等の機密ファイルブロックを通過**させてください。
   * `MaterialStore` には物理パスを渡さず、`title` にはファイル名のみ（またはユーザー指定ラベル）、`source_hash` には `compute_sha256(正規化相対パス)` でハッシュ化した不透明なダイジェストのみを渡してください。
   * また、この操作は**人間のコマンド/UI操作専用**とし、AIが呼び出せるツール関数として公開しないでください。

---

## 4. セキュリティ・定数変更時の同期チェックリスト

保護対象ファイルやモデル定義などを更新する際は、漏れを防ぐため以下の関連ファイルをセットで確認・同期してください。

| 変更内容 | 同時に確認・更新すべきファイル一覧 |
| :--- | :--- |
| **機密ファイル・機密ディレクトリパターンの追加**<br>(`SECRET_FILE_PATTERNS`, `SECRET_DIR_NAMES`, `ALLOWED_ENV_FILES`) | 1. `src/gemio/project/base.py`（定数定義）<br>2. `.gitignore` および `.dockerignore`（混入防止リストの同期）<br>3. `tests/test_project_base.py` & `tests/test_project_developer.py`<br>4. `manuals/01_USER_MANUAL.md`, `02_DEVELOPER_MODE_MANUAL.md`, `03_DOCKER_CONTAINER_MANUAL.md` |
| **自動走査除外ディレクトリの追加**<br>(`IGNORED_DIRS`, `IGNORED_DIR_PATTERNS`) | 1. `src/gemio/project/base.py`<br>2. `src/gemio/project/developer.py`（`_collect_local_workspace_raw_entries` 内のスキップ判定確認）<br>3. `tests/test_project_base.py` |
| **管理・仮想環境ディレクトリの追加**<br>(`SENSITIVE_ENV_DIR_NAMES`) | 1. `src/gemio/project/base.py`<br>2. `src/gemio/project/developer.py`（`classify_workspace_entry` の警告文分岐）<br>3. `manuals/02_DEVELOPER_MODE_MANUAL.md`, `04_SANDBOX_PROTECTION_GUIDELINE.md` |
| **子プロセス除去環境変数の追加**<br>(`SCRUBBED_ENV_KEYS`, `SCRUBBED_ENV_SUFFIXES`) | 1. `src/gemio/project/developer.py`<br>2. `tests/test_project_developer.py`<br>3. `manuals/02_DEVELOPER_MODE_MANUAL.md` |
| **AIモデルプリセット・エイリアスの追加・変更**<br>(`DEFAULT_AI_MODEL_NAME`, `MODEL_PRESETS`, `MODEL_ALIASES`) | 1. `src/gemio/core/models.py`<br>2. `src/gemio/cli/ui/renderables.py`（`build_help_panel` 内の例示）<br>3. `tests/test_core_client_and_models.py`, `tests/test_cli_main.py` |
| **公開コアAPI（新クラス・定数）の追加** | 1. 各サブパッケージの `__init__.py`<br>2. `src/gemio/core/__init__.py` および `src/gemio/__init__.py` (`__all__`) |

---

## 5. 単体テストスイート（`pytest`）の構成とテスト作成ガイド

### 5.1 全12テストモジュールの責務一覧

`tests/` 配下は、パッケージ構造と1対1に対応する12モジュールに整理されています。

| レイヤー | テストファイル | 主な検証内容 |
| :--- | :--- | :--- |
| **Core** | `tests/test_core_client_and_models.py` | モデル名・エイリアス解決、`GenAIClient` の初期化・モデル切替・503リトライ回復、`TokenUsageTracker` の `chat`/`search`/`compaction` 分離集計 |
| **Core** | `tests/test_core_context.py` | `ChatTurn` 履歴管理・`rewind`・範囲抽出・Markdown整形、`MaterialStore` の物理パス非保持・SHA-256重複排除・`source_hash` 更新、`ContextCompactor`、`ChatSession` 先頭注入 |
| **Core** | `tests/test_core_tools_and_prompts.py` | 標準日時ツール、`ToolRegistry` / `StaticToolProvider`、`.gemio/tools/` ローダーと物理パス秘匿、`agent_tools` と `agent_toolsets` の一致検証、`SystemPromptComposer` |
| **Core** | `tests/test_core_skills_and_dynamic_tools.py` | `parse_skill_markdown`、固定2箇所からの `SkillLoader`・優先上書き・遅延ロード例外時のパス秘匿、`DynamicToolsetProvider` (`request_tools`) と `activate_skill` のループ途中即時反映 |
| **Core** | `tests/test_core_agent_chat.py` | `ChatAgent` のFunction Callingループ、`ask_user` / Abort時の複数 `function_call` ペア整合性、ループ上限継続とロールバック、503中断時のツール結果保持、`MaterialStore` 操作非露出検証 |
| **Core** | `tests/test_core_agent_search.py` | `SearchAgent` のGoogle検索専用Config、出典抽出・重複排除、ローカル履歴・ユーザー資料の非送信（機密隔離）、連続深掘り検索、通信エラー時のロールバックと `succeeded=False` |
| **Project** | `tests/test_project_base.py` | ファイル操作一式、パストラバーサル・シンボリックリンク遮断、機密ファイル/ディレクトリ・`.git`・`.devcontainer`・`.gemio`・`*.egg-info` 保護、BFS探索と省略通知、3段階承認、サーキットブレーカー |
| **Project** | `tests/test_project_developer.py` | コンテナ実体判定、脱獄構成の `SandboxSecurityError` 拒否、環境変数スクラブ、値非保持の機密キー名検出、`parse_proc_mountinfo` / `classify_workspace_entry` / `inspect_sandbox_status`、`run_sandbox_command` 全文表示・タイムアウト・プロセスキル |
| **CLI** | `tests/test_cli_main.py` | `main.py` のUI直接インポート禁止AST検査、`parse_args`、モック `GemioUI` 注入による起動フロー・全スラッシュコマンド・コールバック・`Ctrl+C` 2段階挙動・`--dynamic-tools`/Skillsリセット連携 |
| **CLI/UI** | `tests/test_ui_renderables.py` | `format_prompt_header`（モード・トークン閾値色分け）、`classify_agent_log`、全 `build_*_panel` の純粋生成と出力テキスト検証 |
| **CLI/UI** | `tests/test_ui_prompts.py` | バッファクリア（Undo維持）、キーバインド（`Esc+Enter`, `Esc+Backspace`）、コンテナ用APIキーマスク入力、承認4択・上限継続・`ask_user`・モデル選択プロンプト |
| **CLI/UI** | `tests/test_ui_rich.py` | `RichConsoleUI` へのカスタム `Console` 注入、スピナーのライフサイクル管理、対話プロンプト呼び出し時の自動 `pause_status()` 動作、ログ分類出力 |

---

### 5.2 単体テスト作成時の鉄則（オフライン・Docker不要・シークレット非混入）

1. **実API通信・実Dockerデーモンへの非依存**:
   * 単体テストはネットワーク遮断コンテナ（`--network none`）内やCI環境でも100%動作しなければなりません。
   * Gemini API 呼び出しは `unittest.mock.patch("google.genai.Client")` でモック化し、Docker CLI 呼び出しは `subprocess.run` / `subprocess.Popen` のモック化で行ってください。
2. **ファイル操作テストでの `tmp_path` 使用**:
   * リポジトリ内の実ファイルを汚さないよう、必ず `pytest` 標準の `tmp_path` フィクスチャを使用してください。
3. **シークレットスキャナー誤検知の防止**:
   * テストコードやドキュメント内にダミーのAPIキーやトークンを記述する際は、実在のキー形式（`AIza...`, `sk-...`, `ghp_...` 等）に似せず、必ず **`"DUMMY_..."`** または **`"<YOUR_...>"`** という明示的なダミー文字列を使用してください。

---

## 6. ドキュメント運用ルール

### 6.1 ドキュメント体系の役割分担（`manuals/` vs `docs/`）

| 配置場所 | 目的・対象読者 | 運用方針 |
| :--- | :--- | :--- |
| **`manuals/01`〜`06`** | **エンドユーザー向けマニュアル**<br>（導入、開発者モード、Docker構築、保護基準、Q&A、Skills/Tools） | 現在利用可能な機能の使い方・ベストプラクティスを平易かつ最新に保つ。 |
| **`manuals/07`〜`09`** | **コントリビューター向けリファレンス & ガイド**<br>（アーキテクチャ仕様、拡張開発ガイド、手動テスト手順） | コードベース全体の見取り図・拡張手順・検証手順を最新に保つ。 |
| **`docs/design/`** | **個別機能の詳細設計書**<br>（背景課題 As-Is、採用した設計 To-Be、代替案検討の記録） | 新機能や大規模リファクタリング実施時に作成・更新し、設計意図（Why）を保存する。 |
| **`docs/PROJECT_SUMMARY.md`** | **開発履歴サマリー（時系列ログ）** | 改修完了ごとに作業内容の要約を**末尾へ日付付きで追記**する。 |

---

### 6.2 `docs/DOCUMENTATION_RULE.md` 準拠の必須ヘッダーと更新履歴
`manuals/` および `docs/design/` のドキュメントを新規作成・更新する際は、[`docs/DOCUMENTATION_RULE.md`](../docs/DOCUMENTATION_RULE.md) に従い、タイトル直下に以下のメタデータと【要約】を記載し、更新時は `最終更新日` と末尾の `更新履歴` テーブルを更新してください。

```markdown
# ドキュメントタイトル

**作成日:** YYYY年MM月DD日  
**最終更新日:** YYYY年MM月DD日  
**ステータス:** 運用中（または 設計完了 / 実装完了 等）  
**対象システム:** `src/gemio/...`  

### 【要約】
（2〜4行で目的・対象機能・要点を端的に記載）
```

---

### 6.3 `docs/PROJECT_SUMMARY.md` への追記ルール
機能追加・リファクタリング・ドキュメント整備などの作業を完了した際は、[`docs/PROJECT_SUMMARY.md`](../docs/PROJECT_SUMMARY.md) の既存履歴を書き換えたり削除したりせず、**必ずファイル末尾の「更新履歴」セクションに作業日付（`YYYY年MM月DD日`）付きで要約を追記**してください。

---

## 7. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-14 | AI Agent | 運用中 | 初版作成。開発環境構築、アーキテクチャ遵守規約、5つの機能拡張レシピ、セキュリティ同期チェックリスト、全12単体テストモジュールの構成・鉄則、およびドキュメント運用ルールを策定。 |
