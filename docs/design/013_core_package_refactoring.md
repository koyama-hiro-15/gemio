# #013 Coreパッケージ構造リファクタリング 設計書

**作成日:** 2026年11月12日  
**最終更新日:** 2026年11月17日  
**ステータス:** 実装完了（現行）  
**対象システム:** `src/gemio/core/`（全モジュールおよび `context/`, `skills/`, `tools/` サブパッケージ）, `src/gemio/__init__.py`, `src/gemio/project/base.py`, `src/gemio/cli/main.py`, `src/gemio/cli/ui/`, `tests/`  

### 【要約】
現在 `AIChatAgent` 単一クラスに集中している責務（通常対話ループ、Web検索、API通信・トークン集計、対話履歴、作業前提資料、静的プロンプト・ツール管理）を分割し、今後の機能拡張（対話の巻き戻し、クリップボード用抽出、前提資料の個別削除・ファイル読み込み追加、コンテキスト要約圧縮、Skills導入、および実験的な動的ツールセット機能）をスムーズに実装できるモジュラーな `src/gemio/core` パッケージ構造を定義します。あわせて、ハッシュ値による資料同一性管理、Skills/Toolsディレクトリの固定化、および前提資料追加権限の制御によってローカル環境情報の露出と冗長なパス処理を排除します。後方互換用の旧クラス名（`AIChatAgent` 等）は残さず、周辺パッケージとテストも含めて新構造へ完全に刷新します。

> **💡 現行仕様に関する補足:** `src/gemio/core/` のモジュラー構成（`GenAIClient`, `TokenUsageTracker`, `ChatAgent`, `SearchAgent`, `context/`, `skills/`, `tools/`）および `.gemio/` 保護仕様は現行コードと一致しています（なお、`v0.1.1` にて上位層固有のシステムプロンプト定数が `src/gemio/project/prompts.py`・`src/gemio/cli/prompts.py` へ分離され、`SystemPromptComposer` の `dev_mode` 引数は `extra_sections` 方式へ整理済みです。詳細は [`manuals/07_ARCHITECTURE_REFERENCE.md`](../../manuals/07_ARCHITECTURE_REFERENCE.md) を参照してください）。

---

## 1. 背景と解決すべき課題（As-Is）

これまでの段階的な機能拡充により、現行の `src/gemio/core/agent.py`（`AIChatAgent`）には以下の6つの構造的課題が生じています。

1. **通常対話処理とWeb検索処理の密結合**:
   ローカルツールを実行する Function Calling ループと、Google Search Grounding による Web 調査（`search_history` 管理・出典抽出）が同一クラス内に同居しており、通常プロンプト実行時に暗黙的に検索履歴をクリアするなど状態管理が絡み合っている。
2. **API通信基盤とトークン集計ロジックの未分離**:
   `genai.Client` の初期化、503/429 エラー時の指数バックオフリトライ、および `usage_metadata` のトークン集計が `AIChatAgent` の内部メソッドとして実装されているため、通常対話・Web検索・コンテキスト要約などの複数コンポーネント間で通信基盤や累積トークン統計を共有できない。
3. **フラットな履歴リストによるコンテキスト操作の困難さ**:
   `chat_history` が生の `list[types.Content]` として管理されているため、「1回のユーザー指示から複数回のツール呼び出しを経て最終回答に至るまで」の **ターン（Turn）境界** が存在しない。その結果、Gemini API のロール交互規約や `function_call` / `function_response` ペア整合性を壊さずに「直前の対話を巻き戻す」ことや、「入力プロンプトと最終回答だけを綺麗に抽出してコピー・再表示する」ことが困難になっている。
4. **検索結果専用に固定された作業前提資料構造**:
   `search_materials` が Web 検索結果専用の辞書リストとして `AIChatAgent` 内部に閉じ込められており、ユーザーがローカルファイルを読み込んで作業前提資料に追加する機能や、不要になった特定の資料だけを個別削除する機能へ拡張しづらい。
5. **システムプロンプトとツールリストの静的固定**:
   初期化時に `system_instruction` と `tools` を固定構築しているため、対話の途中で必要な **Skills（専門手順書）** をオンデマンドに読み込んでシステムプロンプトへ注入したり、AIからの要求に応じて **ツールセットを動的に組み合わせたりする実験的機能** を組み込めない（また未知・未有効ツール呼び出し時に `StopIteration` が発生するリスクがある）。
6. **不要な外部SDK依存の伝播**:
   共通例外 `AgentExecutionAborted` やモデルプリセット定数が `agent.py` に置かれているため、`project` 層や `cli/ui` 層がそれらを参照するだけで `google-genai` SDK までインポートされる構造になっている。

---

## 2. 基本設計方針とセキュリティ・プライバシー原則

