# #016 作業エージェント連携型Web検索（Agent-as-a-Tool）と検索スイッチ 設計書

**作成日:** 2026年11月23日  
**最終更新日:** 2026年11月23日  
**ステータス:** 実装完了（現行）  
**対象システム:** `src/gemio/core/` (`search.py`, `prompts.py`, `tools/registry.py`, `tools/dynamic.py`), `src/gemio/app/` (`config.py`, `commands.py`, `controller.py`, `prompts.py`, `state.py`), `src/gemio/cli/main.py`, `src/gemio/tui/` (`app.py`, `messages.py`, `commands_palette.py`, `styles/app.tcss`, `widgets/header.py`, `widgets/search_card.py`, `widgets/welcome.py`, `screens/help.py`), `tests/`  

### 【要約】
従来の「ユーザー手動 `/search` 専用・全検索結果の `MaterialStore` 自動蓄積」方式を見直し、ローカル作業中の `ChatAgent` がツール呼び出し（`web_search`）を通じて**コンテキスト隔離・ステートレス化された `SearchAgent` へ質問を委譲し、作業文脈へ直接統合する設計（Agent-as-a-Tool）**を導入します。外部データ送信に伴う機密漏洩を防ぐため、検索ツールは既存の承認モード（`NORMAL` / `EDIT` / `TRUST`）から独立した**「送信プロンプト全文の常時確認」**とし、一律デフォルト `ON` で起動しつつステータスヘッダーの **`+WEB` / `-WEB` バッジ**（および `/search on|off`）で即座に有効・無効を切り替えられる構成とします。あわせて既存の `/search <query>` コマンドは「自動蓄積しない単発検索＋任意の資料追加」として再定義します。

---

## 1. 背景と解決すべき課題（As-Is）

これまでの Web 検索機能（[`#001`](./001_web_search.md), [`#002`](./002_search_material_integration.md), [`#013`](./013_core_package_refactoring.md)）では、`SearchAgent` を通常対話から分離し、ユーザーが `/search` コマンドを実行した際の結果をすべて `MaterialStore`（作業前提資料）へ自動蓄積してプロンプト先頭へ毎回注入していた。しかし、実運用において以下の構造的課題が顕在化していた。

1. **検索過程の無差別蓄積によるトークン浪費とコンテキスト汚染**
   * 目的の情報に辿り着くまでに複数ステップの検索を行った場合、最終結論だけでなく**途中の試行錯誤や空振りの検索回答まですべて `MaterialStore` に永続保持**され、後続の毎ターン・毎ツールループでプロンプト先頭に注入され続けていた。
   * また、`MaterialStore` は `/rewind`（ターン巻き戻し）や `/compact`（古いターンの要約圧縮）の対象外であるため、不要な検索経過がトークンを圧迫し続け、手動で `/search --clear` を実行するしかなかった。
2. **エージェント分離によるワークフローの分断と二度手間**
   * 作業エージェント（`ChatAgent`）が検索手段を持たないため、実装途中で外部ライブラリの最新仕様を1つ確認したいだけでも作業を中断してユーザーに `/search` の実行を促す必要があった。
   * 結果として「`/search` で複数回検索して前提資料に溜める ➔ 作業エージェントで前提資料をまとめた成果物ドキュメントを作成する ➔ `/search --clear` で掃除する ➔ 成果物を参照して実装する」という迂遠な手順になりがちで、必要に応じて最新情報を取得できる Google Search Grounding の強みを活かせていなかった。
3. **組み込み `google_search` ツールの直接付与に伴う機密漏洩リスク**
   * Gemini API 自体は Google Search Grounding と Function Calling の併用が可能になっているものの、ローカルソースコードや機密情報を含む `ChatAgent` に組み込みの `{"google_search": {}}` を直接持たせると、モデル内部で生成される検索クエリにプロジェクト固有コードや識別子が混入しても**送信前に人間が検閲・阻止できない**という重大なリスクがある。
4. **`TRUST` モードにおける自律実行と外部送信確認のジレンマ**
   * Web 検索はローカル外への不可逆なデータ送信（Egress）であるため厳格な人間確認が不可欠だが、単に常時確認のツールとして追加するだけでは、`TRUST` モードで自律実行させている最中にも検索確認モーダルで処理が頻繁に停止してしまう。かといって複雑な組み合わせ承認モードを増やすと実装・操作ともに煩雑になる。

