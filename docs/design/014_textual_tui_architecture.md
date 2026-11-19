# #014 Textual TUI 一本化・4層パッケージ再編・画面/操作体系 設計書

**作成日:** 2026年11月17日  
**最終更新日:** 2026年11月17日  
**ステータス:** 実装中（Phase 2 完了）  
**対象システム:** `pyproject.toml`, `requirements.txt`, `src/gemio/__init__.py`, `src/gemio/app/`, `src/gemio/tui/`, `src/gemio/cli/`, `tests/`  

### 【要約】
GemioのUI基盤を従来の `rich + prompt_toolkit`（ストリーム型コンソールUI）から **Textual（フルスクリーンTUI）へ完全一本化** し、新規設計で再構築します。パッケージ構成は既存の `core`・`project` を活かしつつ上位層を **`app`（UI非依存のアプリケーション制御層）・`tui`（Textual UI層）・`cli`（起動ブートストラップ層）** に分離する4層アーキテクチャへ再編します。あわせて、最小 `80×24` 端末・256色環境でも快適に動作するシングルカラム＋折りたたみ型タイムライン、ユーザー入力のプレーンテキスト表示とAI回答のMarkdown表示の分離、モーダル対話、およびアプリを終了させない安全なターン中断機構を定義します。

---

## 1. 背景と設計判断（Why）

### 1.1 背景と現行コンソールUIの課題
これまでの機能拡張（Web検索・作業前提資料、開発者モード、`/sandbox` 診断、3段階承認モード、`ask_user`、`/tokens`、Skills、動的ツールセット等）により、従来のストリーム型コンソールUI（`rich + prompt_toolkit`）では以下の構造的・UX上の限界が生じていました。

1. **対話文脈と作業ログの混在**: 本来の対話（質問と最終回答）の途中に大量のツール呼び出しログや警告パネルが単一ストリームで流れるため、文脈を追いにくい。
2. **入力中の過去ログ参照の困難さ**: 最下部の入力プロンプト滞在中、上方に流れた調査結果やコードを独立スクロールして見比べることができない。
3. **同期REPL構造の制約**: [`#012`](./012_ui_package_refactoring_for_textual.md) でUIパッケージの4層分離を行ったものの、`GemioCLI.run()` の Pull型 `while True` ループ、`__init__` 内の同期プロンプト/`sys.exit(1)`、およびコンソール前提の `GemioUI` 抽象メソッド群は、イベント駆動・非同期ループを持つ Textual のアーキテクチャと直接衝突する。
4. **AI実行中の中断挙動**: 現行ではAI思考・ツール実行中の `Ctrl+C` でGemioプロセス全体が即時終了してしまい、「誤送信や応答遅延時に現在のターンだけを止めて入力欄に戻る」ことができない。

### 1.2 主要な設計決定（Architecture Decisions）

1. **`rich + prompt_toolkit` を廃止し、Textual へ完全一本化する**:
   * 二重UIサポートを維持するための巨大な抽象アダプタ層（旧 `GemioUI`）を撤廃することで、ボイラープレートを排除し、Textual ネイティブのリアクティブ状態管理・折りたたみ（`Collapsible`）・モーダル（`ModalScreen`）・コマンドパレットを最大限に活用する。
2. **現状の `cli` を改修するのではなく、Textual に最適化した新規コードとして構築する**:
   * UI非依存で完成している **`gemio.core`（[`#013`](./013_core_package_refactoring.md)）および `gemio.project` はそのまま維持** し、上位層のみを `app` / `tui` / `cli` としてクリーンに新設する。
3. **ユーザー入力はプレーンテキストそのまま表示し、AI回答のみ Markdown レンダリングする**:
   * ユーザーが貼り付けたコード断片・ログ・アンダースコア（`__init__.py` 等）・記号類が意図せず装飾・消失しないよう、タイムライン上のユーザー入力カードは Markdown 解釈を行わず生テキストのまま表示する。

### 1.3 既存 `src/gemio/cli/` からの資産仕分け

| 区分 | 対象モジュール・要素 | 移行方針 |
| :--- | :--- | :--- |
| **維持（無変更）** | `src/gemio/core/`, `src/gemio/project/` | UI非依存の設計が確立されているため、既存コードをそのまま利用する（必要に応じて実行中ターンのキャンセル連携のみ拡張）。 |
| **ロジック移植** | セッション結線、承認モード遷移、`/clear`・`/search`・`/model` 制御、プロンプト定数、しきい値・案内文面 | UIコードを剥がし、新設する **`src/gemio/app/`**（UI非依存の制御・コマンド層）および `src/gemio/tui/` の各ウィジェット/モーダルへ移植する。 |
| **破棄（撤去）** | `cli/ui/base.py` (`GemioUI`), `cli/ui/prompts.py` (`prompt_toolkit`), `cli/ui/rich_ui.py` (`RichConsoleUI`), 旧同期REPLループ | Textual 一本化完了に伴い完全に削除し、レガシー互換レイヤは残さない。 |