### 2.1 アーキテクチャ基本方針
1. **4つの関心事の明確な分離**:
   * **通信・計測基盤**（`client.py`, `models.py`, `exceptions.py`）
   * **状態・コンテキスト管理**（`context/` サブパッケージ）
   * **対話・検索の実行エンジン**（`agent.py`, `search.py`）
   * **動的プロンプト・Skills・ツール拡張**（`prompts.py`, `skills/`, `tools/`）
2. **レガシー互換レイヤの撤去（完全刷新）**:
   旧 `AIChatAgent` エイリアスや過渡的なラッパーメソッドは残さず、`ChatAgent`・`SearchAgent`・`ChatSession` 等を中心としたクリーンな API に統一し、`project`・`cli`・`tests` を一括で追従させます。
3. **レイヤー依存方向の厳守**:
   パッケージ間の依存方向を **`cli` ➔ `project` ➔ `core`** の単方向に保ちます。`core` パッケージは `rich` や `prompt_toolkit`、`project` に一切依存しません。

### 2.2 ローカル環境情報の露出防止と権限境界の原則
ファイルや拡張リソースを扱う際、ホストOSの絶対パス・OSユーザー名・上位ディレクトリ構造などがプロンプトや外部APIへ露出しないよう、以下の3原則を徹底します。

1. **ハッシュ値による資料同一性管理とパス非保持（`MaterialStore`）**:
   * `MaterialStore` はローカルのファイルパス文字列や物理ファイル属性（`mtime`, UID/GID 等）を **一切保持・使用しません**。
   * ファイルの重複登録防止には本文の SHA-256 ダイジェスト（`content_hash`）を使用し、同一ファイルの再読込による差し替え判定には呼び出し側で一方向ハッシュ化した不透明な識別子（`source_hash`）を使用します。表示名にはユーザー指定ラベルまたはディレクトリ階層を含まない短い名称のみを用います。
2. **作業前提資料（`MaterialStore`）への追加権限の厳格な分離**:
   * **AI（エージェント処理フロー）側から追加できるのは「Web検索結果（`WEB_SEARCH`）」のみ** とし、AIに対してローカルファイルやテキストを `MaterialStore` へ追加・削除するツールは一切提供しません。
   * **検索結果以外の資料（ファイルを読み込んだテキスト資料やメモ等）の追加、および資料の削除操作はすべてユーザー（CLIコマンド / UI操作）のみが行う** 設計とします。
   * また、`SearchAgent` は `ChatSession` / `MaterialStore` への参照を一切持たず、ローカル由来の資料データが Web 検索 API へ送信される経路を構造的に遮断します。
3. **Skills および Tools 用ディレクトリの完全固定化と物理パス秘匿**:
   * 任意の外部ディレクトリやホームディレクトリ（`~/.gemio` 等）を走査する仕組みは設けず、読み込み元を **「① Gemioパッケージ内の標準固定ディレクトリ」** と **「② プロジェクトルート直下の固定ディレクトリ（`.gemio/skills/`, `.gemio/tools/`）」** の2箇所のみに固定します。これにより冗長なパス探索・検証処理を排除します。
   * AIへ渡すカタログ・システムプロンプト・ツール応答・エラーメッセージには **論理名（スキル名・ツール名）と説明・本文のみ** を含め、物理ファイルパスや生の `OSError` メッセージは一切露出させません。
   * プロジェクト直下の `.gemio/` ディレクトリは人間が管理する保護領域とし、`Project` 層のガードレールで AI による直接の作成・書き換え・削除をブロックします。

---

## 3. 新しい `src/gemio/core` パッケージ構造（To-Be）

### 3.1 ディレクトリ・モジュール構成

```text
src/gemio/core/
├── __init__.py          # パッケージ公開ファサード（主要クラス・定数の再公開）
├── exceptions.py        # 共通例外定義（AgentExecutionAborted 等 ※外部SDK非依存）
├── models.py            # モデル定義・エイリアス解決・トークン閾値・ログ種別定数（※外部SDK非依存）
├── client.py            # GenAIClient（API通信・指数バックオフリトライ）& TokenUsageTracker
├── prompts.py           # 標準インストラクション定数 & SystemPromptComposer（動的プロンプト合成）
├── agent.py             # ChatAgent（通常対話・Function Callingループ実行エンジン）
├── search.py            # SearchAgent（Web検索対話・Grounding抽出・SearchResult生成）
│
├── context/             # 【コンテキスト・作業前提資料管理サブパッケージ】
│   ├── __init__.py      # ChatTurn, ChatHistory, ReferenceMaterial, MaterialStore, ChatSession 等を公開
│   ├── history.py       # ターン単位の対話履歴管理（巻き戻し・コピー/再表示用抽出・ロール整合性保護）
│   ├── materials.py     # 作業前提資料ストア（パス非保持・ハッシュ重複防止・個別削除・プロンプト整形）
│   ├── compactor.py     # ContextCompactor（LLMを用いた古い対話履歴の構造化要約・圧縮）
│   └── session.py       # ChatSession（ChatHistory + MaterialStore を束ねるセッション状態管理）
│
├── skills/              # 【Skills管理サブパッケージ】
│   ├── __init__.py      # SkillSpec, SkillLoader, SkillManager を公開
│   ├── builtin/         # パッケージ標準組み込みスキル格納用固定ディレクトリ（*/SKILL.md）
│   ├── loader.py        # 固定2ディレクトリ（builtin/ と .gemio/skills/）からの SKILL.md 解析・遅延ロード
│   └── manager.py       # スキルカタログ生成・オンデマンド有効化/無効化・メタツール提供
│
└── tools/               # 【ツール・動的ツールセット管理サブパッケージ】（旧 tools.py から昇格）
    ├── __init__.py      # ToolSpec, Toolset, ToolRegistry, StaticToolProvider, agent_tools 等を公開
    ├── builtin.py       # 標準組み込みツール（get_current_date, get_current_time）
    ├── loader.py        # プロジェクト内固定ディレクトリ（.gemio/tools/）からの拡張ツール読込
    ├── registry.py      # ツールメタデータ管理・名前解決・安全実行・StaticToolProvider（標準方式）
    └── dynamic.py       # [Experimental] DynamicToolsetProvider（AIからのツール要求・動的組み合わせ）
```