---

## 2. アーキテクチャ基本方針と設計比較

### 2.1 方式比較

| 比較観点 | 現行設計 (`/search` 自動蓄積方式) | `ChatAgent` へ `google_search` 直接付与（※非採用） | 新設計 (`ChatAgent` ➔ ツール経由で隔離 `SearchAgent` 呼出) |
| :--- | :--- | :--- | :--- |
| **機密漏洩防止** | ◎ 安全（人間が手入力した文字列のみ送信） | × 危険（ローカル履歴から内部で自動クエリ生成・検閲不能） | **◎ 安全**（`SearchAgent` はローカル履歴と完全隔離＋渡すプロンプトを送信前に人間が全文確認） |
| **送信前の人間確認 (HITL)** | 人間自身の入力のため自明 | 不可（Built-in ツールはインターセプト不可） | **可能**（Function Calling 引数として可視化され、既存の承認モーダルで検閲・拒否可能） |
| **作業文脈への統合** | × 分断（作業中断と人間のコマンド代行が必要） | ◎ 自然に統合 | **◎ 自然に統合**（コード読込 ➔ Web検索 ➔ 実装・テストが1ターン内で完結） |
| **途中経過のトークン効率** | × 低い（中間検索も `MaterialStore` に残り続け毎ターン注入） | △ 履歴に残るが圧縮可能 | **◎ 高い**（ターン内の `function_response` に留まり、`/compact` で中間経過は自然に消去・圧縮される） |
| **自己矛盾・URL浪費の回避** | ◎ 前提資料化＋リダイレクトURL除去で対応 | × リダイレクトURLや環境差の混同が起きやすい | **◎ 解決**（AI自身の発話ではなく客観的な `function_response` として返却＋URL除去整形を適用） |
| **`/rewind`・`/compact` 整合** | × 非連動（`MaterialStore` にゴミが残る） | ◎ ターン履歴と連動 | **◎ 完全連動**（ターン単位の巻き戻し・要約圧縮と完全に一致） |

### 2.2 採用する5つの基本方針（To-Be）

1. **Agent-as-a-Tool による機密隔離検索の統合**:
   * Google Search Grounding の実行主体は引き続きローカル履歴・`MaterialStore`・ローカルツールから完全に隔離された `SearchAgent` とし、`ChatAgent` からは Python ツール `web_search(query: str)` を通じてプロンプト（質問文）のみを渡し、回答テキストを受け取る。
2. **`SearchAgent` の完全ステートレス（1問1答）化**:
   * 旧 `SearchAgent.search_history`（連続検索履歴）および `-n` / `--new` オプションを廃止し、すべての検索リクエストを履歴なしの1問1答にする。
   * これにより、**「承認モーダルに表示されたプロンプト全文 ＝ 外部検索エージェントへ渡る全データそのもの（What You See Is What You Send）」**という透明性を100%保証する。深掘り調査が必要な場合は、ローカル文脈を持つ `ChatAgent` が次のイテレーションで条件を絞り込んだ自己完結プロンプトを再発行する。
3. **検索結果の `function_response` 直接返却とコンテキスト自然代謝**:
   * AI ツール呼び出しによる検索結果は `MaterialStore` へは蓄積せず、暗号化リダイレクト URL（`vertexaisearch...`）を除外した構造化テキストとして `function_response` に返却する。
   * 既存の `ContextCompactor`（`/compact`）は古いターンを要約する際に途中の `function_response` を破棄し、最終回答と決定事項のみを残すため、複数ステップの検索試行錯誤を行っても古い検索経過のトークンは自動的に回収される。
4. **承認モードから独立した「常時確認」と `+WEB` / `-WEB` スイッチ（一律デフォルト `ON`）**:
   * `Project.approval_mode`（`NORMAL` / `EDIT` / `TRUST`）を変更・複雑化させず、AI からの `web_search` 実行は**どの承認モードでも常に承認モーダルで確認する**仕様とする。
   * 同時に、AI 検索ツールの有効・無効フラグ（`web_search_enabled: bool`、**一律デフォルト `True`**）を設け、上部 `StatusHeader` の承認バッジ右隣に配置した **`+WEB` / `-WEB` バッジのクリック**や `/search on|off` で即座に切り替えられるようにする。`TRUST` モードで完全放置実行したい場合や外部送信自体を封じたい場合は `-WEB` にすることで、AI から検索ツール自体が隠蔽され承認待ち停止が発生しなくなる。