---

## 2. 要求システム要件と環境互換性ガードレール

アプリケーションとしての導入障壁を下げ、ローカル開発端末からコンテナ・SSH環境まで幅広く動作させるため、以下のシステム要件とUI互換性基準を定めます。

### 2.1 明文化する要求システム要件

| 項目 | 要件・サポート範囲 |
| :--- | :--- |
| **Python バージョン** | **Python 3.11 以上**（3.11 / 3.12 / 3.13） |
| **対応 OS** | **Windows 10 (22H2以降) / 11**、**macOS**、**Linux**、**WSL2** |
| **対応ターミナル** | モダンな ANSI / 仮想ターミナル（ConPTY / XTerm互換）対応エミュレータ<br>• **Windows**: Windows Terminal（推奨）、VS Code / Cursor 統合ターミナル、WezTerm 等（※旧来のレガシー `conhost.exe` 単体は非サポート）<br>• **macOS**: 標準 Terminal.app、iTerm2、Ghostty、WezTerm、VS Code 統合ターミナル<br>• **Linux / リモート**: 標準デスクトップ端末、VS Code Remote (WSL / SSH / Dev Containers)、対話型 SSH セッション、`docker exec -it` |
| **推奨画面サイズ** | **最小 80列 × 24行 以上**（狭小時は自動折り返し・レイアウト調整） |
| **開発者モード追加要件** | `--dev` 指定時のみ Docker Desktop または Docker Engine |

### 2.2 環境差を吸収する6つの互換性ガードレール

1. **最小 `80×24` 基準のシングルカラム設計**: VS Code の下部統合ターミナル等でもコードや表が潰れないよう、常時マルチカラム分割は行わず、1カラム＋折りたたみ＋オーバーレイモーダルで構成する。
2. **256色・16色環境での高コントラスト維持**: TrueColor（24bit）専用の微細なグラデーションに依存せず、macOS 標準 `Terminal.app` や `tmux` 等の256色環境へ自動減色されても視認性が落ちない明確な配色・枠線スタイルを用いる。
3. **特殊フォント（Nerd Fonts）への非依存**: コンテナ内や標準フォント環境で文字化け（豆腐化）する私用領域グリフは一切使用せず、標準罫線・ASCII・基本Unicode記号のみで表現する。
4. **キーボード完全完結 ＋ マウス操作のハイブリッド**: マウスイベントが通らない環境でも全操作（フォーカス移動、スクロール、折りたたみ開閉、承認回答、コマンド実行）をキーボードのみで完結させつつ、マウス利用時はクリック・ホイール操作にも対応する。
5. **全端末共通の安全なキーバインドと OSC 52 コピー**: 端末間で差異が出やすい特殊な修飾キー組み合わせだけに頼らず、`Ctrl+J` や `Alt+Enter`（`Esc` ➔ `Enter`）等の確実なシーケンスを採用する。また、Textual の `app.copy_to_clipboard()`（OSC 52 対応）により SSH / Dev Container 越しでもホスト側クリップボードへのコピーを提供する。
6. **非TTY環境のプリフライト検知**: パイプ入力や `docker exec`（`-it` なし）等の非対話端末で実行された場合、TUI制御シーケンスを出力する前に検知し、平文の案内メッセージを標準エラー出力へ出して安全に終了する。

---

## 3. 依存関係設計（`pyproject.toml`）

アプリケーション利用者が `pip install gemio`（開発時は `pip install -e .`）の1コマンドだけで即座に利用開始できるよう、`textual` を標準依存に含めます。

```toml
dependencies = [
    "google-genai",
    "textual",
    "rich",
]

[project.optional-dependencies]
dev = [
    "pytest",
]
```

* **`prompt_toolkit` の削除**: 入力制御・マスク入力・キーバインドがすべて Textual に統合されるため、`prompt_toolkit` 依存を完全に除去します。
* **C拡張ビルド不要**: `textual`（および内包される `rich`）は Pure Python パッケージであるため、`python:3.12-slim` ベースの `Dockerfile` でも追加のシステムコンパイラなしで高速に導入できます。
* **ライブラリ利用時の軽量性維持**: `src/gemio/__init__.py` からは引き続き `core`・`project`（および必要に応じて UI 非依存の `app`）のみを公開し、`tui` / `cli` はインポートしません。

---

## 4. 新パッケージアーキテクチャ設計（4層構造）

### 4.1 ディレクトリ構成と単方向依存ルール

