# Textual移行およびエージェント分離を見据えたUIパッケージ構造リファクタリング設計書

**作成日:** 2026年11月12日  
**最終更新日:** 2026年11月12日  
**ステータス:** 実装完了  
**対象システム:** `src/gemio/cli/main.py`, `src/gemio/cli/__init__.py`, `src/gemio/cli/ui/` (`base.py`, `renderables.py`, `prompts.py`, `rich_ui.py`, `__init__.py`), `tests/`  

### 【要約】
Gemioの多機能化に伴うコンソールUIの表現力限界（対話ログとツール呼び出し/ステータス表示の混在、最下部固定プロンプトによる過去ログ参照の困難さ等）を解消し、将来的な `rich` から `textual` へのTUI移行および `AIChatAgent` の「通常対話処理」と「検索対話処理」の分離をスムーズに進めるため、現行のUI・操作感を維持したまま `src/gemio/cli/ui/` パッケージの構造を4層（抽象UI基底・Renderable純粋生成・対話プロンプト・Rich具象実装）へリファクタリングし、あわせてテストスイート全体を現行アーキテクチャ直結の9モジュール構成へ刷新します。

---

## 1. 背景と解決すべき課題

### 1.1 現行の対話型UIにおける表現力の限界
Gemioの機能拡充に伴い、標準出力へ順次出力していく従来のストリーム型CLI（`rich` + `prompt_toolkit`）では以下のUX上の課題が顕在化しています。

1. **対話ログとツール呼び出し・ステータス表示の混在**:
   AIとの本来の対話（質問・回答）と、途中のツール呼び出し（`Tool call: ...`）・リトライ警告・各種ステータス出力が単一ストリームに混在して流れるため、対話の文脈を追いにくくなっている。
2. **プロンプト入力中のログ閲覧性の制限**:
   プロンプト入力欄がコンソール最下部に固定されているため、上方に流れた過去のログや調査結果をスクロールして見比べながらプロンプトを作成することが難しい。

### 1.2 アーキテクチャ上の課題（As-Is）
直近のリファクタリング（`design_cli_architecture_refactoring.md`）により、`main.py` から `rich` / `prompt_toolkit` の直接インポートは排除され `src/gemio/cli/ui.py` に集約されました。しかし、今後の **「Textualへの移行」** と **「`AIChatAgent` の通常対話・検索対話分離」** を見据えると、単一ファイルの `ui.py` には以下の課題が残っていました。

1. **手続き的なモジュール直接呼び出しへの依存**:
   `GemioCLI` が `ui.print_welcome_banner(...)` 等のモジュール関数を直接呼び出しており、UIバックエンド（RichコンソールUI ⇄ Textual TUIアプリ）を抽象化して差し替える仕組みがない。
2. **「描画パーツ（Renderable）の構築」と「コンソール出力（`console.print`）」の密結合**:
   TextualはRichのRenderable（`Panel`, `Table`, `Markdown`, `Group`, `Text`）をそのままウィジェット（`RichLog` や `Static`）に描画できるにもかかわらず、現行の `print_*` 関数は内部で即座に `console.print()` を呼んでしまっているため、描画パーツ単体を取り出してTextualウィジェットへ渡すことができない。
3. **通常対話ログとツール実行ログの未分類**:
   `AIChatAgent` からのログ出力が単一の文字列コールバックとなっており、Textual移行時に「メイン対話ペイン」と「ツール・アクティビティペイン」へ振り分けるための分類機構がない。
4. **スピナー（`active_status`）制御の制御層への漏れ出し**:
   `GemioCLI` 側で `active_status` の生成・保持・一時停止（`_pause_status`）を管理しており、コンソールスピナー特有の事情が制御層に露出している。

---

## 2. 今後の `AIChatAgent` リファクタリング（通常対話 / 検索対話分離）との連携方針

今後予定されている `AIChatAgent` のリファクタリングでは、現在1つのクラスが抱えている **「通常の対話処理（Function Callingループ・会話履歴管理）」** と **「検索対話処理（Google Search Grounding・検索セッション管理・作業前提資料生成）」** が分離されます。

この分離作業と今回のUIリファクタリングが互いに干渉せず相乗効果を生むよう、以下の設計方針を採用します。

1. **今回のフェーズでは `src/gemio/core/agent.py` 本体には変更を加えない**:
   `AIChatAgent` のメソッドシグネチャやコールバック仕様を今の段階で暫定変更すると、次回のエージェント分離リファクタリング時に二重修正となるため、`core/agent.py` は無変更のまま維持します。
