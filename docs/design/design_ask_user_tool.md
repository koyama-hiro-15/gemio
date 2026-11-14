# ユーザー対話・確認ツール（`ask_user`）機能設計書

**作成日:** 2026年11月06日  
**最終更新日:** 2026年11月07日  
**ステータス:** 実装完了  
**対象システム:** `src/gemio/project/base.py`, `src/gemio/project/developer.py`, `src/gemio/core/agent.py`, `src/gemio/core/prompts.py`, `src/gemio/cli/main.py`, `src/gemio/cli/ui.py`, `tests/test_agent_ask_user.py`  

### 【要約】
AIエージェントが推論・作業ループの途中で、仕様の曖昧さ、複数の実装方針の選択肢、承認拒否後の代替案、またはサンドボックス等の作業環境の不備に直面した際、無茶な推測や強行手段に出ることなく人間に対して質問・確認を行い、判断を仰ぐためのツール `Project.ask_user`（および `AIChatAgent` のプロンプトライフサイクルコールバック連携）を定義します。

---

## 1. 背景と目的

1. **エージェントループ内での双方向コミュニケーションの実現**:
   従来の「プロンプト入力 ➔ ツール連続実行 ➔ 最終テキスト応答」という粗い粒度の一方向ループだけでなく、作業の途中でAIから人間に質問や選択肢を提示し、推論コンテキストを維持したまま方針をすり合わせられるようにする。
2. **作業環境の不備や仕様不明時の「無茶な強行（暴走）」の防止**:
   サンドボックス環境に必要なツール・パッケージがインストールされていない等の環境不備が発覚した際や、要件が曖昧な際に、AIが勝手に環境を改変したり無理なワークアラウンドで強行したりする事故を防ぎ、必ずユーザーに相談する行動規範を確立する。
3. **責務の適切な分離（`Project` への配置と `AIChatAgent` の疎結合維持）**:
   `AIChatAgent` は純粋なAI対話セッションとツール実行ループの制御に専念させ、人間の承認確認（`approval_callback`）やサーキットブレーカー状態（`_rejection_pending`）を管理している `Project`（および `DeveloperProject`）クラスのメソッドとして `ask_user`（および `ask_user_callback`）を実装する。また、プロンプト開始・終了時の状態リセットは `AIChatAgent` の汎用コールバック（`on_prompt_start` / `on_prompt_end`）を介して疎結合に連携する。
4. **承認拒否時のサーキットブレーカーとの自然な調和**:
   変更操作（`write_file` 等）がユーザーに `n`（拒否）された後、AIが `Project.ask_user` を呼び出してユーザーに意図や代替方針を確認した場合は、「ユーザーへの説明・相談が行われた」とみなして `self._rejection_pending = False` によりサーキットブレーカーの保留状態を直接解除し、複雑な状態管理を追加することなく安全に作業を継続できるようにする。

---

## 2. アーキテクチャ・処理フロー設計

```text
[AI (Gemini)]
   │
   ├─ (ケースA) 要件の曖昧さ / 実装方針の選択肢 / サンドボックスの環境不備を発見
   ├─ (ケースB) write_file 等の承認が 'n' (拒否) され、Project._rejection_pending = True となる
   │
   ▼ 方針を確認・相談するため ask_user(question, options, context) を呼び出し
[Project.ask_user] (src/gemio/project/base.py)
   ├─ 1. self._rejection_pending = False にリセット（相談成立によりサーキットブレーカー保留解除）
   ├─ 2. self.ask_user_callback(question, options, context) コールバックを呼び出し
   │       （コールバック未指定時は標準入出力フォールバックを実行）
   │
   ▼
[CLI (cli_ask_user_callback)] (src/gemio/cli/main.py)
   ├─ スピナー (active_status) を一時停止
   ├─ 質問・背景・選択肢 ([1]..[N] + [0] その他（任意の指示） + [a] 作業を強制中止) を Panel 表示
   ├─ ユーザーの入力を受付:
   │    ├─ '1'..'N' : 該当する選択肢の文字列を返却
   │    ├─ '0'      : 自由記述プロンプトを表示し、入力された指示文字列を返却
   │    ├─ 直接テキスト入力 : そのまま自由記述の指示文字列として返却
   │    └─ 'a'      : AgentExecutionAborted を送出し、作業ループを即時安全停止
   └─ 回答文字列を返却 ➔ AIが回答（function_response）を受け取り作業を継続
```