```text
src/gemio/
├── __init__.py            # ライブラリ公開ファサード（※ tui / cli 非依存）
├── __main__.py            # `python -m gemio` エントリ (gemio.cli.main:main)
│
├── core/                  # 【第1層: コアエンジン層】※既存維持
│   ├── exceptions.py, models.py, client.py, prompts.py, agent.py, search.py
│   ├── context/           # history.py, materials.py, compactor.py, session.py
│   ├── skills/            # loader.py, manager.py, builtin/
│   └── tools/             # builtin.py, registry.py, dynamic.py, loader.py
│
├── project/               # 【第2層: プロジェクト・サンドボックス層】※既存維持
│   ├── prompts.py
│   ├── base.py            # Project
│   └── developer.py       # DeveloperProject
│
├── app/                   # 【第3層: アプリケーション制御層】★新設（Textual非依存）
│   ├── __init__.py
│   ├── config.py          # AppConfig データクラス & 起動前パス検証ヘルパー
│   ├── state.py           # SessionSnapshot / SearchOutcome 等の不変UI状態モデル
│   ├── callbacks.py       # InteractionHandler Protocol (承認 / ask_user / 上限確認 / 警告通知)
│   ├── commands.py        # スラッシュコマンドパーサ (文字列 ➔ SlashCommand 構造体)
│   ├── prompts.py         # アプリ標準のWeb検索案内インストラクション定数
│   └── controller.py      # GemioController: コンポーネント結線・状態変更・対話/検索ユースケース
│
├── tui/                   # 【第4層A: Textual UI層】★新設（Textualネイティブ実装）
│   ├── __init__.py        # GemioApp を公開
│   ├── app.py             # GemioApp(textual.app.App): 画面構築・アクション・Workerオーケストレーション
│   ├── bridge.py          # ThreadModalBridge: Workerスレッド ⇄ ModalScreen 間の同期確認ブリッジ
│   ├── messages.py        # カスタム Message 定義 (AgentLogAppended, TurnCompleted, StatusChanged 等)
│   ├── styles/
│   │   └── app.tcss       # Textual CSS スタイルシート（256色互換・80x24レスポンシブ対応）
│   ├── screens/           # ModalScreen 群
│   │   ├── __init__.py
│   │   ├── approval.py    # ApprovalModal (y / n / a / k)
│   │   ├── ask_user.py    # AskUserModal (選択肢 / 自由入力 / Abort)
│   │   ├── iteration.py   # IterationLimitModal (追加ループ回数確認)
│   │   ├── sandbox.py     # SandboxStatusModal (DataTable による診断表示)
│   │   ├── tokens.py      # TokenUsageModal (コンテキスト健全性 & 累積トークン内訳表)
│   │   ├── model.py       # ModelSelectModal (プリセット選択 & カスタム指定)
│   │   ├── consent.py     # TrustConsentModal (トラストモード事前同意)
│   │   ├── api_key.py     # ApiKeyModal (in_container セキュアAPIキー入力)
│   │   └── help.py        # HelpModal (タブ切り替え式操作ガイド & コマンド一覧)
│   └── widgets/           # 常駐・タイムライン用ウィジェット群
│       ├── __init__.py
│       ├── header.py      # StatusHeader (リアルタイム状態バー)
│       ├── timeline.py    # ChatTimeline (スマートオートスクロール対応 VerticalScroll)
│       ├── turn.py        # TurnWidget, UserMessageCard(生テキスト), ActivityGroup, AssistantMessageCard(MD)
│       ├── search_card.py # SearchTurnWidget (クエリ生テキスト + 回答MD + 資料保持ステータス)
│       ├── welcome.py     # WelcomeBanner (コンパクト初期案内)
│       └── composer.py    # ActivityBar + PromptComposer (自動伸縮マルチライン入力欄)
│
└── cli/                   # 【第4層B: CLIブートストラップ層】★極薄化
    ├── __init__.py
    └── main.py            # parse_args(), プリフライト検証 (TTY/パス/Sandbox), GemioApp 起動・終了制御
```

#### 依存方向の鉄則
* **`cli` ➔ `tui` ➔ `app` ➔ `project` ➔ `core`** の単方向依存を厳守します。
* **`app` パッケージは `textual` を一切インポートしません**（AST検査テストで保証）。これにより、アプリケーションの全コマンド制御・状態遷移ロジックをUIなしで高速に単体テストでき、将来の単発ヘッドレス実行（`gemio -p "..."`）でもそのまま再利用できます。

---

### 4.2 第3層 `src/gemio/app/` の詳細仕様

1. **`config.py` (`AppConfig`)**:
   * CLI引数を正規化した設定オブジェクト（`project_path: Path`, `initial_model: str`, `dev_mode: bool`, `container_name: str | None`, `requested_edit: bool`, `requested_trust: bool`, `dynamic_tools: bool`）。
   * `validate_project_dir(raw_dir: str) -> Path`: `sys.exit()` を呼ばず、不正時は専用の `AppStartupError` 例外を送出します。