5. **`/search` コマンドの「単発検索＋任意資料追加」への再定義**:
   * 既存の `/search <query>`（`/s <query>`）は、対話履歴（`ChatHistory`）を汚さずにユーザーが直接調べ物を行う単発検索機能として残す。
   * ただし自動での `MaterialStore` 蓄積は廃止し、検索結果カードの **`[+ 資料に追加]` ボタン**（または `/search --save` コマンド）でユーザーが有用と判断した結果のみを選択的に作業前提資料へ登録できるようにする。

---

## 3. レイヤー別詳細設計

既存の4層パッケージ依存規則（`tui` / `cli` ➔ `app` ➔ `project` / `core`）を厳守し、新しいモーダル画面や複雑な承認モード分岐を追加せずに既存機構へ統合する。

```text
StatusHeader: [Gemio] proj | STD | NORMAL +WEB | gemini-4-neon | Ctx:14.2k M:0
                                          │クリック or `/search on|off`
                                          ▼
                            Toolset("web_search").enabled (default: True)
                                          │毎イテレーション動的同期
                 ┌────────────────────────┴────────────────────────┐
                 ▼ (enabled=True)                                  ▼ (enabled=False)
   ・web_search ツールをAPIへ公開                    ・web_search ツールを非公開化
   ・検索活用＆機密抽象化プロンプトを注入            ・検索無効＆`/search` 案内プロンプトを注入
                 │
                 ▼ ChatAgent が web_search(query) を呼び出し
   1. プリフライト静的検査（物理パス・シークレットパターン・文字数上限チェック）
   2. 常時確認 ApprovalModal（NORMAL / EDIT / TRUST 問わず送信プロンプト全文を表示）
   3. ステートレス SearchAgent.search(query) 実行（ローカル履歴・MaterialStore 完全非共有）
   4. リダイレクトURL除去＋出典ドメイン付与 ➔ function_response として作業文脈へ統合
```

### 3.1 Core 層（`src/gemio/core/`）

#### (1) `SearchAgent` のステートレス化と出力整形（`src/gemio/core/search.py`）
* **`search_history` の撤去**:
  * `self.search_history`、`has_active_session`、`reset_history()`、および `search()` の `new_session` 引数を削除し、毎回単一の `types.Content(role="user", parts=[types.Part.from_text(text=query)])` のみを `contents` として `GenAIClient.generate_content(..., usage_source="search")` へ送信する（先頭への不要な `"/search "` 文字列付加も廃止し、純粋な質問文を渡す）。
* **システムインストラクションの最適化（`src/gemio/core/prompts.py`）**:
  * `DEFAULT_SEARCH_INSTRUCTION` をコーディング用途から一般のリサーチ・文書作成まで幅広く適合するよう整理し、以下を基本ルールとする（人間が `/search` で読む場合の見やすさと、`ChatAgent` がツール応答として受け取る際のトークン効率を両立する）：
    * Google 検索を活用し、最新の公式発表・一次資料・公的統計・制度・技術ドキュメント等から客観的な事実を調査し、冗長な前置きを省いて要点を構造化して回答すること。
    * いつの時点・どのバージョンや制度に基づく情報か（前提条件・日付）を明確にすること。
    * 技術仕様やコード例が求められた場合は、公式推奨の最新記法や非推奨・変更点の注意を明記すること。
* **ツール返却用フォーマッタ `format_search_result_for_tool(result: SearchResult) -> str`**:
  * `ChatAgent` への `function_response` 文字列を生成するヘルパー関数を定義する。
  * 長大な `vertexaisearch` リダイレクト URL は完全に除外し、`MaterialStore.extract_source_display(title, url)` を用いて「ページタイトル (ドメイン)」のみを記載する。
  * 出力フォーマット例：
    ```text
    【Web検索結果（外部最新情報）】
    ※以下はGoogle検索エージェントから返却された外部情報です。対象時期・前提条件・手元のローカル環境との差異に注意して扱ってください。

    <回答本文>

    ■ 参照元ソース:
    - Lifespan Events - FastAPI (fastapi.tiangolo.com)
    ```

