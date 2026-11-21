# #015 動的モデルカタログ・スペック取得と Thinking（思考ログ）統合 設計書

**作成日:** 2026年11月21日  
**最終更新日:** 2026年11月21日  
**ステータス:** 実装完了（現行）  
**対象システム:** `src/gemio/core/` (`models.py`, `client.py`, `agent.py`), `src/gemio/app/controller.py`, `src/gemio/tui/` (`screens/model.py`, `widgets/turn.py`, `commands_palette.py`)  

### 【要約】
従来の静的なモデル名プリセット管理を刷新し、`google-genai` SDK の `models.list()` / `models.get()` を活用した**動的モデルカタログおよびモデルスペック（Thinking対応可否・最大入出力トークン上限等）の取得・キャッシュ基盤**を導入します。あわせて、Thinking 対応モデルでのみ `ThinkingConfig(include_thoughts=True)` を自動有効化し、推論過程（思考ログ）を TUI の折りたたみログ（`ActivityGroup`）へリアルタイム表示する仕組みを統合します。

---

## 1. 背景と解決すべき課題

1. **静的モデル定義の保守コストとスペック情報の欠如**
   * 従来の `src/gemio/core/models.py` は `MODEL_PRESETS: list[tuple[str, str]]`（モデルIDと説明文のみ）をハードコードしており、新モデルの追加・プレビューモデルの更新のたびにコード改修が必要だった。
   * また、各モデルが **Thinking（思考機能）に対応しているか**、**最大入力コンテキスト長（`input_token_limit`）がいくつか**といったスペック情報を保持するデータ構造が存在しなかった。
2. **思考ログ（Thinking）の未有効化と非対応モデルへの配慮**
   * `ChatAgent` にはレスポンスから思考パート（`part.thought == True`）を抽出する実装があったものの、リクエスト時の生成設定に `ThinkingConfig(include_thoughts=True)` を付与していなかったため、TUI 上に思考ログが表示されていなかった。
   * 一方で、すべてのモデルに対して無条件に `ThinkingConfig` を送信すると、Thinking 非対応モデルや一部のカスタム・旧世代モデルで API エラー（HTTP 400 等）を引き起こすリスクがある。
3. **単発対応による二度手間の回避**
   * Thinking 可否判定のためだけに専用の仮キャッシュを作ると、将来「動的な利用可能モデル一覧取得」や「コンテキスト上限表示」を導入する際にデータ構造・通信層・TUI モーダルを二重に改修することになるため、モデル関連基盤を一括で刷新する。

---

## 2. アーキテクチャ方針とレイヤー別責務

既存の4層アーキテクチャおよび依存方向ルール（[`manuals/07_ARCHITECTURE_REFERENCE.md`](../../manuals/07_ARCHITECTURE_REFERENCE.md)）を厳守し、以下のように責務を分担する。

| レイヤー / モジュール | 主な役割・責務 |
| :--- | :--- |
| **`core/models.py`**<br>*(外部SDK非依存)* | 不変データクラス `ModelInfo` の定義、モデルID正規化（`models/` 除去）、ダックタイピングによるテキスト生成モデル抽出判定・Thinking対応判定、オフライン/テスト時用のフォールバックカタログ生成、`AgentLogCategory.THOUGHT` 定数定義。 |
| **`core/client.py`**<br>(`GenAIClient`) | `client.models.get()` / `client.models.list()` の呼び出し、`v1beta` エンドポイント優先参照とフォールバック、セッション内の `ModelInfo` キャッシュ管理、`supports_thinking()` 判定および万一の Thinking 設定拒否時の自動フォールバック。 |
| **`core/agent.py`**<br>(`ChatAgent`) | `client.supports_thinking()` に基づく `ThinkingConfig(include_thoughts=True)` の動的付与、イテレーションごとの思考テキスト抽出と `AgentLogCategory.THOUGHT` での逐次ログ送出。 |
| **`app/controller.py`**<br>(`GemioController`) | TUI 層向けにキャッシュ済みモデル一覧の即時取得（`get_cached_models()`）およびワーカースレッドからの最新モデル一覧取得（`fetch_available_models()`）・現在モデルのスペック参照 API を公開。 |
| **`tui/`**<br>(`ModelSelectModal`, `TurnWidget`, `GemioCommandProvider`) | モデル選択モーダルでのスペック表示（`[Think]` バッジ・最大コンテキスト長・非同期自動更新・手動再取得 `R`）、コマンドパレットへの動的モデル公開、および `ActivityGroup` でのリアルタイム思考ログ表示。 |