---

## 3. モジュール別詳細設計

### 3.1 `src/gemio/project/base.py` (`Project`) および `src/gemio/project/developer.py` (`DeveloperProject`)

#### (1) `__init__` の拡張と `agent_tools()` への登録
* `Project.__init__` および `DeveloperProject.__init__` の引数に `ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None` を追加し、`self.ask_user_callback` として保持します。
* `Project.agent_tools()` が返すツール関数リストに `self.ask_user` を含めます。

```python
    def __init__(
        self,
        project_path: str | Path,
        require_approval: bool = True,
        approval_callback: Callable[[str], bool | str] | None = None,
        ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None,
    ):
        self.project_path = Path(project_path).resolve()
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.require_approval = require_approval
        self.approval_callback = approval_callback
        self.ask_user_callback = ask_user_callback
        self._rejection_pending: bool = False
```

#### (2) プロンプトライフサイクルハンドラとサーキットブレーカーリセット
`AIChatAgent` のプロンプト開始・終了イベントにバインドして `_rejection_pending` を確実にリセットするため、以下のメソッドを提供します。

```python
    def reset_rejection_state(self) -> None:
        """承認拒否の保留状態（サーキットブレーカー監視フラグ）をリセットします。"""
        self._rejection_pending = False

    def on_prompt_start(self) -> None:
        """プロンプト処理開始時に呼び出されるコールバックハンドラ。"""
        self._rejection_pending = False

    def on_prompt_end(self) -> None:
        """プロンプト処理終了時に呼び出されるコールバックハンドラ。"""
        self._rejection_pending = False
```

#### (3) `ask_user` ツールメソッドの仕様
Google GenAI SDK が関数シグネチャと Docstring からスキーマを自動生成するため、型ヒントと説明文を明確に記述します。

```python
    def ask_user(
        self,
        question: str,
        options: list[str] | None = None,
        context: str = "",
    ) -> str:
        """
        作業中に仕様の曖昧さ、複数の実装方針の選択肢、作業環境の不備（必要なツールの未インストール等）、
        または操作が承認拒否された後の代替方針について、ユーザーに質問・相談し判断や指示を取得します。
        推測や無茶な手段で作業を強行する前にこのツールを使用してください。

        Args:
            question: ユーザーに確認・質問したい具体的な内容。
            options: ユーザーに提示する選択肢の文字列リスト（任意。自由回答のみを求める場合は省略可）。
                     UI側で自動的に「その他（任意の指示を入力）」も選択可能になります。
            context: 質問の背景、現在の分析状況、またはなぜこの確認が必要なのかの説明（任意）。

        Returns:
            ユーザーが選択した選択肢、または入力した回答・指示の文字列。
        """
        # ユーザーに質問・相談を行ったため、承認拒否保留状態（サーキットブレーカー）をリセット
        self._rejection_pending = False

        if self.ask_user_callback is not None:
            return self.ask_user_callback(question, options, context)

        # コールバック未指定時の標準入出力フォールバック処理
        ...
```

---

### 3.2 `src/gemio/core/agent.py` (`AIChatAgent`)

`AIChatAgent` が `Project` の内部属性に直接依存しない疎結合な設計を維持するため、プロンプト処理の開始時・終了時に呼び出される汎用コールバック引数を備えます。

* `__init__` 引数に `on_prompt_start: Callable[[], None] | None = None` および `on_prompt_end: Callable[[], None] | None = None` を追加します。
* `process_prompt` の開始時に `self.on_prompt_start()` を、終了時（`finally` ブロック）に `self.on_prompt_end()` を呼び出します。

