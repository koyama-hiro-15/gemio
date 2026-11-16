# #011 累積トークン量確認および対話セッションリセット機能 設計書

**作成日:** 2026年11月07日  
**最終更新日:** 2026年11月17日  
**ステータス:** 一部更新（#012, #013 参照）  
**対象システム:** `src/gemio/core/agent.py`, `src/gemio/cli/ui.py`, `src/gemio/cli/main.py`, `tests/`  

### 【要約】
AIエージェントとの対話セッション進行に伴うコンテキスト肥大化やAPIトークン消費量をユーザーがリアルタイムに把握し、適切なタイミングで新しい対話セッションへ切り替えられるようにするため、トークン使用量トラッキング機能およびセッションリセット機能を導入します。プロンプトヘッダーでの常時サマリ表示、`/tokens`（`/usage`）コマンドによる詳細内訳・コンテキスト健全性診断パネル表示、および `/clear`（`/reset`, `/new`）コマンドによる対話セッションの即時リセットを提供します。

> **💡 現行仕様に関する注記:** コンテキスト量・累積トークン量の2軸計測、3段階しきい値（5万 / 15万 tokens）、プロンプトヘッダー表示、および `/tokens`・`/clear` コマンドの仕様は現在も有効ですが、内部クラス構成（旧 `AIChatAgent` / 旧 `ui.py`）は後続の [`012_ui_package_refactoring_for_textual.md`](./012_ui_package_refactoring_for_textual.md) および [`013_core_package_refactoring.md`](./013_core_package_refactoring.md)（`TokenUsageTracker`・`ChatSession` 分離）で刷新されています。現行仕様は [`manuals/07_ARCHITECTURE_REFERENCE.md`](../../manuals/07_ARCHITECTURE_REFERENCE.md) を参照してください。

---

## 1. 背景と目的

1. **セッション切り替えタイミングの可視化（Context Rot の防止）**:
   AIエージェントとの対話が長引くと、会話履歴（`chat_history`）やツール実行結果が蓄積して1リクエストあたりの入力トークン数（コンテキスト量）が増大し、推論精度の低下・応答遅延・トークン消費の増大を招きます。現在のコンテキスト量を常時可視化することで、ユーザーが迷わず新しいセッションへ切り替えられる判断材料を提供します。
2. **累積トークン消費量の透明化**:
   エージェントは1回のプロンプト指示の裏で複数回のFunction Callingループを実行します。各APIコールで消費された入力・出力・推論（Thinking）・キャッシュトークンの累積値を正確に集計し、セッション全体のコスト感を把握できるようにします。
3. **CLIを再起動しないシームレスな新セッション移行**:
   コンテキストが膨らんだ際、`/exit` でCLIを終了して再起動する手間なく、現在の動作モード（`--dev` 等）や承認モード（`/edit`, `/trust`）、選択中のAIモデルを維持したまま、コマンド1つ（`/clear`）で新しい対話セッションを開始できるようにします。

---

## 2. 追跡するトークン指標とデータ構造

### 2.1 Gemini API `usage_metadata` からの抽出項目
`google-genai` SDK の `GenerateContentResponse.usage_metadata` から、毎回のAPI応答時に以下のフィールドを安全に取得します。

| APIフィールド名 | 意味 |
| :--- | :--- |
| `prompt_token_count` | 今回のリクエストで送信された入力トークン数（システム指示＋ツール定義＋会話履歴＋作業前提資料） |
| `candidates_token_count` | モデルが生成した出力トークン数（テキスト応答およびFunction Call引数） |
| `thoughts_token_count` | 思考（Thinking）プロセスで消費されたトークン数 |
| `cached_content_token_count` | コンテキストキャッシュにヒットしたトークン数 |
| `tool_use_prompt_token_count` | 組み込みツール（Google Search Grounding等）の実行結果に伴う追加プロンプトトークン数 |
| `total_token_count` | 今回のAPIリクエスト単体での合計トークン数 |

### 2.2 エージェントが管理する2大指標 (`AIChatAgent`)

ユーザーの目的（①セッション切替判断、②総消費量把握）に合わせ、以下の2つの軸で統計を保持します。

1. **現在のコンテキスト量 (`last_prompt_tokens`) — 【セッション切替判断の主指標】**
   - 直近の通常対話（`chat`）API呼び出しにおける `prompt_token_count` を記録します。
   - 会話履歴と作業前提資料が現在どれだけ膨らんでいるかを直接表します。
2. **セッション累積トークン量 (`cumulative_*`) — 【総消費量の指標】**
   - セッション開始（または前回の `/clear`）以降に実行されたすべてのAPI呼び出し（ツール呼び出しループおよび `/search` Web検索を含む）のトークン数を累積加算します。