2. **UIインターフェース（`GemioUI`）側で「通常対話系」と「検索対話系」の受け口を明確に分離する**:
   * **通常対話系**: `thinking_status()`, `print_agent_log()`, `print_chat_divider()`, `print_unexpected_error()`
   * **検索対話系**: `search_status()`, `print_search_response()`, `print_search_materials_cleared()`, `print_search_help()`, `print_search_error()`
   * これにより、次回エージェントが「通常対話クラス」と「検索対話クラス」に分割された際にも、`GemioCLI` から各UIメソッドへの接続をそのまま維持できます。
3. **UI層での透過的なログ種別判定 (`classify_agent_log`)**:
   `core/agent.py` を変更せずに「対話ログ」と「ツール呼び出し・ステータス表示」を識別できるよう、UI層（`renderables.py`）にログ文字列の種別（`user` / `tool` / `warning` / `abort` / `search` / `assistant`）を判定する `classify_agent_log()` を実装します。
   * 現行の `RichConsoleUI` では従来の `[💬 Agent Log]` パネル表示を完全に維持します。
   * 将来の `TextualUI` ではこの分類結果を用いてペイン振り分けを行え、さらに `AIChatAgent` リファクタリング時にはエージェント側から明示的にカテゴリを指定する方式へシームレスに移行できます。

---

## 3. 新しい `src/gemio/cli/ui/` パッケージ構造（To-Be）

単一ファイルの `src/gemio/cli/ui.py` を **`src/gemio/cli/ui/` パッケージ** に昇格させ、責務ごとに4つのモジュール＋公開ファサード（`__init__.py`）へ分割します。

```text
src/gemio/cli/
├── __init__.py          # CLIパッケージ公開定義（GemioCLI, GemioUI, RichConsoleUI, main, parse_args 等）
├── main.py              # 【制御層】GemioCLI（GemioUI 抽象インターフェースを介して入出力を制御）
└── ui/
    ├── __init__.py      # 【互換・公開層】GemioUI, RichConsoleUI および既存の全 print_* / prompt_* / build_* 関数を再公開
    ├── base.py          # 【抽象UI層】GemioUI 抽象基底クラス（ABC）、AgentLogCategory 定数定義
    ├── renderables.py   # 【描画パーツ生成層】Richの Panel / Table / Group / HTMLヘッダーを生成する純粋関数群
    ├── prompts.py       # 【対話入力層】prompt_toolkit / rich.prompt を用いたユーザー入力・選択プロンプト関数群
    └── rich_ui.py       # 【具象UI層】現行の Rich + prompt_toolkit による GemioUI 実装クラス (RichConsoleUI)
```

---

## 4. 各モジュールの詳細設計

### 4.1 `src/gemio/cli/ui/base.py`（抽象UI基底クラス `GemioUI`）

すべてのUIバックエンド（現行の `RichConsoleUI` および将来の `TextualUI`）が満たすべき契約（Contract）を抽象基底クラス `GemioUI` として定義します。

* **ログカテゴリ定義 (`AgentLogCategory`)**:
  * `USER = "user"`（ユーザーのプロンプト入力）
  * `ASSISTANT = "assistant"`（AIの応答テキスト）
  * `TOOL = "tool"`（`Tool call: ...` による機能呼び出し）
  * `WARNING = "warning"`（リトライ待機等の警告）
  * `ABORT = "abort"`（作業の強制中止通知）
  * `SEARCH = "search"`（Web検索ログ）
* **ステータス・スピナー管理（コンテキストマネージャ）**:
  * `thinking_status() -> ContextManager`: 通常対話の思考・ツール実行中ステータス表示
  * `search_status(is_subsequent: bool = False) -> ContextManager`: Web検索中ステータス表示
  * `pause_status() -> ContextManager`: 対話確認プロンプト表示中の一時停止制御
* **画面出力・パネル表示メソッド**:
  * 単発メッセージ：`print_cli_error`, `print_dim_message`, `print_exit_message`, `print_chat_divider`, `print_initial_approval_mode_notice`
  * 情報パネル：`print_welcome_banner`, `print_help_panel`, `print_sandbox_status_panel`, `print_token_usage_panel`, `print_session_reset_panel`, `print_edit_mode_changed_panel`, `print_trust_mode_changed_panel`, `print_model_changed_panel`
  * 通常対話ログ：`print_agent_log_panel(log_message, category=None)`
  * 検索対話表示：`print_search_materials_cleared_panel`, `print_search_help_panel`, `print_search_response_panel`
  * エラー表示：`print_sandbox_security_error_panel`, `print_search_error_panel`, `print_unexpected_error_panel`