```python
    def __init__(
        self,
        tools: list,
        system_instruction: str = "",
        search_instruction: str | None = None,
        model_name: str = DEFAULT_AI_MODEL_NAME,
        max_iterations: int = 10,
        on_iteration_limit: Callable[[], tuple[bool, int]] | None = None,
        on_prompt_start: Callable[[], None] | None = None,
        on_prompt_end: Callable[[], None] | None = None,
        api_key: str | None = None,
    ):
        ...
        self.on_prompt_start = on_prompt_start
        self.on_prompt_end = on_prompt_end
```

---

### 3.3 `src/gemio/cli/main.py` (CLI UIハンドラ)

`cli_approval_callback` や `cli_iteration_limit_callback` と並べて `cli_ask_user_callback` を定義し、`Project` / `DeveloperProject` の初期化時に `ask_user_callback=cli_ask_user_callback` として渡します。また、`AIChatAgent` の初期化時に `on_prompt_start=project.on_prompt_start` および `on_prompt_end=project.on_prompt_end` を渡します。

#### (1) パネル表示レイアウト（`options` 指定ありの場合）
```text
╭─ 💬 Gemioからの確認・質問 ────────────────────────────────────────╮
│ 【背景・状況】                                                    │
│ サンドボックス内で pytest を実行しようとしましたが、              │
│ コマンドが見つかりませんでした (command not found)。              │
│                                                                   │
│ 【質問】                                                          │
│ テストの実行環境について、どのように対応すればよろしいでしょうか？│
│                                                                   │
│ 【選択肢】                                                        │
│   [1] poetry run pytest / uv run pytest 等の仮想環境を確認する    │
│   [2] pip install pytest でインストールを実行する                 │
│   [3] 今回はテスト実行をスキップしてコード修正のみ完了とする      │
│   [0] その他（任意の指示を入力）                                  │
│   [a] 作業を強制中止 (Abort)                                      │
╰───────────────────────────────────────────────────────────────────╯
番号を選択、または直接回答・指示を入力してください: 
```

#### (2) ユーザー入力の解釈ルール
1. **`1` 〜 `N`（選択肢番号）の入力**:
   該当する選択肢文字列 `options[idx - 1]` を返します。
2. **`0`（その他）の入力**:
   `Prompt.ask("[bold cyan]回答・指示を入力してください（[bold red]a[/bold red]で強制中止）[/bold cyan]")` で自由記述を受け付け、その文字列を返します（そこでも `a` / `abort` が入力された場合は強制中止とします）。
3. **選択肢番号以外のテキストの直接入力**:
   ユーザーが番号ではなく直接指示文（例: `"venv配下のpytestを使って実行して"`）を入力した場合は、`0` を選び直させることなくそのまま自由記述の回答としてAIへ返します。
4. **`a` または `abort` の入力**:
   `AgentExecutionAborted("ユーザーによって作業の強制中止（Abort）が選択されました。")` を送出し、エージェントの実行ループを即座に停止します。

---

### 3.4 `src/gemio/core/prompts.py` (システムインストラクションの更新)

#### (1) `DEFAULT_SYSTEM_INSTRUCTION` への追加ルール
```text
【ユーザーへの確認・質問（ask_userツール）に関するルール】
- 作業中に要件や仕様の曖昧さ、複数の妥当な実装方針の選択肢、または判断に迷う点を発見した場合は、推測で作業を強行せず `ask_user` ツールを使用してユーザーに確認・相談すること。
- 必要なツール・ライブラリ・依存関係が不足しているなど、作業環境の不備や制約が発覚した場合は、勝手に環境を改変したり無理な代替手段（無茶なワークアラウンド）で強行したりせず、直ちに `ask_user` ツールで状況を報告し、ユーザーに判断や対応を仰ぐこと。
- ユーザーに選択肢を提示できる場合は、`ask_user` の `options` 引数に具体的な選択肢リストを渡してユーザーが選びやすいように工夫すること。
- 操作の承認（Human-in-the-loop）が拒否された場合、別の方向性や修正案についてそのまま作業を続ける前に `ask_user` ツールでユーザーの意図や希望する方針を確認してもよい。
- ただし、コードを読めば分かる事実や些細な事柄で過剰に質問を繰り返すことは避け、事前に必要なファイル調査を行ってから要点を絞って質問すること。
```