---

## 3. データ構造とモデル抽出・判定仕様

### 3.1 `ModelInfo` 不変データクラス（`src/gemio/core/models.py`）

`core/models.py` は `google-genai` SDK をインポートしないポリシーを維持したまま、モデルのスペックを表現する `@dataclass(frozen=True)` として `ModelInfo` を定義する。

```python
@dataclass(frozen=True)
class ModelInfo:
    model_id: str                           # 正規化済みモデルID（例: "gemini-2.5-flash"）
    display_name: str = ""                  # 表示名（例: "Gemini 2.5 Flash"）
    description: str = ""                   # モデルの説明文
    supports_thinking: bool = False         # Thinking（思考機能）対応可否
    input_token_limit: int | None = None    # 最大入力トークン上限（例: 1048576）
    output_token_limit: int | None = None   # 最大出力トークン上限（例: 65536）
    supported_actions: tuple[str, ...] = () # 対応アクション（例: ("generateContent", ...)）
```

### 3.2 テキスト生成モデルの抽出と Thinking 判定ルール

技術調査資料（[`docs/tech_docs/google_genai_models_reference.md`](../tech_docs/google_genai_models_reference.md)）に基づき、以下の基準で判定を行う。

1. **モデルIDの正規化 (`normalize_model_id`)**:
   * API が返すリソース名 `'models/gemini-...'` または `'publishers/google/models/...'` からプレフィックスを除去し、純粋なモデルID（`'gemini-...'`）へ統一する。
2. **テキスト生成対応モデルの抽出 (`is_text_generation_model`)**:
   * `supported_actions` に `"generateContent"` が含まれていることを必須条件とする（埋め込み専用モデル等を除外）。
   * チャット対話用途に適さない音声合成特化（末尾 `-tts`）および画像生成特化（`-image` を含む）モデルを既定で除外する。
3. **Thinking 対応可否の厳密判定 (`is_thinking_supported`)**:
   * `getattr(sdk_model, "thinking", None) is True` により厳密な `bool` 判定を行う。
   * `None` や `False` に加え、単体テスト時の未設定 `MagicMock` オブジェクトに対しても誤って `True` と判定しない安全な設計とする。
4. **短縮エイリアスとフォールバックカタログの維持**:
   * CLI 引数（`--model flash`）やコマンド手入力（`/model lite`）の利便性を保つため、`MODEL_ALIASES` / `resolve_model_name()` は引き続き維持する。
   * API キー未入力状態（コンテナ起動直後等）、オフライン環境、およびモックテスト環境でも TUI が常に動作するよう、`MODEL_PRESETS` から構築する `get_fallback_model_catalog()` を備える。

---

## 4. `GenAIClient` のカタログキャッシュと Thinking 実行制御

### 4.1 セッション内キャッシュと `v1beta` 優先取得
* **`get_model_info(model=None, *, force_refresh=False) -> ModelInfo | None`**:
  1. エイリアス解決・正規化後のモデルIDが `_model_info_cache` に存在すれば即時返却する（通信コスト 0）。
  2. 未キャッシュ時は `HttpOptions(api_version="v1beta")` を指定して `models.get()` を呼び出す（`gemini-flash-latest` 等のエイリアスやプレビューモデルを確実に解決するため。SDK `3.x` 以降のデフォルト `v1` 化にも備える）。万一失敗した場合はデフォルト設定での `models.get()` を1回試行する。
  3. 有効なモデルメタデータ（`name` が非空文字列）が取得できた場合のみ `ModelInfo` へ変換してキャッシュに保存する。一時的なネットワークエラー時はキャッシュを汚染せず `None` を返す。