#### `agent.get_token_usage()` の返却データ構造
```python
{
    # 1. 直近リクエスト（現在のコンテキスト規模）
    "last_prompt_tokens": int,          # 直近の対話リクエストの入力トークン数（コンテキスト量）
    "last_candidates_tokens": int,      # 直近の対話リクエストの出力トークン数
    "last_total_tokens": int,           # 直近の対話リクエストの合計トークン数

    # 2. セッション累積トークン量
    "cumulative_prompt_tokens": int,    # 入力トークン累計
    "cumulative_candidates_tokens": int,# 出力トークン累計
    "cumulative_thoughts_tokens": int,  # 思考（Thinking）トークン累計
    "cumulative_cached_tokens": int,    # キャッシュヒットトークン累計
    "cumulative_tool_use_tokens": int,  # ツール利用プロンプトトークン累計
    "cumulative_total_tokens": int,     # セッション全体の総消費トークン累計

    # 3. API呼び出し回数およびセッション状態
    "chat_api_calls": int,              # 通常対話・ツールループでのAPIリクエスト回数
    "search_api_calls": int,            # Web検索（/search）でのAPIリクエスト回数
    "total_api_calls": int,             # 総APIリクエスト回数
    "chat_history_messages": int,       # 現在の会話履歴要素数 (len(chat_history))
    "search_materials_count": int,      # 保持中の作業前提資料の件数 (len(search_materials))
}
```

### 2.3 コンテキスト健全性のしきい値（3段階ステータス）

`last_prompt_tokens`（現在のコンテキスト量）に基づき、セッション切り替えの目安を3段階で色分け表示します。

| 状態レベル | しきい値 (`last_prompt_tokens`) | 表示カラー | 診断メッセージ |
| :--- | :--- | :--- | :--- |
| **🟢 快適 (`normal`)** | `0 〜 49,999` tokens | 緑 (`green`) | コンテキスト量は十分余裕があります。そのまま対話を継続できます。 |
| **🟡 やや大 (`warning`)** | `50,000 〜 149,999` tokens | 黄 (`yellow`) | 会話履歴が蓄積しています。作業の区切りで `/clear` による新セッションへの切り替えを推奨します。 |
| **🔴 肥大化 (`danger`)** | `150,000` tokens 以上 | 赤 (`red`) | コンテキストが肥大化しています（応答遅延や精度低下の懸念）。`/clear` で新しいセッションに切り替えることを強く推奨します。 |

---

## 3. アーキテクチャ・クラス設計

### 3.1 `AIChatAgent` (`src/gemio/core/agent.py`)

* **初期化とリセット**:
  * `_init_token_usage()` メソッドで内部カウンタ辞書 `self._token_usage` を0で初期化します。
  * `reset_token_usage()` メソッドでトークン統計カウンタをすべて0にリセットします。
  * `reset_session(clear_materials: bool = False) -> dict[str, int]` メソッドを新設:
    * `self.chat_history.clear()` および `self.search_history.clear()` を実行。
    * `clear_materials=True` の場合は `self.search_materials.clear()` も実行。
    * `self.reset_token_usage()` でトークン統計をリセットし、リセット前のメッセージ数・資料数等のサマリ辞書を返します。
* **トークン使用量の自動記録 (`_record_usage_metadata`)**:
  * `_generate_content_with_retry()` に `usage_source: str = "chat"` 引数（`"chat"` または `"search"`）を追加し、`generate_content` 成功直後に `self._record_usage_metadata(response, source=usage_source)` を呼び出します。
  * 単体テスト時のモック（`MagicMock`）や `usage_metadata` が `None` のケースでも例外を起こさないよう、`isinstance(val, int) and not isinstance(val, bool)` を満たす正の整数値のみを安全に抽出します。

### 3.2 UI層 (`src/gemio/cli/ui.py`)

1. **プロンプトヘッダーの拡張 (`format_prompt_header`)**:
   ```python
   def format_prompt_header(
       project: Project,
       token_usage: dict[str, int] | None = None,
   ) -> str:
   ```
   * `token_usage` が渡された場合（CLIメインループ内）、3行目にトークンサマリ行を追加します（`token_usage=None` の場合は従来の表示を維持し、既存テストとの後方互換性を100%担保します）。
   * コンテキスト量（`last_prompt_tokens`）のカラータグは、しきい値に応じて `<ansigreen>`, `<ansiyellow>`, `<ansired>` に動的に変化します。
   * 表示例:
     ```text
     project: /workspaces/gemio
     mode: 基本モード (Standard)  |  approval: 通常承認モード (都度確認)
     tokens: context 12,450 / cumulative 45,820 (calls: 4)
     User > (Esc+Enterで送信):
     ```