### 3.2 コンポーネント連携・権限境界図

```text
                       ┌──────────────────────────────────────────────────┐
                       │             GemioCLI / UI Layer (User)           │
                       └──┬────────────────────┬───────────────────────┬──┘
       [User専用操作]     │                    │通常対話               │Web検索 (/search)
  ・ファイル読込資料の追加│                    │                       │
  ・メモ追加 / 個別削除   │                    │                       │
  ・巻き戻し / コピー抽出 │                    │                       │
                          ▼                    ▼                       ▼
┌───────────────────────────────────┐   ┌─────────────┐         ┌─────────────┐
│            ChatSession            │◄──┤  ChatAgent  │         │ SearchAgent │
│  ├── ChatHistory (ChatTurn列)     │   └──────┬──────┘         └──────┬──────┘
│  └── MaterialStore (パス非保持)   │          │                       │SearchResult返却
│       ・WEB_SEARCH (検索結果のみ) │◄─────────┼───────────────────────┘(成功時のみ検索結果を追加
│       ・USER_DOCUMENT (User追加)  │          │                        ※MaterialStoreはSearchAgentへ非公開)
└─────────────────▲─────────────────┘          │毎ループ動的取得
                  │要約・圧縮                  ▼
         ┌────────┴─────────┐        ┌────────────────────────┐
         │ ContextCompactor │        │ ・SystemPromptComposer │
         └────────┬─────────┘        │   └─ SkillManager      │ (固定: builtin/ + .gemio/skills/)
                  │                  │ ・ToolProvider         │ (固定: builtin  + .gemio/tools/ + Project)
                  │                  │   (Static / Dynamic)   │
                  │                  └─────────┬──────────────┘
                  ▼                            ▼
         ┌────────────────────────────────────────────────────────────────┐
         │       GenAIClient (API通信・リトライ) + TokenUsageTracker      │
         └────────────────────────────────────────────────────────────────┘
```

---

## 4. 各モジュール・サブパッケージの詳細設計

### 4.1 基盤モジュール群（`exceptions.py`, `models.py`, `client.py`, `prompts.py`）

#### (1) `src/gemio/core/exceptions.py`
外部SDKに依存しない軽量な例外定義モジュールです。
* `AgentExecutionAborted(Exception)`: ユーザーの Abort 選択またはサーキットブレーカー発動による作業ループ強制中止例外。

#### (2) `src/gemio/core/models.py`
外部SDKに依存しない定数・データ型定義モジュールです。
* モデル設定: `DEFAULT_AI_MODEL_NAME`, `MODEL_PRESETS`, `MODEL_ALIASES`, `resolve_model_name(input_name: str) -> str`
* コンテキスト健全性しきい値: `CONTEXT_TOKEN_WARNING_THRESHOLD` (`50_000`), `CONTEXT_TOKEN_DANGER_THRESHOLD` (`150_000`)
* ログ種別定数 `AgentLogCategory`:
  * `USER = "user"`, `ASSISTANT = "assistant"`, `TOOL = "tool"`, `WARNING = "warning"`, `ABORT = "abort"`, `SEARCH = "search"`
  * ※ `cli/ui/base.py` はこの定数クラスをインポートして再公開し、`core` ➔ `cli` の逆依存を作らずにエージェント側から明示的なログカテゴリを通知できるようにします。

#### (3) `src/gemio/core/client.py`
API通信・リトライ制御とトークン使用量集計を担う共通クライアント層です。