2. **`state.py` (`SessionSnapshot`, `SearchOutcome`)**:
   * UIヘッダーやモーダルが参照する不変データクラス。
   * `SessionSnapshot`: `project_name`, `project_path`, `is_dev_mode`, `execution_mode`, `container_name`, `approval_mode`, `current_model`, `context_tokens`, `cumulative_tokens`, `total_api_calls`, `materials_count`, `turn_count`, `active_skills_count` 等を一括保持します。
   * `SearchOutcome`: `SearchResult` に加え、`material_status: Literal["added", "duplicate", "failed"]` と `total_materials: int` を保持し、検索失敗時や重複検索時の誤表示を構造的に防止します。
3. **`commands.py` (`parse_slash_command`)**:
   * ユーザー入力文字列を解析し、通常プロンプトかスラッシュコマンドかを判定して型付きコマンドオブジェクト（例: `ClearCommand(clear_materials=True)`, `ModelCommand(target_model="argon" | None)`, `SearchCommand(query=..., new_session=..., clear=...)` 等）へ変換します。
4. **`controller.py` (`GemioController`)**:
   * `Project` / `DeveloperProject`, `ToolRegistry`, `ToolProvider`, `SkillManager`, `SystemPromptComposer`, `GenAIClient`, `ChatSession`, `ChatAgent`, `SearchAgent` を結線・保持します。
   * **主な型付き操作API（スラッシュコマンドとTUIコマンドパレットの双方が共通利用）**:
     * `get_snapshot() -> SessionSnapshot`
     * `needs_api_key_input() -> bool` / `set_api_key(api_key: str) -> None`
     * `set_approval_mode(mode: str) -> SessionSnapshot` / `toggle_edit_mode() -> bool`
     * `set_model(model_name_or_alias: str) -> str`
     * `clear_session(clear_materials: bool = False) -> dict[str, int]`
     * `inspect_sandbox() -> dict[str, Any]`
     * `get_token_usage() -> dict[str, int]`
     * `run_chat_turn(prompt: str, on_log: Callable[[str, str], None]) -> ChatTurnResult`
     * `run_search(query: str, new_session: bool = False, on_log: Callable[[str, str], None] | None = None) -> SearchOutcome`
     * `cancel_active_execution() -> None`: 実行中のサンドボックス子プロセス停止および進行中ターンのキャンセルフラグを設定します。
     * **Core先行実装APIへの接続メソッド**: `rewind_turns(steps: int = 1)`, `compact_context(keep_recent_turns: int = 2)`, `get_last_assistant_response() -> str | None`

---

### 4.3 第4層A `src/gemio/tui/` のスレッド境界とブリッジ設計

Textual のメインイベントループ（`asyncio`）を決してブロックしないため、以下のスレッド分担を徹底します。

```text
[Main Thread: Textual Event Loop]              [Worker Thread: @work(thread=True)]
  ・キー入力 / マウス操作受付                    ・controller.run_chat_turn(...)
  ・StatusHeader / ChatTimeline 描画更新   ◄───  ・controller.run_search(...)
  ・ModalScreen の push_screen / 結果返却  ◄───► ・controller.inspect_sandbox()
       ▲                                              │
       └──── app.call_from_thread + Event.wait() ─────┘
             (ThreadModalBridge: 承認 / ask_user / 上限確認)
```

* **`ThreadModalBridge` (`tui/bridge.py`)**:
  * `Project` の `approval_callback` / `ask_user_callback` および `ChatAgent` の `on_iteration_limit` はワーカースレッド上で同期呼び出しされます。
  * ブリッジ内部では `done_event = threading.Event()` を生成し、`app.call_from_thread(app.push_screen, ApprovalModal(...), callback)` でメインスレッドにモーダルを表示させ、ユーザーが選択してモーダルが閉じるまで**ワーカースレッド側のみを `done_event.wait()` で待機**させます。メインスレッドは一切ブロックされないため、モーダル内のスクロールやボタン操作・画面リサイズが滑らかに動作します。
  * モーダル待機中にユーザーがプロセス終了（`k` または `Ctrl+Q`）やターン強制中断を行った場合も、`done_event` を即座に解除してデッドロックを防止します。

---

### 4.4 第4層B `src/gemio/cli/` の起動・終了ライフサイクル

1. **引数解析 (`parse_args`)**: 既存の全起動オプション（`project_dir`, `-m/--model`, `--dev`, `--container`, `--edit`, `--trust/--auto`, `--dynamic-tools`）を完全維持します。
2. **プリフライト検証（TUI起動前）**:
   * 標準入出力が TTY でない場合は案内メッセージを出して終了コード `1` を返します。
   * `project_dir` の存在検証、および `--dev` 指定時の `DeveloperProject` 安全性検証（`SandboxSecurityError` 検査）を TUI 起動前に実行し、エラー時はコンソールへ明確なエラー内容を表示して終了コード `1` で即座に終了します。
