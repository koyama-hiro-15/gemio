# CLIモジュール構造整理（UI分離 & GemioCLIクラス化）設計書

**作成日:** 2026年11月07日  
**最終更新日:** 2026年11月12日  
**ステータス:** 実装完了  
**対象システム:** `src/gemio/cli/main.py`, `src/gemio/cli/ui/` (`base.py`, `renderables.py`, `prompts.py`, `rich_ui.py`, `__init__.py`), `src/gemio/cli/__init__.py`, `tests/test_cli_prompt.py`, `tests/test_model_selection.py`  

### 【要約】
機能追加に伴い肥大化した `src/gemio/cli/main.py` の可読性・保守性・テスト容易性を向上させるため、UI表示・対話入力層（`src/gemio/cli/ui/` パッケージ）とセッション制御層（`src/gemio/cli/main.py` の `GemioCLI` クラス）に分離します。さらに、`prompt_toolkit` および `rich` への外部ライブラリ依存をすべて `src/gemio/cli/ui/` に完全集約し、`GemioUI` 抽象インターフェースと `RichConsoleUI` 具象クラスによって将来のTextual移行にも対応可能な構成とします（詳細は `docs/design/design_ui_package_refactoring_for_textual.md` も参照）。

---

## 1. 背景と課題

これまで段階的に機能拡張（モデル切替、Web検索・作業前提資料、エラー自動回復、開発者モード、サンドボックス診断、トラストモード、エディットモード、`ask_user` ツール等）を重ねた結果、`src/gemio/cli/main.py` において以下の構造的課題が生じていました。

1. **`main()` 関数の肥大化（約440行）**:
   引数パース、ディレクトリ検証、承認モード初期化、3つのUIコールバック関数定義、`Project`/`AIChatAgent` の生成、および多数のスラッシュコマンド分岐を含むREPLループが単一の `main()` 関数内に記述されていた。
2. **クロージャによる状態共有の複雑化**:
   処理中スピナー（`active_status`）の一時停止・再開を行うため、`cli_approval_callback`、`cli_iteration_limit_callback`、`cli_ask_user_callback` の3関数が `main()` のローカル変数を参照するネスト関数になっており、関数の切り出しや単体テストが困難であった。
3. **「画面デザイン・対話入力（`rich` / `prompt_toolkit`）」と「コマンド制御ロジック」の混在**:
   ヘルプパネルやサンドボックス診断テーブル等のレイアウト定義・Richマークアップタグ・`PromptSession` のキーバインド定義と、モード切替やAPI呼び出しの制御フローが同一ファイルに混在し、コードの見通しを悪化させていた。

---

## 2. アーキテクチャ設計（UI依存完全集約 & クラス化構成）

ファイルが細分化しすぎて追跡しづらくなる事態を避けつつ、責務の境界を明確にするため、**「① `prompt_toolkit` と `rich` への依存を完全集約したUI層 (`ui.py`)」** と **「② ステートフルなCLIセッションの制御層 (`GemioCLI` in `main.py`)」** の2モジュール構成を採用します。

```text
src/gemio/cli/
├── __init__.py        # パッケージ公開定義 (main, GemioCLI, GemioUI, RichConsoleUI, parse_args 等)
├── main.py            # 【制御層】引数解析・GemioCLIクラス（状態管理・コールバック・コマンド処理）・main()
│                      #          ※ prompt_toolkit / rich を直接インポートしない
└── ui/                # 【UI層パッケージ】prompt_toolkitとrichへの全依存を集約
    ├── __init__.py    # 互換・公開ファサード
    ├── base.py        # GemioUI 抽象基底クラス・AgentLogCategory 定義
    ├── renderables.py # Rich Renderable（Panel/Table等）の純粋生成関数群
    ├── prompts.py     # prompt_toolkit / rich.prompt による対話入力関数群
    └── rich_ui.py     # RichConsoleUI 具象クラスおよび出力ラッパー関数群
```

### 2.1 `src/gemio/cli/ui.py`（UI表示・対話プロンプト層）

`prompt_toolkit` および `rich` への依存をすべてこのモジュールに集約します。引数として渡されたデータを整形してコンソールへ出力する関数、スピナー生成関数、およびユーザーからの入力を受け取るプロンプト関数を担当します。

