# 🧩 Gemio Skills & カスタムツール拡張マニュアル

**作成日:** 2026年11月14日  
**最終更新日:** 2026年11月14日  
**ステータス:** 運用中  
**対象システム:** Skills管理・拡張ツール・動的ツールセット基盤 (`src/gemio/core/skills/`, `src/gemio/core/tools/`, `src/gemio/core/prompts.py`, `src/gemio/project/base.py`, `src/gemio/cli/main.py`)  

### 【要約】
本マニュアルは、対象プロジェクト直下の `.gemio/` ディレクトリを使用して、プロジェクト固有の専門ワークフロー・規約を定義する **Skills（`.gemio/skills/`）**、独自のPython関数をAIツールとして追加する **カスタムツール（`.gemio/tools/`）**、およびトークン消費を抑えて必要なツールだけをオンデマンド有効化する実験的機能 **動的ツールセット（`--dynamic-tools`）** の使い方とセキュリティ仕様を解説するガイドです。

---

## 目次
1. [拡張機能の全体像と `.gemio/` ディレクトリ構成](#1-拡張機能の全体像と-gemio-ディレクトリ構成)
2. [Skills（専門手順・規約の段階的開示）](#2-skills専門手順規約の段階的開示)
   - [2.1 Skillsの仕組み（Progressive Disclosure）](#21-skillsの仕組みprogressive-disclosure)
   - [2.2 `SKILL.md` の配置ルールと作成方法](#22-skillmd-の配置ルールと作成方法)
   - [2.3 フロントマター仕様と実践テンプレート](#23-フロントマター仕様と実践テンプレート)
3. [カスタムツール（プロジェクト固有ツールの追加）](#3-カスタムツールプロジェクト固有ツールの追加)
   - [3.1 カスタムツールの配置ルールと読み込み仕様](#31-カスタムツールの配置ルールと読み込み仕様)
   - [3.2 3つの定義スタイル（シンプル形式 / Toolset形式）](#32-3つの定義スタイルシンプル形式--toolset形式)
   - [3.3 AIが使いやすいツール関数（型ヒント・Docstring）の書き方](#33-aiが使いやすいツール関数型ヒントdocstringの書き方)
4. [動的ツールセット機能（`--dynamic-tools` / Experimental）](#4-動的ツールセット機能--dynamic-tools--experimental)
   - [4.1 動的ツールセットの仕組みとメリット](#41-動的ツールセットの仕組みとメリット)
   - [4.2 標準ツールセット名一覧](#42-標準ツールセット名一覧)
   - [4.3 Skills（`recommended_toolsets`）との連動](#43-skillsrecommended_toolsetsとの連動)
5. [セキュリティと保護仕様（必読）](#5-セキュリティと保護仕様必読)
6. [トラブルシューティング](#6-トラブルシューティング)
7. [更新履歴](#7-更新履歴)

---

## 1. 拡張機能の全体像と `.gemio/` ディレクトリ構成

プロジェクトごとに「毎回同じコーディング規約やレビュー手順をプロンプトで指示するのが手間」「プロジェクト固有のデータ変換やチェック処理をツールとしてAIに使わせたい」という場合、プロジェクトルート直下に **`.gemio/`** ディレクトリを作成して機能を拡張できます。

```text
your_project/
├── .gemio/
│   ├── skills/                        # ① Skills（専門手順書・規約）格納用
│   │   ├── python-review/
│   │   │   └── SKILL.md               #    1フォルダにつき1つの SKILL.md を配置
│   │   └── doc-writing/
│   │       └── SKILL.md
│   │
│   └── tools/                         # ② カスタムツール（Pythonモジュール）格納用
│       ├── text_utils.py              #    公開リスト agent_tools または TOOLSET(S) を定義
│       └── schema_validator.py
├── src/
└── tests/
```

| 機能 | 配置パス | 主な用途 | 読み込みタイミング |
| :--- | :--- | :--- | :--- |
| **Skills** | `.gemio/skills/<スキル名>/SKILL.md` | コーディング規約、設計ルール、テスト手順、ドキュメント書式などの定型ワークフロー定義 | 起動時は「名前＋1行説明」のみ登録。AIが `activate_skill` を呼んだ時点で本文を遅延ロード |
| **カスタムツール** | `.gemio/tools/<モジュール名>.py` | 固有フォーマットの検証、計算補助、定型テンプレート生成などのPython関数提供 | Gemio起動時に自動ロードしてツールレジストリへ登録 |
| **動的ツールセット** | CLI起動時 `--dynamic-tools` 指定 | 多数のツールがある環境での初期コンテキスト（スキーマトークン）節約 | AIが `request_tools` を呼んだ時点で対象ツールセットを有効化 |

---

## 2. Skills（専門手順・規約の段階的開示）

### 2.1 Skillsの仕組み（Progressive Disclosure）
長い規約ドキュメントを最初からすべてシステムプロンプトに入れてしまうと、関係ない雑談や別作業のときにも毎ターン大量のトークンを消費してしまいます。

Gemioの **Skills** は **段階的開示（Progressive Disclosure）** 方式を採用しています：
1. **起動時（軽量カタログのみ）**: 各 `SKILL.md` の先頭にあるメタデータ（`name` と `description`）だけを読み取り、システムプロンプトの `【利用可能なSkills】` 一覧に数行だけ掲載します（登録スキルが0件の場合は一覧も `activate_skill` ツールも一切追加されません）。
2. **作業開始時（オンデマンド有効化）**: ユーザーの指示に関連するスキルがあるとAIが判断すると、AI自身がメタツール **`activate_skill(skill_name="...")`** を呼び出します。
3. **直後のステップから即時適用**: 有効化されたスキルの Markdown 本文がシステムプロンプトの `【有効化中のSkills（適用手順）】` セクションに注入され、直後のループからその手順に従って作業が遂行されます。
4. **セッションリセット時**: `/clear`（`/reset`, `/new`）を実行すると、有効化されていたスキルは自動的に解除され初期の軽量状態に戻ります。

---

### 2.2 `SKILL.md` の配置ルールと作成方法

* **配置場所**: `<プロジェクトルート>/.gemio/skills/<フォルダ名>/SKILL.md`
  * ファイル名は必ず大文字の **`SKILL.md`** にしてください。
  * **1階層のみ**走査されます（サブフォルダのさらに奥にあるファイルは走査されません）。
  * `.`（ドット）や `_`（アンダースコア）で始まるフォルダ名（例: `_draft/`）、シンボリックリンク、および **256 KB を超えるファイル**は安全のため自動的にスキップされます。
  * Gemioパッケージ本体に同名の標準組み込みスキルが存在する場合は、**プロジェクト側（`.gemio/skills/`）の定義が優先**（上書き適用）されます。

---

### 2.3 フロントマター仕様と実践テンプレート

`SKILL.md` の先頭には `---` で囲まれた軽量YAMLフロントマターを記述し、その下にAIへ伝える具体的な手順を Markdown で記述します。

#### フロントマターの項目
| キー名 | 必須 | 説明 |
| :--- | :---: | :--- |
| **`name`** | 任意 | スキルの識別名（省略時は親フォルダ名がそのままスキル名になります）。 |
| **`description`** | 推奨 | **いつこのスキルを使うべきか**をAIが判断するための1行説明（省略時は本文の最初の非空行の先頭160文字が使われます）。 |
| **`recommended_toolsets`**<br>*(別名: `toolsets`)* | 任意 | `--dynamic-tools` 利用時に、このスキルの有効化と同時に自動有効化したいツールセット名のリスト（`[a, b]` のインライン配列、カンマ区切り、または `- item` の箇条書きリストに対応）。 |

#### 実践テンプレート例：`.gemio/skills/api-endpoint-impl/SKILL.md`
```markdown
---
name: "api-endpoint-impl"
description: "バックエンドの新しいREST APIエンドポイントを実装・修正する際に適用する規約と検証手順"
recommended_toolsets:
  - project_explore
  - project_read
  - project_write
  - sandbox
---

# REST API エンドポイント実装手順

新しいAPIエンドポイントを追加・変更する際は、必ず以下の規約と手順に従ってください。

## 1. 実装規約
- ルーティング定義は `src/api/routes/` 配下に配置し、ビジネスロジックは `src/services/` に分離すること。
- リクエストおよびレスポンスの型定義（Pydanticスキーマ）を `src/schemas/` に明記すること。
- エラー応答は共通例外ハンドラを通し、生のスタックトレースを返さないこと。

## 2. 必須の検証ステップ
1. Pythonファイル変更後、必ず `syntax_check` ツールで構文エラーがないことを確認する。
2. `tests/api/` に対応する正常系・異常系（400 / 404）の単体テストを追加する。
3. 開発者モード（`--dev`）の場合は `run_sandbox_command` で `pytest tests/api/` を実行し、全件パスすることを確認する。
```

---

## 3. カスタムツール（プロジェクト固有ツールの追加）

### 3.1 カスタムツールの配置ルールと読み込み仕様

プロジェクト直下の **`.gemio/tools/`** フォルダに `.py` ファイルを配置すると、Gemio起動時に自動的に読み込まれ、AIが Function Calling で呼び出せるツールとして登録されます。

* **配置場所**: `<プロジェクトルート>/.gemio/tools/*.py`
* **自動スキップ対象**:
  * `_`（アンダースコア）で始まるファイル（例: `_helpers.py` ── 共通ヘルパーモジュールの分離に利用できます）
  * シンボリックリンク
  * **512 KB** を超えるファイル

---

### 3.2 3つの定義スタイル（シンプル形式 / Toolset形式）

モジュール内で公開する変数名に応じて、3通りの書き方が選べます。

#### スタイル1：`agent_tools` リストで公開する（最もシンプル・推奨）
関数を定義して `agent_tools = [...]` に並べるだけで、`ext_<ファイル名>` という名前のツールセットとして登録されます。モジュールの先頭 Docstring の1行目がツールセットの説明になります。

```python
"""プロジェクト固有のテキスト・識別子変換ユーティリティ"""

import hashlib


def generate_short_id(prefix: str, raw_key: str) -> str:
    """
    プレフィックスと元文字列から仕様準拠の短縮IDを生成します。

    Args:
        prefix: IDの先頭に付与する種別プレフィックス（例: "usr", "ord"）。
        raw_key: ハッシュ元の文字列。

    Returns:
        "<prefix>_<8桁16進ハッシュ>" 形式の識別子文字列。
    """
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:8]
    return f"{prefix.strip()}_{digest}"


agent_tools = [
    generate_short_id,
]
```

#### スタイル2：`TOOLSET` でツールセット名や `always_active` を明示指定する
`--dynamic-tools` 使用時のツールセット名を自分で決めたい場合や、常時有効（`always_active=True`）にしたい場合は `Toolset.from_callables()` を使用します。

```python
from gemio.core.tools import Toolset


def check_naming_rule(identifier: str) -> str:
    """指定された識別子がスネークケース命名規約に準拠しているか検査します。"""
    if identifier.islower() and " " not in identifier:
        return f"OK: '{identifier}' は命名規約に準拠しています。"
    return f"[ERROR] '{identifier}' は小文字スネークケースにしてください。"


TOOLSET = Toolset.from_callables(
    name="naming_checker",
    description="識別子の命名規約を検証するカスタムツールセット",
    funcs=[check_naming_rule],
    always_active=False,
)
```

#### スタイル3：`TOOLSETS` で1ファイルから複数のツールセットを公開する
1つの `.py` ファイル内で `TOOLSETS = [toolset_a, toolset_b]` のように `Toolset` のリストを定義すると、複数のツールセットを一括登録できます。

---

### 3.3 AIが使いやすいツール関数（型ヒント・Docstring）の書き方

Gemini API の Function Calling スキーマは、Python関数のシグネチャと Docstring から自動生成されます。以下のポイントを守るとAIの呼び出し精度が大幅に向上します。

1. **引数と戻り値に明確な型ヒントを付ける**:
   `str`, `int`, `float`, `bool`, `list[str]` などのプリミティブで分かりやすい型を指定してください。
2. **Docstringの「最初の1行目」に簡潔な機能概要を書く**:
   動的ツールセットのカタログ表示では、Docstringの先頭1行目がツールの1行サマリとして自動抽出されます。続けて `Args:` と `Returns:` を記載してください。
3. **想定内のエラーは例外を送出せず `[ERROR] ...` の文字列で返す**:
   入力値エラーなどで例外を投げると処理が中断しやすくなります。`"[ERROR] 指定された形式が正しくありません: ..."` のように文字列で返すと、AIがエラー理由を読んで自律的に引数を修正・再試行できます。

---

## 4. 動的ツールセット機能（`--dynamic-tools` / Experimental）

### 4.1 動的ツールセットの仕組みとメリット
通常モード（デフォルト）では、標準ツールとカスタムツールのすべての関数スキーマが最初からAIに渡されます。しかしカスタムツールを大量に追加すると、毎ターンの関数定義だけで多くのコンテキストトークンを消費します。

起動時に **`--dynamic-tools`** オプションを指定すると、以下のオンデマンド方式に切り替わります：

```bash
python -m gemio . --dev --dynamic-tools
```

1. **初期状態**: 常時有効（`always_active=True`）の基本ツール（`ask_user`, `get_current_date`, `get_current_time`）と、ツール要求用メタツール **`request_tools`**（およびスキルがある場合は `activate_skill`）のみがAPIスキーマに渡されます。
2. **軽量カタログの提示**: システムプロンプトに各ツールセットと所属ツールの1行サマリ（`[要リクエスト]` 状態）が掲載されます。
3. **オンデマンド有効化**: AIがファイル探索や編集を行う必要が生じた際、先に `request_tools(toolsets=["project_explore", "project_read"])` 等を呼び出し、直後のステップから対象ツールが利用可能になります。
4. **リセット**: `/clear` を実行すると、動的に有効化されたツール状態も初期の最小構成へリセットされます。

---

### 4.2 標準ツールセット名一覧

`SKILL.md` の `recommended_toolsets` 等で指定できる組み込みツールセット名の一覧です。

| ツールセット名 | 初期状態 (`--dynamic-tools` 時) | 含まれるツール関数 |
| :--- | :---: | :--- |
| **`datetime`** | 常時有効 (`always_active`) | `get_current_date`, `get_current_time` |
| **`project_interaction`** | 常時有効 (`always_active`) | `ask_user` |
| **`project_explore`** | 要リクエスト | `get_structure`, `find_files`, `search_text`, `get_file_metadata` |
| **`project_read`** | 要リクエスト | `read_file`, `read_files_batch`, `preview_file`, `syntax_check` |
| **`project_write`** | 要リクエスト | `write_file`, `search_and_replace_file`, `create_directory`, `rename_path`, `copy_file`, `delete_path` |
| **`sandbox`** *(※`--dev` 時のみ)* | 要リクエスト | `run_sandbox_command` |
| **`ext_<ファイル名>`** *(※カスタム)* | デフォルトは要リクエスト | `.gemio/tools/<ファイル名>.py` で `agent_tools` 公開した関数群 |

---

### 4.3 Skills（`recommended_toolsets`）との連動
`--dynamic-tools` 有効時に、AIが `activate_skill` でスキルを有効化すると、そのスキルのフロントマター `recommended_toolsets` に記載されているツールセットも**同時に一括で自動有効化**されます。これにより、`request_tools` と `activate_skill` を別々に呼ぶ往復ターンを節約できます。

---

## 5. セキュリティと保護仕様（必読）

### 🔒 1. AIによる `.gemio/` 書き換えのハードブロック
AIが自分のルール（Skills）や実行コード（カスタムツール）を勝手に書き換えて権限昇格やガードレール回避を行うことを防ぐため、`Project` クラスのセキュリティガードレールにより **`.gemio/` 配下へのAIからのファイル作成・上書き・リネーム・削除は承認モード（`--trust` 含む）を問わず常時ブロック**されています。`.gemio/` 内のファイルは必ず人間がエディタで作成・編集してください。

### ⚠️ 2. 未知の外部リポジトリにおける `.gemio/tools/` の注意点
**カスタムツール（`.gemio/tools/*.py`）は、Gemio本体と同じPythonプロセス上でインポート・実行されます。**
ホストOSから起動（基本モード、または開発者モードの Host-Orchestrated モード）している場合、`.gemio/tools/*.py` 内のPythonコードはホストOS上で動作します。
* 外部からクローンした**信頼できない第三者のリポジトリ**でGemioを起動する際は、事前に `.gemio/tools/` 配下に不審な `.py` ファイルが含まれていないか必ず確認してください。

### 🐳 3. Dockerサンドボックス利用時の `:ro` マウント保護
開発者モード（`--dev`）でコンテナ内のシェルコマンド（`run_sandbox_command`）を使用する場合、コンテナ内のシェル経由で `.gemio/` が書き換えられるのを防ぐため、`compose.yaml` 等で `.gemio` を読み取り専用（`:ro`）マウントにすることを推奨します（未設定の場合は `/sandbox` 診断パネルで `🟡 注意` として案内されます）。

```yaml
    volumes:
      - .:/app
      - ./.gemio:/app/.gemio:ro
```

### 🕵️ 4. ローカル環境情報（物理パス）の完全秘匿
Skills やカスタムツールのローダーは、ホストOSの絶対パスやOSユーザー名がAIプロンプトや外部APIへ漏洩しないよう、物理ファイルパス属性を一切保持せず、読み込みエラー時の警告・応答メッセージからも物理パスを完全に除去（サニタイズ）して論理名のみを通知します。

---

## 6. トラブルシューティング

### Q1. `.gemio/skills/` にMarkdownファイルを置いたのに認識されません。
* **A.** ファイル名が正確に **`SKILL.md`**（大文字）になっており、`.gemio/skills/<スキルフォルダ名>/SKILL.md` の階層に配置されているか確認してください（`.gemio/skills/SKILL.md` のように直置きされたファイルや、`.`・`_` 始まりのフォルダ、シンボリックリンクは読み込まれません）。また、追加後はGemioを再起動してください。

### Q2. `.gemio/tools/` に追加したPythonツールが呼び出せません。
* **A.** 以下を確認してください：
  1. モジュールのトップレベルに `agent_tools = [...]`、`TOOLSET = ...`、または `TOOLSETS = [...]` のいずれかが定義されているか。
  2. ファイル名が `_`（アンダースコア）で始まっていないか。
  3. 起動直後に「`拡張ツール '...' の読み込みに失敗したためスキップしました。`」というメッセージが出ていないか（構文エラーやインポートエラーがある場合は `python -m py_compile .gemio/tools/xxx.py` 等で確認してください）。

---

## 7. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-14 | AI Agent | 運用中 | 初版作成。Skills（`.gemio/skills/`）、カスタムツール（`.gemio/tools/`）、動的ツールセット（`--dynamic-tools`）の仕様・テンプレート・セキュリティ保護ルールを策定。 |