3. **TUI起動後の初期モーダルチェーン（`GemioApp.on_mount`）**:
   * `in_container` かつ環境変数にAPIキーが未設定の場合は、起動直後に `ApiKeyModal` を表示します（キャンセル時はアプリをクリーン終了）。
   * `--trust` が指定されている場合は `TrustConsentModal` を表示し、同意時は `TRUST`、拒否時は `--edit` 指定有無に応じて `EDIT` または `NORMAL` モードへフォールバックしてトースト通知します。
   * `--dev` 起動時に危険・注意アラート（コンテナ内機密キー露出等）が検出された場合は、起動直後に通知バナーまたは `SandboxStatusModal` で確実に警告します。

---

## 5. Textual UI 画面レイアウト・ウィジェット詳細設計

### 5.1 メイン画面の4ゾーン構成

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ [Gemio] my-project │ DEV:gemio-sandbox │ EDIT │ flash-latest │ Ctx:14.2k M:2 │ ◄ ① StatusHeader
├──────────────────────────────────────────────────────────────────────────────┤
│ ┌─ You ─────────────────────────────────────────────────── 14:20:05 [Copy] ┐ │
│ │ def __init__(self): の実装を確認してテストを実行してください。           │ │ ◄ 生テキスト表示 (markup=False)
│ └──────────────────────────────────────────────────────────────────────────┘ │
│   ▸ Tools (3 calls: read_file, search_and_replace_file, run_sandbox_command) │ ◄ 折りたたみ (展開可)
│ ┌─ Gemio ───────────────────────────────────────────────── 14:20:18 [Copy] ┐ │ ◄ ② ChatTimeline
│ │ 修正とテストが完了しました。主な変更点は以下の通りです：                 │ │   (独立スクロール領域)
│ │  • `src/app.py` の初期化処理を整理                                       │ │ ◄ Markdownレンダリング表示
│ └──────────────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────────┤
│ * Gemioが思考・実行中... (Tool: run_sandbox_command)       [Esc: ターン中断] │ ◄ ③ ActivityBar
│ ┌─ Prompt (Ctrl+J / Alt+Enter: 送信 │ Enter: 改行) ────────────────────────┐ │
│ │ 続けて設計ドキュメントも更新してください。                               │ │ ◄ ③ PromptComposer
│ │                                                                   [Send] │ │   (3〜8行で自動伸縮)
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ^P コマンド  F1 ヘルプ  F2 モデル  F3 承認モード  Alt+Enter 送信  ^Q 終了    │ ◄ ④ Footer
└──────────────────────────────────────────────────────────────────────────────┘
```

1. **`StatusHeader`（上部固定・1〜2行）**:
   * `SessionSnapshot` を受け取り、プロジェクト名、動作モードバッジ（`STD` 緑 / `DEV:...` マゼンタ）、承認モードバッジ（`NORMAL` 緑 / `EDIT` 黄 / `TRUST` 赤）、現在モデル名、コンテキストトークン量（`<50k` 緑 / `>=50k` 黄 / `>=150k` 赤）＋保持資料件数（`M:N`）を常時表示します。
   * マウス操作時は各バッジ（承認モード・モデル名・トークン表示）のクリックで対応する切替・診断モーダルを直接開けます。
2. **`ChatTimeline`（中央可変・`VerticalScroll`）**:
   * ターン単位のウィジェットを縦方向にマウントします。
   * **スマートオートスクロール**: タイムラインが最下部付近にあるときは新規ログ追加時に自動追従し、ユーザーが過去ログを読むために上へスクロールしている最中は自動スクロールを一時停止します。
3. **`ActivityBar` + `PromptComposer`（下部固定）**:
   * `ActivityBar`: アイドル時は入力ヒントを表示し、AI実行中・検索中はスピナー・現在実行中のツール名・経過秒数・`[Esc: ターン中断]` ガイドを表示します。
   * `PromptComposer`: Textual `TextArea` ベースの複数行エディタ（内容に応じて3行〜最大8行まで自動リサイズ）と `[Send]` ボタン。AI実行中も次の指示の下書き入力が可能で、送信アクションのみ実行完了までロックします。
4. **`Footer`（最下部1行固定）**:
   * 主要キーバインドを常時表示し、クリック実行にも対応します。

---

### 5.2 タイムライン内ウィジェットとテキスト描画仕様（重要）

#### (1) `TurnWidget`（通常対話ターン）
1回のユーザープロンプト送信からAIの最終応答までを1つのコンテナにまとめ、内部を以下の3要素で構成します。

* **`UserMessageCard`（ユーザー入力プロンプト：プレーンテキストそのまま表示）**:
  * **仕様**: **Markdown レンダリングおよび Rich マークアップ解釈を一切行いません**（`Static(prompt_text, markup=False)` を使用）。
  * **効果**: `# 見出し`、`*アスタリスク*`、`__init__.py` 等のアンダースコア、`<tag>`、`[1]` 等の角括弧、インデント付きコード断片をそのまま貼り付けても、文字の消失や意図しない装飾が一切起きず、入力した文字列が1文字単位で正確にそのまま表示されます（ウィンドウ幅でのソフトラップのみ適用）。
  * ヘッダー右端に送信時刻と `[Copy]` ボタン（生テキストをコピー）を配置します。