* **`TokenUsageTracker` クラス**:
  * `record(usage_metadata: Any, source: str = "chat") -> None`:
    * `source`（`"chat"` | `"search"` | `"compaction"`）別のAPI呼び出し回数と累積トークン（`prompt`, `candidates`, `thoughts`, `cached`, `tool_use`, `total`）を集計します。
    * `source == "chat"` のときのみ、現在の対話コンテキスト規模を表す `last_prompt_tokens` / `last_candidates_tokens` / `last_total_tokens` を更新します。
  * `reset() -> None`: カウンタを初期状態（0）にリセットします。
  * `get_snapshot(history_messages: int = 0, materials_count: int = 0) -> dict[str, int]`: UI表示用の統計スナップショット辞書を返します。

* **`GenAIClient` クラス**:
  * `__init__(self, model_name: str = DEFAULT_AI_MODEL_NAME, api_key: str | None = None, token_tracker: TokenUsageTracker | None = None)`:
    * `genai.Client` の生成を1箇所にカプセル化します（将来の Vertex AI / Gemini Enterprise 認証対応時も本クラスのみの変更で完結します）。
    * `set_model(model_name: str)` により、共有クライアント全体の使用モデルを一括切り替えできます。
  * `generate_content(...)`:
    * 503 / 429 / 500 / 502 / 504 やネットワークタイムアウトに対する指数バックオフ＋ジッター付き自動リトライを実行し、成功時に `self.token_tracker.record(response.usage_metadata, source=usage_source)` を自動記録します。
  * `close() -> None`: 内部の `genai.Client` をクローズします。

#### (4) `src/gemio/core/prompts.py`
プロンプト定数と、動的なシステムインストラクション合成器を提供します。

* 定数:
  * `DEFAULT_SYSTEM_INSTRUCTION`, `DEVELOPER_MODE_INSTRUCTION_ADDON`, `DEFAULT_SEARCH_INSTRUCTION`
  * 「作業前提資料の取り扱いに関するルール」を、Web検索結果およびユーザーが明示提供した参照資料の双方に適合する記述へ整備します。
* **`SystemPromptComposer` クラス**:
  * ベース指示、開発者モード追加指示、`SkillManager`（スキルカタログおよび有効化中スキルの本文）、`ToolProvider`（動的ツールセット有効時のツールカタログ）を受け取り、`compose() -> str` メソッドで**そのイテレーション時点の最新システムインストラクション文字列**を動的に組み立てます。

---

### 4.2 コンテキスト・作業前提資料管理サブパッケージ（`context/`）

#### (1) `src/gemio/core/context/history.py`（ターン単位の対話履歴）
生の `types.Content` のフラット配列を廃止し、1回のプロンプト対話単位を表す **`ChatTurn`** のリストとして管理します。

* **`ChatTurn` データクラス**:
  ```python
  @dataclass
  class ChatTurn:
      turn_id: str                     # 一意識別子（連番または短縮ID）
      user_prompt: str                 # ユーザーが入力した生のプロンプト文字列（前提資料未注入）
      contents: list[types.Content]    # このターン内で発生した user -> (model call -> user resp)* -> model 最終応答
      assistant_response: str          # 最終的なAIのテキスト応答
      thoughts: list[str]              # 思考プロセス（Thinking）テキスト（取得可能な場合）
      tool_call_count: int = 0         # このターン内で実行されたツール呼び出し回数
      aborted: bool = False            # 強制中止またはエラー中断で終了したターンかどうか
      created_at: str = ""             # 作成時刻 ("HH:MM:SS")
  ```
* **`ChatHistory` クラス**:
  * `turns: list[ChatTurn]` および圧縮済み要約文 `summary_text: str | None` を保持します。
  * **ターン追加 (`append_turn(turn: ChatTurn)`)**: 完了したターンを確定記録します。
  * **対話の巻き戻し (`rewind(steps: int = 1) -> list[ChatTurn]`)**:
    * 末尾から指定ターン数を丸ごとポップして返します。1ターン内に何回の Function Calling 往復が含まれていてもターン境界ごと除去されるため、API のロール交互規約や `function_call` / `function_response` のペア整合性が常に保たれます。
  * **クリップボードコピー・再表示用抽出**:
    * `last_turn -> ChatTurn | None`: 直近のプロンプト（`user_prompt`）や回答（`assistant_response`）をノイズなしで即時取得します。
    * `get_turns(start: int | None = None, end: int | None = None) -> list[ChatTurn]`: 範囲指定でのターン取得。
    * `format_turns_markdown(turns: list[ChatTurn] | None = None, include_user: bool = True, include_assistant: bool = True) -> str`: コピーやコンテキスト再表示に適した Markdown テキストを生成します。
  * **API送信用コンテンツ展開 (`to_contents() -> list[types.Content]`)**:
    * `summary_text` が存在する場合は先頭に「これまでの対話の要約」ペア（`user` / `model`）を配置し、続けて全 `turns` の `contents` をフラット展開したスナップショットリストを返します。

#### (2) `src/gemio/core/context/materials.py`（パス非保持・ハッシュ管理の作業前提資料ストア）
ローカルファイルパスを一切保持せず、ハッシュ値によって重複防止・差し替えを行う **`MaterialStore`** を定義します。