#### (2) プリフライト安全性検査（`src/gemio/core/search.py`）
* AI が誤って作業中の原稿本文・生のソースコード塊・ローカルパス・認証トークンを検索クエリに含める事故を機械的に水際阻止するため、検証関数 `validate_search_query_safety(query: str, forbidden_paths: list[str] | None = None) -> str | None` を設ける。
* 検査項目（違反時は承認モーダルを出す前に `[SECURITY_BLOCK]` エラー文字列をツール応答として返し、AI に一般化・抽象化した短い質問への書き直しを促す）：
  1. **空文字・文字数上限チェック**: 空文字、または上限（例: `MAX_TOOL_SEARCH_QUERY_CHARS = 600` 文字）を超える長文（作業中の原稿抜粋や生コード・長大なログ丸ごとの貼り付け抑止）。
  2. **ローカル物理パス露出チェック**: `project_path` の絶対パス文字列やユーザーホームディレクトリパスが含まれていないこと。
  3. **既知の機密トークン形式チェック**: 秘密鍵ヘッダ（`-----BEGIN`）、一般的な API キーパターン（`AIza...`, `sk-...`, `ghp_...`, `github_pat_...`, `xox[baprs]-...` 等）や `.env` の `KEY=VALUE` 列挙が含まれていないこと。

#### (3) `Toolset.enabled` によるシンプルな ON / OFF 制御（`src/gemio/core/tools/`）
* `Toolset`（`core/tools/registry.py`）に **`enabled: bool = True`** フィールドを追加する。
* **`StaticToolProvider`**:
  * `get_active_tools()` で `ts.enabled is False` のツールセットに属するツールをスキップする。
* **`DynamicToolsetProvider`**:
  * `ts.enabled is False` のツールセットは `get_active_tools()`・`get_catalog_prompt()`・`request_tools()` の対象外とし、無効中に呼び出された場合は `"[ERROR] ツール 'web_search' は現在ユーザー設定により無効化（-WEB）されています。"` を返す。
* なお、`web_search` ツールセットは 1 関数のみでスキーマトークンが極小であるため、`always_active=True` として登録する（`enabled=True` の間は `--dynamic-tools` 環境下でも `request_tools` の1往復を消費せず即座に呼び出し可能）。

---

### 3.2 App / CLI 層（`src/gemio/app/`, `src/gemio/cli/`）

#### (1) 起動設定と初期値（`src/gemio/app/config.py`, `src/gemio/cli/main.py`）
* `AppConfig` に `enable_web_search: bool = True`（**一律デフォルト `ON`**）を追加する。
* CLI 引数（`cli/main.py`）にも、起動時点から検索ツールを無効化したい自動化・機密用途向けにオプション `--no-web-search`（指定時 `enable_web_search=False`）を追加する。

#### (2) `GemioController` における検索ツール結線と常時確認制御（`src/gemio/app/controller.py`）
* **状態管理**:
  * `self._web_search_toolset: Toolset` の `enabled` 属性（初期値 `config.enable_web_search`）を唯一の真実のソース（Single Source of Truth）とする。
  * プロパティ `web_search_enabled -> bool` および切替メソッド `set_web_search_enabled(enable: bool | None = None) -> tuple[bool, bool]`（`None` 時はトグル、`"(現在の有効状態, 変更されたか)"` を返却）を提供する。