2. **トークン使用量詳細パネル (`print_token_usage_panel`)**:
   * `/tokens`（エイリアス: `/usage`）実行時に表示するRichパネル。
   * **セクション1（現在のコンテキスト状態）**:
     * 現在のコンテキストサイズ (`last_prompt_tokens` tokens) ＋ 🟢/🟡/🔴 ステータスバッジ
     * 保持中の会話履歴メッセージ数 (`chat_history_messages` 件)
     * 保持中の作業前提資料 (`search_materials_count` 件)
   * **セクション2（セッション累積トークン内訳テーブル）**:
     * 入力トークン累計 (`cumulative_prompt_tokens`)
     * 出力トークン累計 (`cumulative_candidates_tokens`)
     * 思考（Thinking）トークン累計 (`cumulative_thoughts_tokens`)
     * キャッシュヒット累計 (`cumulative_cached_tokens`)
     * ツール利用トークン累計 (`cumulative_tool_use_tokens`)
     * **累積合計トークン (`cumulative_total_tokens`)**
     * APIリクエスト回数（対話 `chat_api_calls` 回 / 検索 `search_api_calls` 回 / 合計 `total_api_calls` 回）
   * **セクション3（診断メッセージ＆操作ガイド）**:
     * コンテキスト量に応じたアドバイスと、`/clear`（新セッション開始）および `/clear --all`（前提資料も含めて完全クリア）の案内。

3. **ヘルプパネル (`print_help_panel`)・ウェルカムバナーの更新**:
   * コマンド一覧に `/tokens` (`/usage`) および `/clear` (`/reset`, `/new`) を追記します。

### 3.3 CLI制御層 (`src/gemio/cli/main.py` の `GemioCLI`)

* **`dispatch_input` へのコマンド追加**:
  * `/tokens`, `/usage`, `/token` ➔ `self.handle_tokens_command()` を呼び出し、`print_token_usage_panel(self.agent.get_token_usage())` を実行。
  * `/clear`, `/reset`, `/new`（オプション `-a`, `--all` 対応）➔ `self.handle_clear_command(user_input)` を呼び出し。
* **`handle_clear_command(self, user_input: str)` の動作**:
  * 引数に `-a` または `--all` が含まれる場合は `clear_materials=True`、それ以外は `clear_materials=False` で `self.agent.reset_session(clear_materials=...)` を呼び出します。
  * あわせて `self.project.reset_rejection_state()` を呼び出し、承認拒否の保留状態もクリアします。
  * リセット完了パネル（消去した会話メッセージ数、リセット前の累積トークン数、および作業前提資料の保持/消去状態）を表示します。

---

## 4. コマンド体系一覧（追加分）

| コマンド | エイリアス | オプション | 動作内容 |
| :--- | :--- | :--- | :--- |
| **`/tokens`** | `/usage`, `/token` | なし | 現在のコンテキスト量、健全性診断、およびセッション累積トークン内訳パネルを表示します。 |
| **`/clear`** | `/reset`, `/new` | なし | 会話履歴・検索履歴・トークン統計をリセットし、新しい対話セッションを開始します（作業前提資料は維持）。 |
| **`/clear --all`** | `/clear -a`, `/reset -a`, `/new -a` | `-a`, `--all` | 会話履歴・トークン統計に加え、保持中の「作業前提資料」もすべて破棄して完全に初期状態へリセットします。 |

---

## 5. テスト計画

1. **`AIChatAgent` トークン集計・セッションリセット単体テスト (`tests/test_agent_token_usage.py`)**:
   - 通常プロンプト（複数回のツール呼び出しループを含む）における `last_prompt_tokens` の更新と `cumulative_*` の正確な加算検証。
   - `/search` 実行時の `search_api_calls` と累積トークンの加算検証。
   - `usage_metadata` が `None` や一部欠損しているレスポンスに対する安全性検証。
   - `reset_session(clear_materials=False)` と `reset_session(clear_materials=True)` の挙動検証。
2. **CLI表示・コマンドディスパッチ単体テスト (`tests/test_cli_prompt.py`)**:
   - `format_prompt_header(project, token_usage)` におけるトークン表示と3段階カラー（緑・黄・赤）の切り替わり検証。
   - `GemioCLI.dispatch_input` における `/tokens`, `/clear`, `/clear --all` の動作検証。

---

## 6. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-07 | AI Agent | 実装完了 | 初版作成および実装完了。コンテキスト量・累積トークン量の2軸トラッキング、プロンプトヘッダー表示、`/tokens` 詳細パネル、`/clear` セッションリセットを実装・単体テスト通過。 |
| 2026-11-17 | AI Agent | 一部更新（#012, #013 参照） | 設計書の3桁連番管理導入に伴い `011_token_usage_and_session_reset.md` へリネームし、`#012`・`#013` への案内注記を追加。 |