* **`MaterialKind` (Enum / 定数)**:
  * `WEB_SEARCH = "web_search"`（`SearchAgent` による Web 検索結果）
  * `USER_DOCUMENT = "user_document"`（ユーザーがファイル読み込みまたはテキスト入力によって追加した前提資料）
* **`ReferenceMaterial` データクラス**:
  ```python
  @dataclass
  class ReferenceMaterial:
      material_id: str                 # 個別指定・削除用ID（例: "m1", "m2"...）
      kind: MaterialKind               # 資料種別 (web_search / user_document)
      title: str                       # 表示ラベル（検索クエリ、ユーザー指定見出し、またはパスを含まない短い識別名）
      content: str                     # 資料本文テキスト
      content_hash: str                # 本文の SHA-256 ハッシュ値（同一内容の重複追加防止用）
      source_hash: str | None = None   # 取得元識別子の SHA-256 ハッシュ値（同一元データの更新・差し替え用、パス非保持）
      sources: list[dict[str, str]] = field(default_factory=list)  # Web出典 [{"title": ..., "url": ...}]（WEB_SEARCH用）
      created_at: str = ""             # 追加時刻 ("HH:MM:SS")
      char_count: int = 0              # 文字数（コンテキスト規模の目安表示用）
  ```
* **`MaterialStore` クラス**:
  * **検索結果の追加 (`add_search_result(query, response_text, sources, created_at="") -> tuple[ReferenceMaterial, bool]`)**:
    * `SearchAgent` の実行結果を追加します。同一 `content_hash` が既に存在する場合は重複追加せず `(既存資料, False)` を返します。
  * **ユーザー資料の追加 (`add_user_document(title, content, source_hash=None, created_at="") -> tuple[ReferenceMaterial, str]`)**:
    * **ユーザー操作（CLI/UI）専用のメソッド**です（AIツールとしては公開しません）。
    * 呼び出し側（CLI層）がファイルを読み込む際は、`Project` のセキュリティ検証を通過したテキスト内容と、パス階層を除いた表示ラベル（またはユーザー指定タイトル）、および必要に応じて正規化識別子の SHA-256 ハッシュ（`source_hash`）のみを渡します。
    * 重複・更新制御：
      1. `content_hash`（本文の SHA-256）が既存資料と完全一致する場合は重複登録せず `"duplicate"` ステータスで既存資料を返します。
      2. `source_hash` が指定されており、同一 `source_hash` の既存資料がある場合は新しい内容へインプレース更新し `"updated"` ステータスを返します。
      3. いずれにも該当しない場合は新規追加し `"added"` ステータスを返します。
  * **一覧取得・個別削除・クリア（ユーザー操作専用）**:
    * `list_all() -> list[ReferenceMaterial]`
    * `remove(identifier: str | int) -> ReferenceMaterial | None`: 1始まりの表示連番または `material_id` で不要な資料を個別削除します。
    * `clear(kind: MaterialKind | None = None) -> int`: 全資料クリア、または種別指定クリア（例: `/search --clear` で検索資料のみ破棄、または全破棄）を行います。
  * **プロンプト注入用フォーマット (`format_for_prompt() -> str`)**:
    * 資料が0件なら空文字列を返します。
    * `WEB_SEARCH` 資料は長大なリダイレクトURL（`vertexaisearch...`）を除外して「タイトル（＋判別可能ドメイン）」を記載し、`USER_DOCUMENT` 資料は `title` と本文のみを出力します（ローカルパス情報は一切含まれません）。

#### (3) `src/gemio/core/context/compactor.py`（コンテキスト要約・圧縮）
* **`ContextCompactor` クラス**:
  * `compact(history: ChatHistory, keep_recent_turns: int = 2) -> CompactionResult`:
    * 既存の `history.summary_text` と、直近 `keep_recent_turns` を除いた古い `ChatTurn` 群を `GenAIClient`（`usage_source="compaction"`）で構造化要約し、`history` の古いターン群を要約へ圧縮置換します。

#### (4) `src/gemio/core/context/session.py`（`ChatSession`）
* `history: ChatHistory` と `materials: MaterialStore` を束ねるセッション状態管理クラスです。
* `build_request_contents(pending_contents: list[types.Content] | None = None) -> list[types.Content]`:
  * 履歴コンテンツに進行中ターンの `pending_contents` を結合したスナップショットを生成し、`materials` に資料がある場合は直近のユーザー発話テキスト先頭に `materials.format_for_prompt()` を合成して返します（履歴本体の `ChatTurn` は一切肥大化しません）。
* `reset(clear_materials: bool = False) -> dict[str, int]`: 履歴および必要に応じた前提資料をクリアし、サマリ辞書を返します。

---

### 4.3 実行エンジンの分離（`agent.py` & `search.py`）

#### (1) `src/gemio/core/agent.py`（`ChatAgent`）
Web検索関連のコードをすべて排除し、通常対話と Function Calling ループに専念するクラスです。