#### (2) `DEVELOPER_MODE_INSTRUCTION_ADDON` への追加・補強ルール
```text
- サンドボックス環境に必要なコマンド・ランタイム・パッケージ等がインストールされていないなど、作業環境の不備が発覚した場合は、無断でパッケージのインストールやシステム設定の変更等の無茶な手段に出ず、`ask_user` ツールを用いてユーザーに状況を伝え、インストールしてよいか・どう対処すべきかを相談してください。
- テストが失敗した際、設定ファイルを書き換えてテスト自体を除外・無効化しようとする行為は禁止です。必ずコード側の原因を分析して修正するか、解決困難な場合は `ask_user` ツール等でユーザーに相談してください。
```

---

## 4. テスト計画 (`tests/test_agent_ask_user.py`)

1. **`ask_user` ツールの登録と基本動作検証**:
   - `Project.agent_tools()` に `ask_user`（`proj.ask_user`）が含まれること。
   - `Project` に `ask_user_callback` が設定されている場合、`question`, `options`, `context` が正しく渡され、コールバックの戻り値が返ること。
   - コールバック未設定時の標準入出力フォールバックにおいて、番号選択・`0`（その他自由入力）・直接テキスト入力・`a`（Abort）がそれぞれ意図通り動作すること。
2. **承認拒否後のサーキットブレーカー解除連携テスト**:
   - `Project` の変更操作（`write_file`）が拒否（`n`）されて `project._rejection_pending == True` となった状態で、`project.ask_user(...)` を実行すると `project._rejection_pending` が `False` にリセットされること。
   - その直後に再度 `write_file` を呼び出しても `AgentExecutionAborted`（サーキットブレーカー）が発生せず、正常に承認確認が行えること。
   - また、`AIChatAgent` の `on_prompt_start` / `on_prompt_end` コールバック（`project.on_prompt_start` / `project.on_prompt_end`）経由でも `project._rejection_pending` が `False` にリセットされること。
3. **強制中止（Abort）時のループ停止と履歴整合性テスト**:
   - エージェントループ内で `ask_user` が呼ばれ、ユーザーが `a`（Abort）を選択して `AgentExecutionAborted` が送出された際、ループが安全に終了し、`chat_history` の `function_call` と `function_response` のペア整合性が保たれること。
4. **プロンプト構築検証**:
   - `build_system_instruction(dev_mode=False)` および `build_system_instruction(dev_mode=True)` に `ask_user` や環境不備時の相談に関するルールが含まれていること。

---

## 5. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-06 | AI Agent | 実装完了 | 初版作成および実装完了。`ask_user` ツールの仕様、サーキットブレーカー連携、CLI UI、システムプロンプト指針を定義・実装。 |
| 2026-11-06 | AI Agent | 実装完了 | `ask_user` メソッドの配置を `AIChatAgent` から `Project` (`ask_user_callback`) へ移設し、`AIChatAgent` 側は `on_prompt_start` / `on_prompt_end` コールバックによる疎結合設計へ変更した現行仕様に合わせて設計書を更新。 |
| 2026-11-07 | AI Agent | 実装完了 | `prompt_ask_user_response` (`src/gemio/cli/ui.py`) にて、`\\[a]` のRichマークアップタグ誤認防止エスケープおよび `question`・`options`・`context` のMarkdownレンダリング（`Table.grid` + `Markdown`）を追加。 |