* **`ChatAgent` 向けツール関数 `web_search(query: str) -> str` の実行フロー**:
  1. **プリフライト検査**: `validate_search_query_safety(query, forbidden_paths=[str(self.project_path), str(Path.home())])` を実行し、違反があれば即座にエラー案内文字列を返す。
  2. **常時確認（全承認モード共通）**:
     * `Project.approval_mode`（`NORMAL` / `EDIT` / `TRUST`）を参照せず、**無条件に `self.on_approval(action_desc)` を呼び出す**。
     * `action_desc` には `summarize_text_for_display()`（省略処理）を使用せず、**検索エージェントに送信される `query` 全文を1文字も省略せずそのまま含める**：
       ```text
       Web検索エージェントへの問い合わせ（外部送信）:
         ※ローカル対話履歴やファイルは送信されません。以下のプロンプトのみが送信されます。

         <query 全文>
       ```
     * **承認結果のハンドリング**:
       * `'y'`（許可）: 検索を実行する。
       * `'n'`（拒否）: `Project._rejection_pending`（ファイル破壊操作用のサーキットブレーカー）は変更せず、`"[REJECTED] ユーザーによってWeb検索の実行が拒否されました。手元のコード・資料のみで作業を進めるか、ask_user ツールで方針を相談してください。"` を返す。
       * `'a'`（強制中止）: 既存どおり `AgentExecutionAborted` を送出して現在のターンを安全に停止する。
       * `'k'`（即時終了）: 既存の `ThreadModalBridge` / `_kill_process` により即時終了する。
  3. **ロック非競合の検索実行と `Esc` キャンセル連動**:
     * `run_chat_turn()` が保持している `self._active_cancel_event` と現在のターン用ログコールバックを参照し、`self.search_agent.search(query, on_log_append=..., cancel_event=active_cancel_event)` を直接呼び出す（トップレベルの `_begin_execution()` を二重取得しないためデッドロックや競合が発生せず、`Esc` キー押下時には実行中の Web 検索も即座に中断される）。
  4. **結果返却**:
     * `result.aborted` 時は `AgentExecutionAborted` を送出。
     * 通信失敗時はエラー内容を `[ERROR]` 文字列として返却。
     * 成功時は `format_search_result_for_tool(result)` を返却する（`MaterialStore` への自動追加は一切行わない）。

#### (3) 動的システムプロンプトの自動同期（`src/gemio/app/prompts.py`）
* 従来の固定定数 `APP_SEARCH_GUIDANCE_INSTRUCTION` を関数 `build_web_search_instruction(enabled: bool) -> str` に置き換え、`SystemPromptComposer(extra_sections=[..., lambda: build_web_search_instruction(self.web_search_enabled)])` として登録する。
* Gemio はコーディングだけでなく文書作成・企画整理・一般リサーチなど幅広い用途で使用されるため、曖昧な表現で拘束力を落とすことなく非コーディング領域の機密も確実に保護できるよう、**「境界原則の明示 ＋ 3領域の具体例列挙」**で構成する。
  * **`enabled=True`（`+WEB`）時（および `web_search` ツールの Docstring）**:
    * **活用基準**: 最新の事実・動向、公的情報・統計、製品や制度の仕様、公式ドキュメントなど、手元の情報だけでは裏付けできない外部情報の確認が必要な場合は、推測で補完せず `web_search` ツールを使用して調査すること。
    * **自己完結クエリの作成**: 検索エージェントはローカルのファイルやこれまでの会話履歴を一切共有していないため、調査対象の一般名称・分野・対象時期やバージョン等の公開前提条件を含めた、**短く自己完結した質問文**を作成すること。
    * **機密・非公開情報の厳格な除外**: ローカルファイルや対話に含まれる非公開情報を検索質問文へ転記することは厳禁とし、以下の3領域の情報は絶対に含めず、**公開されている一般名称・専門用語・標準的な概念や事象のみに抽象化・一般化**して質問すること：
      1. **文書・業務上の非公開情報**: 作業中の原稿・メモ・資料の本文抜粋、未公開の企画名・案件名・製品名、内部の数値や契約条件。
      2. **個人情報・非公開の固有名詞**: 氏名、顧客・取引先名、組織内部の呼称、連絡先・アカウント等の識別情報。
      3. **コード・環境・認証情報**: プロジェクト固有のソースコード断片、非公開の識別子（変数・クラス・内部API名等）、ローカルパス、認証情報（APIキー等）、生の長大なログ全文。
  * **`enabled=False`（`-WEB`）時**:
    * 現在 Web 検索ツールはユーザー設定により無効化（`-WEB`）されている旨を明示し、外部の最新情報が必要で作業を継続できない場合は推測で進めず、ユーザーに `+WEB`（`/search on`）への切り替えまたは `/search <調べたい内容>` の利用を案内すること。