| カテゴリ | 関数名 | 責務 |
| :--- | :--- | :--- |
| **共通コンソール** | `console` | CLI全体で共有する `rich.console.Console` インスタンス |
| **プロンプト制御** | `clear_prompt_buffer(buffer)` | 入力欄テキストをUndo可能な状態で全消去 |
| **プロンプト制御** | `create_prompt_session()` | `Esc+Enter`（送信）・`Esc+Backspace`（クリア）を備えた `PromptSession` を生成 |
| **プロンプト制御** | `format_prompt_header(project, token_usage)` | プロンプト入力欄上部のHTMLステータスヘッダー文字列を生成 |
| **プロンプト制御** | `prompt_user_input(session, project, token_usage)` | HTMLヘッダー付きマルチラインプロンプトを表示し入力を取得 |
| **スピナー・装飾** | `format_dev_mode_label(project)` | ウェルカムバナー等に表示する動作モードの装飾ラベルを生成 |
| **スピナー・装飾** | `create_thinking_spinner()` | AI思考・ツール実行中のスピナー（`Status`）を生成 |
| **スピナー・装飾** | `create_search_spinner(is_subsequent)` | Web検索中のスピナー（`Status`）を生成 |
| **単発メッセージ** | `print_cli_error(message)` | 標準エラー出力に赤字のエラーメッセージを表示 |
| **単発メッセージ** | `print_dim_message(message)` | 補足・キャンセル等の控えめなメッセージを表示 |
| **単発メッセージ** | `print_exit_message()` | CLI終了時の挨拶メッセージを表示 |
| **単発メッセージ** | `print_chat_divider()` | 対話ターン終了時の区切り線を表示 |
| **単発メッセージ** | `print_initial_approval_mode_notice(...)` | 起動時の初期承認モード決定に伴う通知メッセージを表示 |
| **パネル表示** | `print_welcome_banner(...)` | 起動時のウェルカムバナーを表示 |
| **パネル表示** | `print_help_panel()` | `/help` 操作ガイド・コマンド一覧パネルを表示 |
| **パネル表示** | `print_sandbox_status_panel(project)` | `/sandbox` コンテナ設定・マウント保護状態診断パネルを表示 |
| **パネル表示** | `print_token_usage_panel(token_usage)` | `/tokens` コンテキスト量・累積トークン消費量パネルを表示 |
| **パネル表示** | `print_session_reset_panel(...)` | `/clear` 対話セッションリセット完了通知パネルを表示 |
| **パネル表示** | `print_edit_mode_changed_panel(enabled)` | `/edit` エディットモード切替通知パネルを表示 |
| **パネル表示** | `print_trust_mode_changed_panel(enabled)` | `/trust` トラストモード切替通知パネルを表示 |
| **パネル表示** | `print_model_changed_panel(model_name)` | `/model` AIモデル切替通知パネルを表示 |
| **パネル表示** | `print_search_materials_cleared_panel(count)` | `/search --clear` 参照資料クリア完了パネルを表示 |
| **パネル表示** | `print_search_help_panel()` | `/search` コマンドの使い方・オプション案内パネルを表示 |
| **パネル表示** | `print_search_response_panel(...)` | Web検索の回答・参照元ハイパーリンク・保持案内を表示 |
| **パネル表示** | `print_agent_log_panel(log_message)` | エージェントの思考・ツール実行ログをMarkdownパネル表示 |
| **エラーパネル** | `print_sandbox_security_error_panel(error)` | サンドボックス安全性検証エラーパネルを表示 |
| **エラーパネル** | `print_search_error_panel(error)` | Web検索エラーパネルを表示 |
| **エラーパネル** | `print_unexpected_error_panel(error)` | 通常プロンプト実行中の予期せぬエラーパネルを表示 |
| **対話入力** | `request_trust_mode_consent()` | トラストモード有効化前の注意事項パネル表示とYes/No同意確認 |
| **対話入力** | `prompt_api_key_for_container(...)` | コンテナ内起動時のAPIキー案内パネル表示とパスワードマスク入力 |
| **対話入力** | `prompt_approval_choice(action_description)` | Human-in-the-loop 承認要求パネル表示と `y/n/a/k` 選択入力 |
| **対話入力** | `prompt_iteration_limit_continue()` | ツール連続呼び出し上限到達時の継続確認と追加ループ回数入力 |
| **対話入力** | `prompt_ask_user_response(question, options, context)` | AIからの質問パネル表示と選択肢番号・自由記述・Abort入力 |
| **対話入力** | `prompt_model_selection(current_model)` | `/model` プリセット一覧パネル表示とモデル選択入力 |

