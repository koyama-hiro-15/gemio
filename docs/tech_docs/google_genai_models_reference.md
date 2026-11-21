# 🤖 Google GenAI SDK モデル情報取得・機能判定リファレンス（`models.list` / `models.get`）

**作成日:** 2026年11月21日  
**最終更新日:** 2026年11月21日  
**ステータス:** 技術資料  
**対象システム:** `src/gemio/core/`（`google-genai` SDK 通信・モデル管理基盤）  

### 【要約】
本ドキュメントは、Python向け公式SDK `google-genai` における `client.models.list()` および `client.models.get()` の仕様・取得可能なメタデータ項目を整理し、特に実務で頻出する**「テキスト生成対応モデルの抽出方法」**と**「Thinking（思考機能）対応モデルの判定方法」**を実践的なサンプルコードと共にまとめた技術資料です。

---

## 目次
1. [`client.models.list()` と `client.models.get()` の概要](#1-clientmodelslist-と-clientmodelsget-の概要)
2. [`types.Model` で取得できる主要メタデータ一覧](#2-typesmodel-で取得できる主要メタデータ一覧)
3. [テキスト生成モデルの抽出方法と Thinking 対応の判定方法](#3-テキスト生成モデルの抽出方法と-thinking-対応の判定方法)
4. [実践サンプルコード集（`google-genai` SDK）](#4-実践サンプルコード集google-genai-sdk)
5. [利用・実装時の注意点](#5-利用実装時の注意点)
6. [更新履歴](#更新履歴)

---

## 1. `client.models.list()` と `client.models.get()` の概要

`google-genai` SDK（旧 `google-generativeai` の後継ライブラリ）では、利用可能なモデルの一覧走査や各モデルのスペック・制限値・対応機能の確認を行うために `models.list()` と `models.get()` が提供されています。

* **同期 / 非同期の両対応**: 同期クライアント（`client.models.*`）と非同期クライアント（`client.aio.models.*`）の双方で利用可能です。
* **共通インターフェース**: **Gemini Developer API（Google AI Studio）** と **Gemini Enterprise Agent Platform（旧 Vertex AI）** の両バックエンドに対して同じメソッド体系でアクセスできます。

### 1.1 `client.models.list()` — モデル一覧の取得

利用可能な**ベースモデル（基盤モデル）**、または自身が作成した**ファインチューニング済みモデル（Tuned Models）**の一覧をページネーション形式で取得します。

```python
pager = client.models.list(*, config=None)
```

`config` 引数には `google.genai.types.ListModelsConfig` オブジェクト、または同等の辞書（`dict`）を指定できます。

| `config` の設定項目 | 型 | 説明 |
| :--- | :--- | :--- |
| **`query_base`** | `bool` | **`True`（デフォルト）**: 利用可能なベースモデル一覧を取得します。<br>**`False`**: ファインチューニング済みモデル（Tuned Models）一覧を取得します。 |
| **`page_size`** | `int` | 1回のAPIリクエスト（1ページ）あたりに取得する最大モデル件数を指定します。 |
| **`page_token`** | `str` | 特定のページから再開する場合のページトークンを指定します。 |
| **`filter`** | `str` | 絞り込み用のフィルタ文字列を指定します（主に Vertex AI / Enterprise 環境やチューニング済みモデルの検索で使用）。 |
| **`http_options`** | `HttpOptions` | このリクエスト単体でAPIバージョン（`v1` / `v1beta` 等）、タイムアウト、カスタムヘッダー等を上書きする場合に指定します。 |

#### 戻り値（`Pager[types.Model]` / `AsyncPager[types.Model]`）の扱い方
単なる `list` ではなく、ページネーション管理機能を持つ `Pager` オブジェクトが返ります。
* **自動ページ送り（全件ループ）**: `for model in client.models.list():` のようにイテレートすると、次ページが存在する場合はSDKが裏側で自動的に次ページを取得し、全件を順番に走査できます。
* **手動ページ制御・インデックス参照**:
  * `pager[0]` のように現在ページ内の要素へインデックスアクセスが可能です。
  * `pager.page_size` で設定されたページサイズを参照できます。
  * `pager.next_page()`（非同期では `await async_pager.next_page()`）で手動ページ送りが可能です。

### 1.2 `client.models.get()` — 特定モデルの詳細情報取得

特定のモデル1件を指定して、コンテキスト長の上限、出力トークン上限、対応アクション、Thinking対応可否などのメタデータを直接取得します。

```python
model_info = client.models.get(*, model, config=None)
```

| 引数 | 型 | 必須 | 説明 |
| :--- | :--- | :---: | :--- |
| **`model`** | `str` | 必須 | 取得したいモデルのIDまたはリソース名を指定します。<br>例: `'gemini-2.5-flash'`、`'models/gemini-2.5-flash'`、またはチューニング済みモデル名。 |
| **`config`** | `GetModelConfig` / `dict` | 任意 | `http_options` を指定してAPIバージョンやタイムアウト等をリクエスト単位で変更できます。 |

#### 戻り値（`google.genai.types.Model`）
* Pydantic の `BaseModel` を継承した `types.Model` オブジェクトが返ります。
* 属性アクセスだけでなく、`model_info.model_dump()` による辞書変換や `model_info.model_dump_json()` によるJSON文字列化もそのまま利用できます。

---

## 2. `types.Model` で取得できる主要メタデータ一覧

`models.list()` の各要素および `models.get()` の戻り値である `types.Model` オブジェクトには、以下の主要フィールドが含まれます（APIバックエンドやモデル種別によって未提供の項目は `None` になります）。

### ① 基本識別情報

| 属性名 | 型 | 内容 |
| :--- | :--- | :--- |
| **`name`** | `str \| None` | モデルのリソース名。<br>・Gemini Developer API: `'models/gemini-...'` 形式<br>・Vertex AI / Enterprise: `'publishers/google/models/...'` 形式など |
| **`display_name`** | `str \| None` | 人間が読みやすいモデルの表示名（例: `"Gemini 2.5 Flash"`）。 |
| **`description`** | `str \| None` | モデルの特徴やリリース情報などの説明文。 |
| **`version`** | `str \| None` | モデルのバージョン識別子（例: `'001'`, `'default'` など）。 |

### ② トークン上限・機能サポート情報（抽出・判定で主に使用）

| 属性名 | 型 | 内容 |
| :--- | :--- | :--- |
| **`input_token_limit`** | `int \| None` | **最大入力トークン数**（コンテキストウィンドウの上限。例: `1048576`）。 |
| **`output_token_limit`** | `int \| None` | **最大出力トークン数**（例: `65536` や `8192`）。 |
| **`supported_actions`** | `list[str] \| None` | そのモデルが対応しているAPIメソッド名のリスト。<br>例: `['generateContent', 'countTokens', 'createCachedContent', 'batchGenerateContent']` や埋め込み用の `'embedContent'` など。<br>※旧SDK（`google-generativeai`）の `supported_generation_methods` に相当します。 |
| **`thinking`** | `bool \| None` | モデルが思考機能（Thinking）をサポートしているかどうかを示す真偽値（`True` / `False` / `None`）。 |

### ③ デフォルト生成パラメータ・上限値

| 属性名 | 型 | 内容 |
| :--- | :--- | :--- |
| **`temperature`** | `float \| None` | デフォルトの `temperature`（ランダム性）の値。 |
| **`max_temperature`** | `float \| None` | そのモデルで指定可能な `temperature` の最大値（例: `2.0`）。 |
| **`top_p`** | `float \| None` | Nucleus sampling（`top_p`）のデフォルト値。 |
| **`top_k`** | `int \| None` | Top-k sampling（`top_k`）のデフォルト値。`None` の場合は `top_k` 指定に非対応であることを示します。 |

### ④ ファインチューニング・デプロイ関連情報

| 属性名 | 型 | 内容 |
| :--- | :--- | :--- |
| **`tuned_model_info`** | `TunedModelInfo \| None` | チューニング済みモデルの情報（`base_model`, `create_time`, `update_time` を保持）。 |
| **`endpoints`** | `list[Endpoint] \| None` | デプロイ済みエンドポイントのリスト（各要素は `name`, `deployed_model_id` を保持）。 |
| **`default_checkpoint_id`** | `str \| None` | チューニング済みモデルのデフォルトのチェックポイントID。 |
| **`checkpoints`** | `list[Checkpoint] \| None` | 利用可能なチェックポイントのリスト（各要素は `checkpoint_id`, `epoch`, `step` を保持）。 |
| **`labels`** | `dict[str, str] \| None` | リソースに付与されたカスタムラベル（Key-Value辞書）。 |

---

## 3. テキスト生成モデルの抽出方法と Thinking 対応の判定方法

### 3.1 テキスト生成モデルの抽出方法

`models.list()` が返す一覧には、テキスト生成モデルだけでなくベクトル埋め込み（Embedding）専用モデルやメディア生成専用モデルも含まれます。テキスト生成（コンテンツ生成）に対応したモデルを抽出するには、以下の2段階のチェックを組み合わせます。

1. **`supported_actions` に `"generateContent"` が含まれているかを確認する（必須）**
   * `model.supported_actions` は `None` になる可能性があるため、`actions = model.supported_actions or []` とした上で `"generateContent" in actions` を判定します。
   * これにより、`embedContent` 専用の埋め込みモデルや `predict` 系の専用モデルを除外できます。
2. **音声合成（TTS）・画像生成特化モデルをモデル名で除外する（任意・推奨）**
   * 一部のTTS（音声合成）モデルや画像生成モデルも `"generateContent"` アクションに対応している場合があります。
   * チャットや一般的なテキスト応答モデルのみに絞り込みたい場合は、モデルIDの末尾 `-tts` や `-image` を含むモデル名を除外すると確実です。

### 3.2 モデルが Thinking 対応かどうかの判定方法

モデルが思考・推論機能（Thinking）をサポートしているかどうかは、`types.Model` の **`thinking` 属性**で直接判定できます。

* **判定式**: `bool(model.thinking)` または `model.thinking is True`
* `model.thinking` の型は `bool | None` であり、Thinking対応モデルでは `True`、非対応モデルでは `False` または `None` となります。
* `bool(model.thinking)` を使うことで、`None` と `False` の両方を安全に `False`（非対応）として扱えます。

---

## 4. 実践サンプルコード集（`google-genai` SDK）

事前に `google-genai` をインストールし、環境変数 `GEMINI_API_KEY` を設定しておきます。

```bash
pip install -U google-genai
export GEMINI_API_KEY="your-api-key"
```

### 4.1 判定ロジックの共通ヘルパー関数例

`models.list()` と `models.get()` の両方で共通利用できる判定関数の実装例です。

```python
from google.genai import types


def is_text_generation_model(
    model: types.Model,
    *,
    exclude_specialized_media: bool = True,
) -> bool:
    """モデルがテキスト生成（generateContent）に対応しているかを判定します。"""
    actions = model.supported_actions or []
    if "generateContent" not in actions:
        return False

    if exclude_specialized_media:
        model_id = (model.name or "").removeprefix("models/").lower()
        # 音声合成(TTS)や画像生成特化モデルを除外したい場合の補助フィルタ
        if model_id.endswith("-tts") or "-image" in model_id:
            return False

    return True


def is_thinking_supported(model: types.Model) -> bool:
    """モデルが Thinking（思考機能）に対応しているかを判定します。"""
    return bool(model.thinking)
```

### 4.2 `client.models.list()` でテキスト生成モデルを抽出し Thinking 対応状況を一覧表示する

全ベースモデルを走査し、テキスト生成対応モデルのみを抽出して Thinking 可否・入力トークン上限を表示するサンプルです。

```python
from google import genai

with genai.Client() as client:
    text_models: list[tuple[str, bool, int | None]] = []

    # page_size を指定してページネーション込みで全モデルを走査
    for model in client.models.list(config={"page_size": 100}):
        actions = model.supported_actions or []

        # 1. コンテンツ（テキスト）生成に対応しているモデルを抽出
        if "generateContent" not in actions:
            continue

        model_id = (model.name or "").removeprefix("models/")

        # （任意）音声合成・画像生成特化モデルを名前で除外
        if model_id.endswith("-tts") or "-image" in model_id:
            continue

        # 2. Thinking対応かどうかを判定（model.thinking は bool | None）
        supports_thinking = bool(model.thinking)

        text_models.append((model_id, supports_thinking, model.input_token_limit))

# 結果を見やすく整形して出力
print(f"{'Model ID':<38} | {'Thinking対応':<14} | {'入力トークン上限'}")
print("-" * 75)
for model_id, supports_thinking, input_limit in sorted(text_models):
    status = "対応 (True)" if supports_thinking else "非対応 (False)"
    limit_str = f"{input_limit:,}" if input_limit is not None else "N/A"
    print(f"{model_id:<38} | {status:<14} | {limit_str}")
```

### 4.3 `client.models.get()` で特定モデル1件の対応機能・スペックを検査する

ユーザーが指定したモデル名について、呼び出し前に「テキスト生成に使えるか」「Thinking 設定（`ThinkingConfig` 等）を渡せるか」をピンポイントで確認するサンプルです。

```python
from google import genai
from google.genai import types


def inspect_model_capabilities(model_name: str, api_version: str | None = None) -> None:
    """指定したモデルのテキスト生成対応・Thinking対応・上限値を確認して表示します。"""
    http_opts = types.HttpOptions(api_version=api_version) if api_version else None

    with genai.Client(http_options=http_opts) as client:
        model = client.models.get(model=model_name)

        actions = model.supported_actions or []
        can_generate_text = "generateContent" in actions
        can_think = bool(model.thinking)

        print(f"=== モデル詳細検査: {model_name} ===")
        print(f"リソース名       : {model.name}")
        print(f"表示名           : {model.display_name}")
        print(f"テキスト生成対応 : {can_generate_text} (actions: {actions})")
        print(f"Thinking対応     : {can_think} (raw value: {model.thinking})")
        print(f"入力トークン上限 : {model.input_token_limit}")
        print(f"出力トークン上限 : {model.output_token_limit}")
        print(
            f"デフォルトTemp   : {model.temperature} "
            f"(最大: {model.max_temperature})"
        )


if __name__ == "__main__":
    # 安定版モデルの確認
    inspect_model_capabilities("gemini-2.5-flash")

    # gemini-flash-latest 等のエイリアスやプレビューモデルを確認する場合は v1beta を指定
    inspect_model_capabilities("gemini-flash-latest", api_version="v1beta")
```

### 4.4 非同期クライアント（`client.aio.models`）での抽出・判定例

Textual TUI や `asyncio` ベースのアプリケーション内でイベントループをブロックせずにモデル情報を取得する場合の実装例です。

```python
import asyncio
from google import genai


async def list_thinking_text_models_async() -> list[str]:
    """非同期で『テキスト生成かつThinking対応』のモデルID一覧を取得します。"""
    matched_models: list[str] = []

    async with genai.Client().aio as aclient:
        # 単一モデルの非同期取得例
        single_info = await aclient.models.get(model="gemini-2.5-flash")
        print(
            f"[単一確認] {single_info.display_name}: "
            f"thinking={bool(single_info.thinking)}"
        )

        # モデル一覧の非同期イテレーション例
        pager = await aclient.models.list(config={"page_size": 100})
        async for model in pager:
            actions = model.supported_actions or []
            model_id = (model.name or "").removeprefix("models/")

            if "generateContent" in actions and bool(model.thinking):
                if not (model_id.endswith("-tts") or "-image" in model_id):
                    matched_models.append(model_id)

    return sorted(matched_models)


if __name__ == "__main__":
    models = asyncio.run(list_thinking_text_models_async())
    print("Thinking対応テキスト生成モデル一覧:", models)
```

---

## 5. 利用・実装時の注意点

1. **APIバージョン（`v1` と `v1beta`）による取得結果の違い**
   * `google-genai` SDK のメジャーバージョン `3.x` 以降では、Gemini Developer API のデフォルトAPIバージョンが `v1beta` から **`v1`** に変更されています。
   * プレビュー段階のモデルや `gemini-flash-latest` などの一部エイリアスは `v1beta` エンドポイントのみに存在する場合があります。それらを `list()` や `get()` で漏れなく参照したい場合は、以下のように `HttpOptions(api_version="v1beta")` を指定してください。
     ```python
     from google import genai
     from google.genai import types

     # クライアント全体で v1beta を使用する場合
     client = genai.Client(http_options=types.HttpOptions(api_version="v1beta"))

     # または特定のリクエスト単位で v1beta を指定する場合
     pager = client.models.list(
         config=types.ListModelsConfig(
             http_options=types.HttpOptions(api_version="v1beta")
         )
     )
     ```
2. **Gemini Developer API と Vertex AI（Enterprise）での返却メタデータの差異**
   * Gemini Developer API（APIキー認証）では、ベースモデルに対して `supported_actions`、`thinking`、`input_token_limit`、`temperature` 等が詳細に返却されます。
   * 一方、Vertex AI バックエンド（`vertexai=True` 等）経由で `list()` / `get()` を呼び出した場合、バックエンドAPI仕様の違いにより一部フィールドが `None` になることがあります。コード実装時は必ず `model.supported_actions or []` や `bool(model.thinking)` のように `None` 安全な判定を行ってください。
3. **`types.Model` では取得できない情報**
   * モデルの総パラメータ数（非公開）や、モデルの提供終了予定日（Deprecation / Retirement date）を表すフィールドは `types.Model` には含まれていません。これらは公式ドキュメントのリリースノートや Deprecations ページで確認する必要があります。

---

## 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-21 | AI Agent | 技術資料 | 初版作成。`google-genai` SDK の `models.list()` / `models.get()` の仕様、テキスト生成モデル抽出および Thinking 対応判定の方法・サンプルコードを整理。 |