#### (4) `/search` コマンド拡張と単発検索の任意資料化（`src/gemio/app/commands.py`, `state.py`, `controller.py`）
* **`SearchCommand`（`app/commands.py`）**:
  ```python
  @dataclass(frozen=True)
  class SearchCommand:
      query: str = ""
      toggle_enabled: bool | None = None   # /search on -> True, /search off -> False
      save_last: bool = False              # /search --save (-s)
      clear_materials: bool = False        # /search --clear (-c)
      show_help: bool = False              # /search (引数なし)
  ```
  * `/search on` / `/s on`（`enable` も可）➔ `SearchCommand(toggle_enabled=True)`
  * `/search off` / `/s off`（`disable` も可）➔ `SearchCommand(toggle_enabled=False)`
  * `/search --save` / `/search -s` / `/s --save` / `/s -s` ➔ `SearchCommand(save_last=True)`
  * `/search --clear` / `/search -c` / `/s --clear` / `/s -c` ➔ `SearchCommand(clear_materials=True)`
  * 旧 `-n` / `--new` オプションは `SearchAgent` のステートレス化に伴い廃止する。
* **単発検索と任意資料追加API（`app/controller.py`, `app/state.py`）**:
  * `MaterialStatus` を `Literal["unretained", "added", "duplicate", "failed", "aborted"]` に拡張する。
  * `controller.run_search(query)` は `SearchAgent.search(query)` を実行し、成功時は `MaterialStore` へは追加せず `self._last_search_result = result` に記録した上で `material_status="unretained"` の `SearchOutcome` を返す。
  * `controller.save_search_result_to_materials(result: SearchResult | None = None) -> SearchOutcome` を新設し、指定された `SearchResult`（省略時は `_last_search_result`）を `chat_session.materials.add_search_result(...)` で登録して `"added"` または `"duplicate"` の `SearchOutcome` を返す（`/clear` 実行時は `_last_search_result` もクリアする）。
* **`SessionSnapshot`（`app/state.py`）**:
  * `web_search_enabled: bool = True` を追加し、以下の表示プロパティを提供する：
    * `web_badge_text -> str`: `"+WEB"` if `self.web_search_enabled` else `"-WEB"`
    * `web_display_label -> str`: `"Web検索: ON (都度確認)"` if `self.web_search_enabled` else `"Web検索: OFF"`

---

### 3.3 TUI 層（`src/gemio/tui/`）

#### (1) `StatusHeader` の `+WEB` / `-WEB` バッジ（`widgets/header.py`, `messages.py`, `styles/app.tcss`）
* **配置と 80 列幅への適合**:
  * `HeaderBadgeTarget`（`tui/messages.py`）に `"web_search"` を追加する。
  * `StatusHeader` 内の `#hdr-approval`（`NORMAL` / `EDIT` / `TRUST`）の**すぐ右隣にパイプ区切り `|` を挟まず** `_ClickableBadge("", "web_search", id="hdr-web", classes="header-badge")` を配置する（承認バッジの右パディング調整により 1 スペース区切りで並ぶようにし、増加幅をわずか 5 文字に抑えて最小 80 列ターミナルでも1行に余裕で収める）。
  * 表示イメージ：
    ```text
    [Gemio] gemio | STD | NORMAL +WEB | gemini-4-neon | Ctx:14.2k M:0
    [Gemio] gemio | DEV:app | TRUST -WEB | gemini-4-neon | Ctx:38.0k M:1
    ```
* **配色と減光スタイル（`styles/app.tcss`）**:
  * `NORMAL`（緑）・`EDIT`（黄）・`TRUST`（赤）の3色と干渉せず、かつ `-WEB` 時に視界の邪魔にならないよう以下のクラスを切り替える：
    ```css
    #hdr-approval {
        padding: 0 0 0 1;
    }

    .badge-web-on {
        color: $accent;
        text-style: bold;
    }

    .badge-web-off {
        color: $text-muted;
        text-style: none;
    }
    ```
  * `-WEB` 時は `text-style: none` と `$text-muted` により控えめなグレーで沈みつつ、マウスホバー時は `.header-badge:hover { text-style: bold reverse; }` によってクリック可能であることが直感的に伝わる。