### 2.2 `src/gemio/cli/main.py`（セッション制御・コマンドディスパッチ層）

`prompt_toolkit` や `rich` を一切インポートせず、CLIの起動から終了までのライフサイクルと状態を管理する `GemioCLI` クラスを中心に構成します。画面出力・プロンプト入力はすべて `gemio.cli.ui` モジュール経由で呼び出します。

#### `GemioCLI` クラスの構成
* **保持する主な属性**:
  * `args: argparse.Namespace`: 解析済みコマンドライン引数
  * `project_path: Path`: 検証済みの対象プロジェクト絶対パス
  * `current_model: str`: 現在選択されているAIモデル名
  * `active_status: Any`: 現在表示中のスピナー（未表示時は `None`）
  * `project: Project | DeveloperProject`: プロジェクト管理インスタンス
  * `dev_mode_label: str`: ウェルカムバナー表示用の動作モードラベル
  * `agent: AIChatAgent`: AI対話エージェントインスタンス

* **スピナー一時停止コンテキストマネージャ (`_pause_status`)**:
  ```python
  @contextlib.contextmanager
  def _pause_status(self) -> Iterator[None]:
      if self.active_status:
          self.active_status.stop()
      try:
          yield
      finally:
          if self.active_status:
              self.active_status.start()
  ```
  これにより、各コールバックメソッド（`on_approval`, `on_iteration_limit`, `on_ask_user`）は `with self._pause_status():` で囲んで `ui.py` のプロンプト関数を呼び出すだけの簡潔な構造になります。

* **コマンドハンドラメソッド**:
  * `dispatch_input(user_input: str) -> bool`: 入力文字列を解析し、終了コマンドなら `False`、スラッシュコマンドまたは通常プロンプトなら対応メソッドを実行して `True` を返す。
  * `handle_tokens_command() -> None`: `/tokens`・`/usage` の表示処理。
  * `handle_clear_command(user_input: str) -> None`: `/clear`・`/reset`・`/new` のセッションリセット処理。
  * `handle_edit_command(user_input: str) -> None`: `/edit`（`on`/`off`）の切り替え処理。
  * `handle_trust_command(user_input: str) -> None`: `/trust`・`/auto`（`on`/`off`）の切り替え処理。
  * `handle_model_command(user_input: str) -> None`: `/model` によるモデル直接指定または対話選択処理。
  * `handle_search_command(user_input: str) -> None`: `/search`・`/s`（`--clear`, `-n/--new`, 検索実行）の処理。
  * `run_chat_prompt(user_input: str) -> None`: 通常プロンプトのAI推論・ツール実行およびエラーハンドリング。

* **エントリポイント (`main`)**:
  ```python
  def main(argv: list[str] | None = None) -> None:
      args = parse_args(argv)
      cli = GemioCLI(args)
      cli.run()
  ```

---

## 3. 単体テストにおける検証

1. **`main.py` の非依存性検証 (`test_main_py_has_no_direct_prompt_toolkit_or_rich_imports`)**:
   * `ast.parse` により `src/gemio/cli/main.py` の全 `import` / `from ... import` 文を静的に検査し、`prompt_toolkit` および `rich` が一切インポートされていないことを自動テストで保証します。
2. **UI層と制御層の独立テスト**:
   * `tests/test_cli_prompt.py` および `tests/test_model_selection.py` にて、`ui.py` のプロンプト・ヘッダー生成・バッファクリア等と、`main.py` の `GemioCLI` コマンドディスパッチ・コールバック制御をそれぞれ独立して検証します。

---

## 4. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-07 | AI Agent | 実装完了 | 初版作成。`ui.py` 分離と `GemioCLI` クラス導入によるハイブリッド構造整理の設計を策定。 |
| 2026-11-11 | AI Agent | 実装完了 | `prompt_toolkit` と `rich` への全依存（`PromptSession`・スピナー生成・各種通知/エラーパネル等）を `ui.py` に完全集約し、`main.py` から両ライブラリの直接インポートを排除。 |
| 2026-11-12 | AI Agent | 実装完了 | Textual移行およびエージェント分離を見据え、`ui.py` を `ui/` サブパッケージ（`base.py`, `renderables.py`, `prompts.py`, `rich_ui.py`, `__init__.py`）へ分割。 |