* **動的プロンプト＆ツール評価ループ (`_execute_agent_loop`)**:
  * **毎イテレーションの先頭**で `prompt_composer.compose()` と `tool_provider.get_active_tools()` を評価して `config` を構築します。これにより、ループ内で AI が `activate_skill` や `request_tools` を呼び出した直後のイテレーションから、追加されたスキル手順やツール定義が即座に有効化されます。
  * ツール実行は `tool_provider.execute(func_name, func_args, executor=tool_executor)` に委譲し、未知・未有効ツールの呼び出し時も `StopIteration` を起こさず安全な案内メッセージを `function_response` として返します。
  * サーバーエラー中断時の実行済みツール結果保持とモデル中断メッセージ補完、Abort時の複数 `function_call` ペア整合性保護、および連続実行上限到達時の追加ループ／ロールバック制御をターン単位でクリーンに維持します。
* **戻り値とログカテゴリ通知 (`process_prompt`)**:
  * ログコールバックへ明示的な `AgentLogCategory` を通知可能にし、完了時は応答テキストと確定した `ChatTurn` を含む `ChatTurnResult` を返します。

#### (2) `src/gemio/core/search.py`（`SearchAgent`）
Google Search Grounding 専用の独立エージェントクラスです。

* **`SearchResult` データクラス**:
  * `query: str`, `response_text: str`, `sources: list[dict[str, str]]`, `created_at: str`, `succeeded: bool = True`
* **`SearchAgent` クラス**:
  * `ChatSession` や `MaterialStore`、ローカルツールへの参照を一切持たず、`search_history: list[types.Content]` のみを内部管理します。
  * `search(query: str, on_log_append: Callable | None = None, new_session: bool = False) -> SearchResult`:
    * Google検索ツール専用 Config で `client.generate_content(..., usage_source="search")` を呼び出し、出典の重複排除を行って `SearchResult` を返します。
    * 通信エラー時は `search_history` をロールバックし、`succeeded=False` を返すことでエラー文が作業前提資料に混入するのを防ぎます。
  * `reset_history() -> None` / `has_active_session -> bool`: 検索文脈のリセットと状態確認を呼び出し側（CLI層）から明示的に制御できます。

---

### 4.4 Skills管理サブパッケージ（`skills/`）

ディレクトリの固定化と段階的開示（Progressive Disclosure）により、簡潔・安全かつ低トークン消費な Skills 機構を実現します。

#### (1) 読み込みディレクトリの固定仕様 (`src/gemio/core/skills/loader.py`)
任意のディレクトリリストを受け取る汎用探索は行わず、以下の **固定2箇所のみ** を対象とします：
1. **パッケージ標準固定ディレクトリ**: `Path(__file__).resolve().parent / "builtin"`（`src/gemio/core/skills/builtin/<skill_name>/SKILL.md`）
2. **プロジェクト直下固定ディレクトリ**: `project_root / ".gemio" / "skills"`（`<project_root>/.gemio/skills/<skill_name>/SKILL.md` ※ `project_root` が渡された場合のみ。同名スキルはプロジェクト側を優先）

* **セキュリティ＆露出防止ガード**:
  * 各固定ディレクトリ配下の1階層（`<skill_name>/SKILL.md`）のみを直接検査し、再帰探索や対象ディレクトリ外を指すシンボリックリンクの追跡は一切行いません。
  * **`SkillSpec` データクラス**には物理パスを公開属性として露出させず、`name: str`, `description: str`, `recommended_toolsets: list[str]` と、例外サニタイズ済みの内部ローダー関数のみを保持します。ファイル読み込みエラー発生時も物理パスを含まない安全なメッセージのみを返します。

#### (2) `src/gemio/core/skills/manager.py`（`SkillManager`）
* 初期状態では全登録スキルの `name` と `description` のみを並べた軽量なカタログ文を `SystemPromptComposer` へ提供します。
* AIがメタツール `activate_skill(skill_name: str)` を呼び出す（またはユーザーが有効化する）と、対象スキルの Markdown 本文をアクティブ集合へロードし、次のイテレーションからシステムプロンプトへ動的に付加します。

---

### 4.5 ツール・動的ツールセット管理サブパッケージ（`tools/`）

#### (1) 固定ディレクトリからの拡張ツール読込 (`src/gemio/core/tools/builtin.py`, `loader.py`)
* **標準組み込みツール (`builtin.py`)**: `get_current_date`, `get_current_time`（常時利用可能な基本ツールセット）。
* **プロジェクト固定ツールディレクトリ (`loader.py`)**:
  * カスタムツールの読み込み先を **`<project_root>/.gemio/tools/*.py`** の1箇所のみに固定します（アンダースコア始まりのファイルやシンボリックリンクは除外）。
  * モジュール内で明示公開された `Toolset` または `agent_tools` リストを読み込みます。エラー時やツール説明にも物理ファイルパスは一切含めません。