* **対話的確認・プロンプト入力メソッド**:
  * `create_prompt_session()`
  * `prompt_user_input(session, project, token_usage)`
  * `request_trust_mode_consent() -> bool`
  * `prompt_api_key_for_container(prompt_fn=None) -> str`
  * `prompt_approval_choice(action_description: str) -> str`
  * `prompt_iteration_limit_continue() -> tuple[bool, int]`
  * `prompt_ask_user_response(question, options, context) -> str`
  * `prompt_model_selection(current_model: str) -> str | None`

### 4.2 `src/gemio/cli/ui/renderables.py`（描画パーツ純粋生成層）

`console.print()` などの副作用を持たず、データを受け取って Rich の Renderable オブジェクト（`Panel`, `Table`, `Group`, `Text`）やフォーマット済み文字列を返す純粋関数群です。

| 関数名 | 戻り値 | 説明・Textualでの再利用用途 |
| :--- | :--- | :--- |
| `format_prompt_header(project, token_usage)` | `str` (HTML) | プロンプト上部のステータスヘッダー文字列生成 |
| `format_dev_mode_label(project)` | `str` (Markup) | 動作モードの装飾ラベル文字列生成 |
| `classify_agent_log(log_message)` | `str` | ログ文字列から `AgentLogCategory` を判定 |
| `build_welcome_panel(...)` | `Panel` | 起動時のウェルカムバナーパネルを生成 |
| `build_help_panel()` | `Panel` | `/help` 操作ガイド＆コマンド一覧パネルを生成 |
| `build_sandbox_status_panel(project)` | `Panel` | `/sandbox` 診断テーブル＆アラートパネルを生成 |
| `build_token_usage_panel(token_usage)` | `Panel` | `/tokens` コンテキスト診断＆累積トークン表パネルを生成 |
| `build_session_reset_panel(...)` | `Panel` | `/clear` セッションリセット完了パネルを生成 |
| `build_edit_mode_changed_panel(enabled)` | `Panel` | `/edit` モード切替通知パネルを生成 |
| `build_trust_mode_changed_panel(enabled)` | `Panel` | `/trust` モード切替通知パネルを生成 |
| `build_model_changed_panel(model_name)` | `Panel` | `/model` モデル切替通知パネルを生成 |
| `build_search_materials_cleared_panel(count)` | `Panel` | `/search --clear` 資料破棄完了パネルを生成 |
| `build_search_help_panel()` | `Panel` | `/search` 使い方案内パネルを生成 |
| `build_search_response_panel(...)` | `Panel \| None` | Web検索回答と参照元リンクのパネルを生成 |
| `build_agent_log_panel(log_message, category)` | `Panel` | エージェントログのMarkdownパネルを生成 |
| `build_sandbox_security_error_panel(error)` | `Panel` | サンドボックス安全性検証エラーパネルを生成 |
| `build_search_error_panel(error)` | `Panel` | Web検索エラーパネルを生成 |
| `build_unexpected_error_panel(error)` | `Panel` | 予期せぬエラーパネルを生成 |
| `build_trust_mode_consent_panel()` | `Panel` | トラストモード有効化前の注意事項パネルを生成 |
| `build_api_key_guide_panel()` | `Panel` | コンテナ内起動時のAPIキー入力案内パネルを生成 |
| `build_approval_panel(action_description)` | `Panel` | Human-in-the-loop 承認要求パネルを生成 |
| `build_ask_user_panel(question, options, context)` | `Panel` | AIからの質問・選択肢（Markdown対応）パネルを生成 |
| `build_model_selection_panel(current_model)` | `Panel` | `/model` プリセット選択パネルを生成 |

### 4.3 `src/gemio/cli/ui/prompts.py`（対話入力プロンプト層）

`prompt_toolkit` および `rich.prompt` を用いてユーザーからのキーボード入力を受け取る関数群です。事前表示するパネルはすべて `renderables.py` の `build_*_panel` を呼び出して描画します。

### 4.4 `src/gemio/cli/ui/rich_ui.py`（`RichConsoleUI` 具象クラス）

現行のコンソールUI動作を実現する `GemioUI` の具象実装クラスです。