* **クリック・コマンド時のトグル動作（`tui/app.py`）**:
  * `HeaderBadgeClicked("web_search")` または `/search on|off`、コマンドパレット実行時に `GemioApp._execute_web_search_toggle(enable)` を呼び出し、`StatusHeader` と `WelcomeBanner` を即時更新してトースト通知を表示する（※AIターン実行中の切り替えは競合防止のため `controller.is_busy` チェックでガードする）。

#### (2) `SearchTurnWidget` の `[+ 資料に追加]` ボタン（`widgets/search_card.py`）
* `/search <query>` による単発検索カード（`SearchTurnWidget`）のヘッダー行（`[Copy]` ボタンの左隣）にコンパクトな **`[+ 資料に追加]` ボタン**（`id="btn-save-material"`）を配置する。
* 検索成功時（`material_status == "unretained"`）にボタンを有効表示し、下部フッターには控えめな案内を表示する：
  * 未保持時: `💡 単発検索結果（作業前提資料へ保持する場合は [+ 資料に追加] または /search --save）`
* ユーザーが `[+ 資料に追加]` をクリック（または `/search --save` を実行）すると、カスタムメッセージ `SaveSearchMaterialRequested(result)` を送出して `controller.save_search_result_to_materials(result)` を実行し、ボタンを無効化（または `[保持済]` 表記へ変更）した上でフッターを `[資料保持] 作業前提資料 [第N弾] としてセッションに保持しました。（破棄: /search --clear）` に更新し、`StatusHeader` の `M:N` カウンタを即時インクリメントする。

#### (3) AI検索ツール実行時のタイムライン可視化（`widgets/turn.py`, `tui/app.py`）
* `ChatAgent` が `web_search` ツールを実行している間、`ActivityBar` に `Tool: web_search` を表示する。
* `SearchAgent` から送出される `AgentLogCategory.SEARCH` ログ（またはツール完了時の参照元サマリ）を `TurnWidget.activity_group` で受け取り、折りたたみログ内に実行された検索質問と参照元ドメイン一覧を記録することで、後からどの情報源を参照したかをいつでも展開確認できるようにする。

#### (4) コマンドパレット・ウェルカムバナー・ヘルプの同期（`commands_palette.py`, `welcome.py`, `screens/help.py`）
* `GemioCommandProvider`（`Ctrl+P`）に以下のエントリを追加する：
  * `Search: AIによるWeb検索ツール (+WEB / -WEB) を切り替え (/search on|off)`
  * `Search: 直近の /search 結果を作業前提資料に追加 (/search --save)`
* `WelcomeBanner` のモード行に `+WEB` / `-WEB` 状態を併記し、`HelpModal` のコマンド一覧タブ・安全設計タブを新仕様に合わせて更新する。

---

## 4. セキュリティ・プライバシー保護の3層ガードレールまとめ

本設計により、作業エージェントからの直接検索呼び出しを実現しつつ、以下の3層防御によってローカル機密の漏洩を徹底防止する。

| 防御層 | 実施箇所 | 保護メカニズム |
| :--- | :--- | :--- |
| **第1層：AIプロンプト制御** | `app/prompts.py` + `web_search` Docstring | `SearchAgent` がローカル非共有である前提を明示し、①文書・業務情報、②個人・組織の固有名詞、③コード・パス・認証情報の3領域の転記を具体的に禁じ、公開されている一般名称・概念のみによる自己完結質問を強制。 |
| **第2層：プリフライト静的検査** | `core/search.py` (`validate_search_query_safety`) | プロジェクト物理パス・ホームパス、秘密鍵・APIキー等の機密パターン、および原稿・コード貼り付けを防ぐ上限文字数（600文字）超過を機械的に検知して送信前にブロック。 |
| **第3層：常時全文確認 + `-WEB` 遮断** | `app/controller.py` + `ApprovalModal` + `StatusHeader` | `NORMAL` / `EDIT` / `TRUST` の全モードで**送信プロンプト全文（省略なし）**をモーダル表示して人間が最終承認。`TRUST` 放置実行時や機密作業時は `-WEB` ワンクリックでツール自体を完全遮断。 |
| **構造的コンテキスト隔離** | `core/search.py` (`SearchAgent`) | `ChatSession`・`MaterialStore`・ローカル操作ツールへの参照を一切持たず、かつ `search_history` も保持しない完全ステートレス構造により、意図しない過去文脈の混入をゼロ化。 |