* **`list_available_models(*, force_refresh=False) -> list[ModelInfo]`**:
  1. `_cached_model_list` が存在し `force_refresh=False` であればキャッシュのコピーを返す。
  2. `models.list(config={"page_size": 100, "http_options": {"api_version": "v1beta"}})` で全ページを走査し、`is_text_generation_model()` を満たすモデルを抽出する。
  3. 取得した各 `ModelInfo` は個別の `_model_info_cache` にも同時登録する。これにより、一覧取得後にどのモデルへ切り替えても追加の `models.get()` リクエストが発生しない。
  4. デフォルトモデル（`gemini-flash-latest`）や主要モデルが先頭付近に来るよう整列して返却する。取得失敗時や該当0件時は `get_fallback_model_catalog()` を返す。

### 4.2 安全な Thinking 有効化と2重フォールバック
1. `ChatAgent._build_generation_config()` は `self.client.supports_thinking()` を参照し、`True` の場合のみ `"thinking_config": types.ThinkingConfig(include_thoughts=True)` を付与する。
2. **実行時セーフティネット**: 万一メタデータ上は `thinking=True` でも特定のエンドポイント制約やツール併用条件により API が `400` 系の Thinking 関連エラーを返した場合、`GenAIClient` は該当モデルIDを `_thinking_disabled_models` セットに記録し、直ちに `thinking_config` を除去して再試行する。これにより思考ログ設定が原因で本編の対話が失敗することを完全に防ぐ。

### 4.3 思考ログのリアルタイム TUI 表示
* `ChatAgent._execute_agent_loop()` の各イテレーションでレスポンスから思考テキストが抽出された際、`turn_thoughts` リストへの蓄積と同時に `AgentLogCategory.THOUGHT` カテゴリで `on_log_append` コールバックへ即時送出する。
* TUI の `TurnWidget` / `ActivityGroup` は `AgentLogCategory.THOUGHT` を受信した時点で折りたたみタイトル（例: `Tools (2 calls: ...) | Thinking (1)`）と展開内容（`[Thinking #1] ...`）をリアルタイム更新する。

---

## 5. TUI モデル選択モーダル（`ModelSelectModal`）の動的化

1. **ノンブロッキング表示＋バックグラウンド自動更新**:
   * `F2` または `/model` で `ModelSelectModal` を開いた瞬間は、`controller.get_cached_models()`（取得済みキャッシュ、未取得ならフォールバックプリセット）を即座に描画し、UI フリーズをゼロにする。
   * API からの一覧が未取得の場合はモーダルマウント時にバックグラウンドワーカー（`@work(thread=True)`）で `controller.fetch_available_models()` を自動実行し、完了次第 `OptionList` を最新カタログへシームレスに更新する（`R` キーまたは「一覧更新 (R)」ボタンで強制再取得も可能）。
2. **リッチなモデルスペック表示**:
   * 各選択肢に **現在選択中マーカー (`*`)**、**モデルID**、**Thinking 対応バッジ (`[Think]`)**、**最大入力トークン上限（例: `Ctx:1M`）**、および**表示名/説明**をコンパクトに表示する。

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-21 | AI Agent | 実装完了（現行） | 初版作成。`models.list()` / `models.get()` による動的モデルカタログ・スペックキャッシュ基盤（`ModelInfo` / `GenAIClient`）、Thinking 自動有効化・リアルタイム思考ログ表示、および `ModelSelectModal` 動的更新の設計を策定。 |