* **`ActivityGroup`（ツール呼び出し・思考ログ・警告の折りたたみ）**:
  * ツール実行・思考テキスト（Thinking）・リトライ警告が発生したターンのみ生成されます。
  * デフォルトは折りたたみ状態（`▸ Tools (N calls: ツール名...)`）とし、実行中は最新ツール名と回数がリアルタイム更新されます。展開（`Enter` / `Space` / クリック）すると、各ツール呼び出し（関数名・引数の色分け表示）、思考テキスト、リトライ警告が時系列で確認できます。
* **`AssistantMessageCard`（AI回答：Markdown レンダリング表示）**:
  * **仕様**: Textual の **`Markdown` ウィジェット** を使用し、見出し・箇条書き・太字・テーブル・コードブロック（シンタックスハイライト付き）をリッチに描画します。
  * 通常完了時はアクセント枠（青/緑系）、Abortや通信エラー中断時は警告枠（赤/黄系）で視覚的に区別します。ヘッダー右端に完了時刻と `[Copy]` ボタン（Markdown原文をコピー）を配置します。

#### (2) `SearchTurnWidget`（`/search` Web検索カード）
* **検索クエリ表示部**: `UserMessageCard` と同様、`markup=False` のプレーンテキストでそのまま表示します。
* **検索回答表示部**: `Markdown` ウィジェットでレンダリングし、下部に重複排除済みの出典タイトル・URL一覧を表示します。
* **前提資料ステータスフッター**: `SearchOutcome.material_status` に基づき、以下の3状態を正確に描き分けます：
  * `"added"`: `作業前提資料 [第N弾] としてセッションに保持しました`
  * `"duplicate"`: `同一内容の資料がすでに保持されているため新規追加をスキップしました（現在 N 件）`
  * `"failed"`: エラースタイルで表示し、前提資料には追加されなかった旨を明示します。

#### (3) トースト通知（`app.notify`）の活用
* `/edit` 切替、`/model` 変更、クリップボードコピー完了などの軽量な状態変更通知は、タイムラインに巨大なパネルを積まず `StatusHeader` の即時更新＋右上の自動消去トースト通知で完結させ、対話ログの見通しをクリーンに保ちます。

---

### 5.3 モーダル画面（`ModalScreen`）一覧

すべてのモーダルはキーボード操作のみで完結し、`Esc` キーで安全にキャンセル/クローズできます。

| モーダルクラス | 役割・主なUI要素とキー操作 |
| :--- | :--- |
| **`ApprovalModal`** | 変更系ツール・シェルコマンドの Human-in-the-loop 承認確認。操作内容のスクロールビューと4つのアクションを提供。<br>• キー1発選択: **`y`** (許可) / **`n`** (拒否) / **`a`** (作業強制中止 Abort) / **`k`** (プロセス即時終了 Kill) |
| **`AskUserModal`** | `ask_user` ツールによるAIからの質問・相談。`context` と `question` を `Markdown` 表示。<br>• 選択肢あり: `OptionList`（上下キーまたは **`1`〜`9` 数字キー** で選択）＋「`0`: その他（自由記述入力欄を展開）」＋「`a`: 作業強制中止」<br>• 選択肢なし: マルチライン回答入力欄 ＋ 送信ボタン ＋ Abortボタン |
| **`IterationLimitModal`** | 連続ツール呼び出し上限（デフォルト20回）到達時の確認。追加回数 `Input`（初期値 `10`）と `[継続 (Enter)]` / `[中断 (Esc)]` ボタン。 |
| **`SandboxStatusModal`** | `/sandbox` (`/status`) 診断画面。コンテナ基本設定サマリ、**`DataTable` によるワークスペース内ファイル・マウント保護状態一覧**（`🟢` 保護済 / `🔵` 作業対象 `rw` / `⚪` 読取専用 `ro` / `🔴` 露出警告）、およびセキュリティアラート一覧を表示。 |
| **`TokenUsageModal`** | `/tokens` (`/usage`) 診断画面。直近コンテキスト量の3段階健全性診断、ソース別・項目別の累積トークン `DataTable`、およびその場で実行できる `[履歴クリア (/clear)]` ボタンを配置。 |
| **`ModelSelectModal`** | `/model`（引数なし）または `F2` で起動。`MODEL_PRESETS` の選択リスト（現在モデルを強調）と、任意のモデル名/エイリアス入力欄を提供。 |
| **`TrustConsentModal`** | `--trust` 起動時および `/trust on` 実行時の注意事項表示と同意確認。誤操作防止のため初期フォーカスは `[キャンセル]` 側に配置。 |
| **`ApiKeyModal`** | `in_container` かつ環境変数APIキー未設定時のセキュア入力画面。OS別のクリップボードコピーコマンド案内と `Input(password=True)` マスク入力欄。 |
| **`HelpModal`** | `/help` または `F1` で起動。`TabbedContent` により「キー操作一覧」「スラッシュコマンド一覧」「承認モード・安全仕様」を切り替えて閲覧可能。 |