#### (2) `ToolRegistry` と `ToolProvider` (`src/gemio/core/tools/registry.py`)
* **`ToolSpec` / `Toolset` / `ToolRegistry`**:
  * ツール関数、関数名、1行サマリ（Docstring先頭行）、所属ツールセット名を管理します。
  * 注意点として、`MaterialStore` を操作する関数は一切ツール登録せず、AIが前提資料ストアを書き換えられない設計を保証します。
* **`StaticToolProvider(ToolProvider)`（標準実装）**:
  * 登録されたすべてのツールを最初から有効状態で `ChatAgent` に提供します。

#### (3) `DynamicToolsetProvider` (`src/gemio/core/tools/dynamic.py` - 実験的機能)
* **初期状態**: `always_active=True` の最小基本ツール群（例: `ask_user`, 日時取得）と、ツール要求用メタツール **`request_tools(toolsets: list[str] | None = None, tool_names: list[str] | None = None)`** のみをAPIスキーマへ渡します。
* **カタログ注入と動的組み合わせ**:
  * `SystemPromptComposer` 経由で「利用可能なツールセット名と各ツールの1行概要カタログ」をシステムプロンプトへ付加します。
  * AIがタスク遂行に必要なツールセットや個別ツールを `request_tools` で要求するとアクティブ集合に追加され、直後のループイテレーションから対象ツールの Function Calling スキーマが有効化されます。

---

## 5. 周辺パッケージへの刷新・移行仕様

後方互換エイリアス（旧 `AIChatAgent` 等）は残さず、プロジェクト全体を新設計へ統一します。

### 5.1 `src/gemio/project/base.py` & `developer.py`
* `AgentExecutionAborted` のインポート元を `from gemio.core.exceptions import AgentExecutionAborted` に変更します。
* **`.gemio/` 固定ディレクトリの保護追加**:
  * プロジェクト直下の `.gemio/`（`.gemio/skills/`, `.gemio/tools/` 等）を人間が管理する保護領域として位置づけ、`SENSITIVE_ENV_DIR_NAMES` および `_check_fatal_security_path(..., is_write=True)` に追加して、AI による `.gemio/` 配下への無断ファイル作成・書き換え・削除をハードブロックします。
* 動的ツールセットとの連携を見据え、`Project` / `DeveloperProject` のツール群を論理ツールセット（例: `"project_explore"`, `"project_read"`, `"project_write"`, `"sandbox"`）としても取得できるヘルパーメソッド `agent_toolsets()` を整備します。

### 5.2 `src/gemio/cli/main.py`（`GemioCLI`）
* 単一の `self.agent` を廃止し、役割ごとに分離された以下のインスタンスを保持・連携させます：
  * `self.ai_client: GenAIClient`（モデル切替 `/model`・トークン統計 `/tokens`）
  * `self.chat_session: ChatSession`（履歴・作業前提資料・`/clear` リセット、および今後の巻き戻し・資料ファイル追加・個別削除・要約圧縮コマンドの直接操作対象）
  * `self.chat_agent: ChatAgent`（通常プロンプト実行担当）
  * `self.search_agent: SearchAgent`（`/search` 実行担当）
* **検索結果と前提資料の連携 (`handle_search_command`)**:
  * `result = self.search_agent.search(...)` を実行し、`result.succeeded` が `True` の場合のみ `self.chat_session.materials.add_search_result(...)` で登録します。
* **通常プロンプト実行 (`run_chat_prompt`)**:
  * `self.search_agent.reset_history()` で直前の検索深掘り文脈を区切った上で、`self.chat_agent.process_prompt(...)` を呼び出します。

### 5.3 `src/gemio/cli/ui/`
* `base.py`: `AgentLogCategory` を `from gemio.core.models import AgentLogCategory` からインポートして再公開します。
* `prompts.py` / `renderables.py`: 定数・例外のインポート先を `gemio.core.models` および `gemio.core.exceptions` に更新し、UI層から `google-genai` SDK への間接依存を完全に解消します。

---

## 6. テストスイートの再編・検証計画

| テストファイル | 検証対象と主なテスト観点 |
| :--- | :--- |
| `tests/test_core_client_and_models.py` | モデルエイリアス解決、`GenAIClient` の初期化・モデル切替・503リトライ回復、`TokenUsageTracker` のソース別集計 |
| `tests/test_core_context.py` | `ChatTurn` 単位の履歴記録と安全な `rewind`、コピー用抽出、`MaterialStore` の **パス非保持・SHA-256ハッシュによる重複排除と `source_hash` 更新・個別削除**、`ContextCompactor` による要約圧縮、`ChatSession` の先頭注入 |
| `tests/test_core_tools_and_skills.py` | 固定ディレクトリ（`builtin` と `.gemio/{skills,tools}`）からの安全なロード、**物理パス非露出・例外サニタイズ検証**、`StaticToolProvider` と `DynamicToolsetProvider`（`request_tools` 動的有効化）、`SystemPromptComposer` |
| `tests/test_core_agent_chat.py` | `ChatAgent` のループ実行、`ask_user` / Abort ペア整合性、上限継続とロールバック、ループ途中での Skill / 動的ツール即時反映、**AIに `MaterialStore` 操作ツールが露出していないこと** の検証 |
| `tests/test_core_agent_search.py` | `SearchAgent` の専用 Config、出典抽出・重複排除、**通常履歴および `MaterialStore` 内のユーザー資料が検索APIへ一切送信されないこと**、エラー時のロールバックと `succeeded=False` 判定 |
| `tests/test_project_base.py` / `developer.py` | **`.gemio/` ディレクトリへのAI書き込みブロック検証**、既存のファイル保護・承認モード・サンドボックス検証 |
| `tests/test_cli_main.py` / `test_ui_*.py` | `GemioCLI` における新コンポーネント群の連携と全スラッシュコマンドの動作検証 |