---

## 5. テストおよび検証計画

1. **Core 層単体テスト（`tests/test_core_agent_search.py`, `tests/test_core_tools_and_prompts.py`, `tests/test_core_skills_and_dynamic_tools.py`）**:
   * `SearchAgent` がステートレス（連続呼び出しでも過去履歴を送信しない）で動作し、`format_search_result_for_tool()` が暗号化リダイレクト URL を除外してタイトル＋ドメインのみを整形すること。
   * `validate_search_query_safety()` がローカル絶対パス・機密キー形式・文字数超過を正確にブロックし、通常の技術質問を通過させること。
   * `Toolset.enabled` の `True` / `False` 切り替えが `StaticToolProvider` と `DynamicToolsetProvider` の双方で有効ツール一覧およびカタログ出力へ即座に反映されること。
2. **App / Controller 層単体テスト（`tests/test_app_controller_and_commands.py`）**:
   * `parse_slash_command` における `/search on|off`, `/search --save`, `/search <query>`, `/search --clear` の解析検証。
   * `ChatAgent` から `web_search` ツールが呼ばれた際、**`NORMAL`・`EDIT`・`TRUST` のすべての承認モードで必ず `approval_callback` が呼ばれ、かつプロンプト全文が省略されずに渡されること**。
   * 承認で `'n'`（拒否）が返された際、`Project._rejection_pending`（サーキットブレーカー）が発火せず後続のツール操作や `ask_user` が正常に行えること、および `'a'`（Abort）や `cancel_active_execution()` で即座に中断されること。
   * `/search <query>` 実行直後は `MaterialStore` が 0 件のままであり、`save_search_result_to_materials()` 実行時のみ登録・重複判定が行われること。
3. **CLI / TUI 統合テスト（`tests/test_cli_main.py`, `tests/test_tui_app.py`）**:
   * デフォルト起動時に `StatusHeader` の `#hdr-web` が `+WEB`（`.badge-web-on`）で表示され、クリックまたは `/search off` で `-WEB`（`.badge-web-off`）へ切り替わること（`--no-web-search` 指定時は `-WEB` で起動すること）。
   * `SearchTurnWidget` の `[+ 資料に追加]` ボタン押下により作業前提資料へ追加され、ヘッダーの `M:` カウンタが増加すること。

---

## 6. 関連ドキュメントの更新方針（実装完了時）

本設計の実装完了に合わせて、[`docs/DOCUMENTATION_RULE.md`](../DOCUMENTATION_RULE.md) に従い以下のドキュメントを同期更新する。
* 過去の設計書 [`#001`](./001_web_search.md), [`#002`](./002_search_material_integration.md), [`#013`](./013_core_package_refactoring.md), [`#014`](./014_textual_tui_architecture.md) のヘッダーステータス・注記への `#016` 参照追加、および [`docs/README.md`](../README.md) のステータス更新。
* Single Source of Truth である [`manuals/07_ARCHITECTURE_REFERENCE.md`](../../manuals/07_ARCHITECTURE_REFERENCE.md) およびユーザーマニュアル類（`manuals/01_`, `02_`, `03_`, `05_`, `09_`, `README.md`）における `/search`・`+WEB`/`-WEB` 仕様の反映。

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-23 | AI Agent | 設計完了（実装準備中） | 初版作成。`ChatAgent` からステートレス `SearchAgent` を呼び出す `web_search` ツール統合、全承認モード共通の全文常時確認、`+WEB`/`-WEB` ヘッダーバッジ（一律デフォルトON）、および `/search` 単発検索＋任意資料追加の設計を策定。 |
| 2026-11-23 | AI Agent | 設計完了（実装準備中） | コーディング以外の文書作成・企画・一般リサーチ用途にも対応しつつ強い拘束力を維持するため、検索システムプロンプトおよび機密除外ルールを3領域（①文書・業務情報、②個人情報・非公開固有名詞、③コード・環境・認証情報）へ汎用化・拡充。 |
| 2026-11-23 | AI Agent | 実装完了（現行） | Core / App / CLI / TUI の全レイヤー実装および単体・ヘッドレスTUIテスト（全88件合格）を完了し、関連ドキュメントへ反映。 |