---

## 6. 操作体系・キーバインド・安全なターン中断機構

### 6.1 キーバインド仕様

| 操作目的 | キーバインド | 詳細挙動 |
| :--- | :--- | :--- |
| **プロンプト送信** | **`Ctrl+J`** または **`Alt+Enter`** (`Esc` ➔ `Enter`) | 入力欄のテキストを送信（スラッシュコマンドまたは通常対話）。全ターミナルで確実に識別でき、かつ現行の `Esc` ➔ `Enter` 操作とも完全互換。 |
| **改行** | **`Enter`** | `PromptComposer` 内で改行を挿入。 |
| **入力欄クリア** | **`Alt+Backspace`** (`Esc` ➔ `Backspace`) | 入力中のテキストを Undo（`Ctrl+Z`）可能な状態で全消去。 |
| **タイムラインスクロール** | **`PageUp`** / **`PageDown`** | 入力欄にフォーカスしたままでも対話タイムラインを上下にスクロール。 |
| **フォーカス移動** | **`Tab`** / **`Shift+Tab`** | 入力欄 ⇄ タイムライン内の各カード・折りたたみ（`ActivityGroup`）・`[Copy]` ボタン間を移動。 |
| **実行中ターンの中断** | **`Esc`** *(AI思考・検索実行中)* | **Gemioアプリ自体は終了せず**、現在実行中のターンと稼働中のサンドボックス子プロセスのみを停止し、ロール整合性を保ったまま入力待ちへ復帰。 |
| **直近回答のコピー** | **`Ctrl+Y`** | 直近ターンのAI回答テキストをクリップボードへコピー（OSC 52 対応）。 |
| **コマンドパレット** | **`Ctrl+P`** | 全機能（モデル切替、承認モード変更、`/sandbox`、`/tokens`、`/clear`、コンテキスト圧縮、巻き戻し等）を検索実行。 |
| **クイックモーダル** | **`F1`**: ヘルプ / **`F2`**: モデル選択 / **`F3`**: 承認モード切替 | 主要機能のワンタッチ呼び出し。 |
| **アプリケーション終了** | **`Ctrl+Q`** *(または `/exit`, `exit`, `quit`)* | 稼働中の子プロセスがあれば終了させた上で、`GenAIClient` をクローズしてTUIを正常終了。 |

### 6.2 アプリを終了させない「安全なターン中断」の内部設計
従来の `Ctrl+C` 即死挙動を改善し、TUI上の `Esc`（または中断ボタン）で実行中ターンだけを安全に止めるため、以下の2段制御を行います。
1. **ツール実行中・承認待ち中の即時停止**:
   * `GemioController.cancel_active_execution()` がキャンセルイベント（`threading.Event`）をセットすると同時に、`project.terminate_active_process()` を呼び出して実行中の `docker exec` / サンドボックス子プロセスを即座に `SIGKILL` で停止します。
   * `ChatAgent` のツール実行ループ先頭・完了後にキャンセルイベントを検査し、検知時は `AgentExecutionAborted("ユーザーによって現在のターンが中断されました。")` と同等のクリーンアップ（未実行 `function_call` へのスキップ応答補完＋中断 `ChatTurn` 確定）を行って安全にループを閉じます。
2. **API応答待機中のUI即時解放**:
   * ネットワーク遅延等で `generate_content` が応答待ちの間でも、キャンセル要求が出された時点で `ChatAgent` 側の結果コミットを抑止し、ワーカーキャンセル後にロール整合性を壊すことなく次のプロンプト入力を受け付け可能にします。

### 6.3 対応スラッシュコマンド一覧（コマンドパレット完全連動）
従来のコマンド互換性をすべて維持しつつ、Core実装済み機能への導線を統合します。
* `/help` (`/h`, `help`, `/info`) ➔ `HelpModal` 表示
* `/model [name]` ➔ 引数ありなら即時切替＋トースト通知、引数なしなら `ModelSelectModal` 表示
* `/edit [on|off]` ➔ エディットモード切替＋ヘッダー更新＋トースト通知
* `/trust [on|off]` (`/auto`) ➔ 有効化時は `TrustConsentModal` 確認後に切替
* `/sandbox` (`/status`, `/container`) ➔ Workerで診断取得後 `SandboxStatusModal` 表示
* `/tokens` (`/usage`, `/token`) ➔ `TokenUsageModal` 表示
* `/clear [-a|--all]` (`/reset`, `/new`) ➔ セッション履歴・トークン・有効スキル・動的ツール状態をリセットしタイムラインを初期化
* `/search <query>` (`/s`, `-n/--new`, `-c/--clear`) ➔ Workerで `SearchAgent` 実行・`SearchTurnWidget` 追加
* `/copy` *(新規)* ➔ 直近のAI回答をクリップボードへコピー
* `/compact` *(新規・Core接続)* ➔ `ContextCompactor` で古い対話履歴を構造化要約へ圧縮
* `/rewind` *(新規・Core接続)* ➔ 直近1ターンを `ChatHistory.rewind(1)` で巻き戻し、タイムライン末尾の `TurnWidget` を除去
* `/exit` (`exit`, `quit`) ➔ アプリケーション終了