---

## 7. 段階的実装ロードマップ

* **Phase 1: 基盤分離と `ChatAgent` / `SearchAgent` 分割 + 基本 `ChatSession` 導入**
  * `exceptions.py`, `models.py`, `client.py`, `context/history.py`, `context/materials.py`（ハッシュ管理・パス非保持）, `context/session.py`, `agent.py`, `search.py` を実装し、`project`（`.gemio/` 保護追加含む）・`cli`・`cli/ui` とテストを新構造へ完全移行する。
* **Phase 2: コンテキスト高度化機能の拡充（`context/` 完成）**
  * `ChatHistory.rewind`・コピー/再表示用フォーマット抽出、`MaterialStore` の `add_user_document`（`content_hash` / `source_hash` 制御）・`remove` 個別削除、および `context/compactor.py`（`ContextCompactor`）を実装・単体テストする。
* **Phase 3: 動的プロンプト・ツール基盤の構築（`prompts.py` + `tools/`）**
  * `tools/` サブパッケージ（`builtin.py`, `registry.py`, 固定 `.gemio/tools/` 用の `loader.py`）と `SystemPromptComposer` を構築し、`ChatAgent` の毎イテレーション動的評価へ統合する。
* **Phase 4: `skills/` サブパッケージおよび実験的 `DynamicToolsetProvider` の実装**
  * 固定2ディレクトリ方式の `skills/`（`builtin/`, `loader.py`, `manager.py`）および `tools/dynamic.py`（`DynamicToolsetProvider`）を実装し、段階的開示と動的ツール組み合わせの単体テストを完備する。

---

## 8. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-12 | AI Agent | 設計完了（実装準備中） | 初版作成。検索機能の分離、`context/`・`skills/`・`tools/` の3サブパッケージ構成、および完全刷新アーキテクチャを策定。 |
| 2026-11-12 | AI Agent | 設計完了（実装準備中） | ローカル環境情報の露出防止と簡素化のため、①`MaterialStore` のパス非保持化とSHA-256ハッシュによる重複/更新管理、②検索結果以外の資料追加をユーザー操作のみに限定、③Skills/Tools読み込み先をパッケージ内標準と `.gemio/{skills,tools}` の固定2箇所に限定・`.gemio/` 保護を追加する仕様へ改訂。 |
| 2026-11-12 | AI Agent | 実装中（Phase 1 完了） | Phase 1 を実装完了。`exceptions.py`, `models.py`, `client.py`, `context/`（`history.py`, `materials.py`, `session.py`）, `ChatAgent`, `SearchAgent` への分割、`.gemio/` 保護追加、および周辺パッケージと単体テストの刷新を完了。 |
| 2026-11-12 | AI Agent | 実装中（Phase 2 完了） | Phase 2 を実装完了。`ChatHistory` の範囲抽出・コピー用Markdown生成、`MaterialStore` の参照・再表示用整形、および `ContextCompactor`（`context/compactor.py`）による履歴要約圧縮を実装・単体テスト通過。 |
| 2026-11-12 | AI Agent | 実装中（Phase 3 完了） | Phase 3 を実装完了。`tools/` サブパッケージ（`builtin.py`, `registry.py`, 固定 `.gemio/tools/` 用の `loader.py`）、`SystemPromptComposer`、`Project.agent_toolsets()`、および `ChatAgent` の毎イテレーション動的評価を統合・単体テスト通過。 |
| 2026-11-12 | AI Agent | 実装完了 | Phase 4 を実装完了。固定2ディレクトリ方式の `skills/` サブパッケージ（`loader.py`, `manager.py`, `activate_skill`）および実験的機能 `tools/dynamic.py`（`DynamicToolsetProvider`, `request_tools`, `--dynamic-tools`）を実装し、全80件の単体テスト合格を確認。 |
| 2026-11-17 | AI Agent | 実装完了（現行） | 設計書の3桁連番管理導入に伴い `013_core_package_refactoring.md` へリネームし、ステータス更新および `v0.1.1` プロンプト分離の補足注記を追加。 |