* インスタンス生成時に `Console` を注入可能（`RichConsoleUI(console=...)`）とし、グローバル変数への依存や手続き的な `print_*` ラッパー関数の重複定義を完全に排除します。
* `thinking_status()` / `search_status()` コンテキストマネージャ内でスピナー（`self._active_status`）を生成・開始・終了し、スピナーの実装詳細を `RichConsoleUI` 内部へ完全にカプセル化します。
* `prompt_approval_choice`、`prompt_iteration_limit_continue`、`prompt_ask_user_response` 等の対話プロンプトメソッド呼び出し時には、内部で自動的に `with self.pause_status():` を適用してスピナーを一時停止・再開します。

### 4.5 `src/gemio/cli/main.py`（`GemioCLI` のUI抽象化対応）

* `GemioCLI.__init__(self, args: argparse.Namespace, ui_backend: GemioUI | None = None)` として外部から任意の `GemioUI` 実装を注入（DI）可能にします（省略時は `RichConsoleUI()` を使用）。
* `GemioCLI` からスピナー管理（旧 `active_status` / `_pause_status`）を完全に除去し、すべての画面表示・ステータス管理・プロンプト入力を `self.ui` 経由に統一します。

---

## 5. テストスイートの再設計（アーキテクチャ直結型9モジュール）

開発初期からの継ぎ足しにより複数層が混在していたテストファイルを、現行のパッケージ構造（`core`, `project`, `cli`, `cli.ui`）および今後のエージェント分離・Textual移行に直結する以下の9モジュールへ刷新します。

| 層 | テストファイル | 検証対象と責務 |
| :--- | :--- | :--- |
| **Core** | `tests/test_core_tools_and_prompts.py` | `core/tools.py`（日時取得）および `core/prompts.py`（基本/開発者モード・検索用インストラクション構築） |
| **Core** | `tests/test_core_agent_chat.py` | `core/agent.py` の **通常対話処理**（モデル解決・Function Callingループ・`ask_user`/Abort・上限継続・503リトライ回復・トークン集計） |
| **Core** | `tests/test_core_agent_search.py` | `core/agent.py` の **検索対話・作業前提資料処理**（Search Grounding・機密履歴隔離・連続深掘り検索・前提資料蓄積/先頭注入・検索エラー回復） |
| **Project** | `tests/test_project_base.py` | `project/base.py`（ファイル操作・BFS探索・機密/管理領域保護・3段階承認モード・サーキットブレーカー・`ask_user`）※UI非依存 |
| **Project** | `tests/test_project_developer.py` | `project/developer.py`（コンテナ判定・セーフティゲート・環境変数スクラブ・`run_sandbox_command`・マウント/機密キー診断）※UI非依存 |
| **CLI** | `tests/test_cli_main.py` | `cli/main.py`（AST非依存検査・引数解析・`GemioUI` モック注入による全コマンドディスパッチとコールバック連携） |
| **UI** | `tests/test_ui_renderables.py` | `cli/ui/renderables.py`（`format_prompt_header`、`classify_agent_log`、全 `build_*_panel` の純粋生成と描画結果検証） |
| **UI** | `tests/test_ui_prompts.py` | `cli/ui/prompts.py`（バッファクリア・キーバインド・APIキー入力・承認/質問/モデル選択プロンプト） |
| **UI** | `tests/test_ui_rich.py` | `cli/ui/rich_ui.py`（`RichConsoleUI` への `Console` 注入・スピナーライフサイクル・プロンプト時の自動一時停止・ログ分類出力） |

---

## 6. 将来の Textual 移行時の拡張ステップ（ロードマップ）

今回のリファクタリング完了後、Textualへ移行する際は以下の手順だけでスムーズに移行できます。

1. `src/gemio/cli/ui/textual_ui.py`（または `textual_app.py`）を新規作成し、`GemioUI` を継承した `TextualUI` を実装する。
2. 画面を「ヘッダーステータスバー（常時表示）」「メイン対話ログペイン（左または上）」「ツール呼び出し・ステータスログペイン（右または折りたたみ）」「マルチライン入力ボックス（下部固定・ログペインとは独立スクロール）」に分割する。
3. 各ペインへの描画には `renderables.py` の `build_*_panel` をそのまま渡し、ログ出力時は `classify_agent_log`（または分離後のエージェントからのイベント種別）に応じて対話ペインとツールログペインへ振り分ける。
4. `GemioCLI(args, ui_backend=TextualUI())` として注入することで、既存のコマンド解析やセキュリティガードレールを一切変更せずにTUI化が完了する。

---

## 6. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-12 | AI Agent | 実装完了 | 初版作成および実装完了。Textual移行およびAIChatAgent分離を見据えた `src/gemio/cli/ui/` パッケージの4層リファクタリングを実装。 |