---

## 7. 段階的実装ロードマップとテスト計画

レガシーコードとの混在期間を最小化し、各段階でテストを通しながら安全に移行するため、以下の4フェーズで実装を進めます。

### Phase 1: 依存関係更新と第3層 `src/gemio/app/` の構築
* `pyproject.toml` / `requirements.txt` に `textual` を追加（移行完了時に `prompt_toolkit` を除去）。
* `src/gemio/app/`（`config.py`, `state.py`, `callbacks.py`, `commands.py`, `prompts.py`, `controller.py`）およびターン中断用のキャンセル機構を実装。
* **単体テスト (`tests/test_app_controller_and_commands.py`)**: Textual 非依存の純粋な Python テストとして、全スラッシュコマンド解析、状態スナップショット、`/search` の3状態（追加・重複・失敗）判定、承認モード遷移、および `app` 層が `textual` をインポートしていないこと（AST検査）を検証。

### Phase 2: 第4層A `src/gemio/tui/` 基本骨格・タイムライン・ブリッジの実装
* `StatusHeader`, `ChatTimeline`, `TurnWidget`（**`UserMessageCard` の `markup=False` プレーンテキスト表示**、`ActivityGroup` 折りたたみ、`AssistantMessageCard` の `Markdown` 表示）、`SearchTurnWidget`, `ActivityBar`, `PromptComposer`, および `ThreadModalBridge` と `GemioApp` 本体を実装。

### Phase 3: 全 `ModalScreen`・コマンドパレット・ショートカットの実装
* 9種のモーダル画面（`ApprovalModal`, `AskUserModal`, `IterationLimitModal`, `SandboxStatusModal`, `TokenUsageModal`, `ModelSelectModal`, `TrustConsentModal`, `ApiKeyModal`, `HelpModal`）と `Ctrl+P` コマンドパレットプロバイダを実装。

### Phase 4: `src/gemio/cli/` 刷新・旧UI撤去・ヘッドレスTUIテスト・ドキュメント同期
* `src/gemio/cli/main.py` をプリフライト検証＋`GemioApp` 起動の薄いブートストラップへ刷新し、旧 `src/gemio/cli/ui/` および旧UIテスト（`test_ui_prompts.py`, `test_ui_renderables.py`, `test_ui_rich.py`）を撤去。
* **TUI自動テスト (`tests/test_tui_app.py`, `tests/test_tui_modals.py`)**: Textual 公式の `async with app.run_test(size=(80, 24)) as pilot:` を用い、最小画面サイズでのレイアウト破綻がないこと、ユーザー入力が生テキストのまま保持表示されること、キー操作（`Ctrl+J` 送信、承認 `y/n/a`、`ask_user` 選択、`Esc` ターン中断）が期待通り動作することを自動検証。
* 実装完了後、`manuals/01_USER_MANUAL.md`、`manuals/07_ARCHITECTURE_REFERENCE.md`、`manuals/08_CONTRIBUTING_GUIDE.md`、`manuals/09_MANUAL_TEST_GUIDELINE.md`、`docs/README.md`（`#012` の置換ステータス更新含む）、および `docs/PROJECT_SUMMARY.md` を最新仕様へ同期。

---

## 8. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-17 | AI Agent | 設計完了（実装準備中） | 初版作成。Textual一本化方針、要求システム要件と6つの環境互換性ガードレール、4層パッケージ構造（`core`/`project`/`app`/`tui`/`cli`）、ユーザー生テキスト表示＋AI Markdown表示のタイムライン・モーダルUI設計、および実装ロードマップを策定。 |
| 2026-11-17 | AI Agent | 実装中（Phase 1 完了） | Phase 1 実装完了。`pyproject.toml` への `textual` / `pytest-asyncio` 設定、`ChatAgent` / `SearchAgent` の安全なターン中断（`cancel_event`）対応、第3層 `src/gemio/app/`（`config`, `state`, `callbacks`, `commands`, `prompts`, `controller`）および単体テストを実装。 |
| 2026-11-17 | AI Agent | 実装中（Phase 2 完了） | Phase 2 実装完了。第4層A `src/gemio/tui/` の基本骨格（`GemioApp`、`ThreadModalBridge`、`StatusHeader`、`ChatTimeline`、`TurnWidget`［ユーザー生テキスト表示＋AI Markdown描画＋`ActivityGroup` 折りたたみ］、`SearchTurnWidget`、`ActivityBar`、`PromptComposer`）および `80×24` ヘッドレステストを実装。 |
