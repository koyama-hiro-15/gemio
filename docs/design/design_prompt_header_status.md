# プロンプト入力欄ヘッダー（動作モード・承認モード表示）機能設計書

**作成日:** 2026年11月05日  
**最終更新日:** 2026年11月07日  
**ステータス:** 実装完了  
**対象システム:** `src/gemio/cli/main.py`, `src/gemio/cli/ui.py`, `tests/test_cli_prompt.py`  

### 【要約】
CLIの対話プロンプト入力欄の上部に、従来の対象プロジェクトパス（`project:`）に加えて現在の動作モード（`mode:`）と承認モード（`approval:`：通常承認モード／エディットモード／トラストモード）を2行ヘッダー構成で常時表示する機能を定義します。セッション中に `/edit` や `/trust`（`/auto`）で承認モードを切り替えた際もリアルタイムで表示とカラーリング（緑／黄／赤）が更新され、ユーザーの誤認・誤操作を防止します。

---

## 1. 背景と目的

1. **動作モードの常時可視化**:
   対話セッションが長引き、起動時のウェルカムバナーが画面外へスクロールした後でも、現在「基本モード」で動作しているのか「開発者モード（Dev Container内実行 / ホストDocker接続）」で動作しているのかを常時一目で把握できるようにする。
2. **承認モードのリアルタイム同期と安全性の向上**:
   `/trust`（または `/auto`）コマンドで「通常承認モード（都度確認）」と「トラストモード（自動承認）」を動的に切り替えた際、プロンプト入力欄の直上に色分け（緑 / 赤）された現在の承認モードを表示することで、意図せず自動承認のまま操作を続けてしまうリスクを防ぐ。
3. **HTMLエスケープによる安全なプロンプト描画**:
   `prompt_toolkit` の `HTML(...)` フォーマット生成において、プロジェクトパスやコンテナ名に `<`, `>`, `&` 等の特殊文字が含まれる場合でもパースエラーを起こさないよう `html.escape` を適用する。

---

## 2. UI・表示設計

### 2.1 レイアウト（2行ヘッダー構成）

1行目にプロジェクトのフルパス（`project:`）、2行目に動作モード（`mode:`）と承認モード（`approval:`）をパイプ（`|`）区切りで配置し、3行目にユーザー入力行（`User >`）を表示します。

* **基本モード ＋ 通常承認モード時**:
  ```text
  project: /path/to/project
  mode: 基本モード (Standard)  |  approval: 通常承認モード (都度確認)
  User > (Esc+Enterで送信 / Esc+Backspaceでクリア):
  ```

* **開発者モード（コンテナ内実行） ＋ トラストモード時**:
  ```text
  project: /workspace/project
  mode: 開発者モード (Dev Container内実行)  |  approval: トラストモード (自動承認)
  User > (Esc+Enterで送信 / Esc+Backspaceでクリア):
  ```

* **開発者モード（ホストDocker接続） ＋ 通常承認モード時**:
  ```text
  project: /path/to/project
  mode: 開発者モード (Docker: gemio-sandbox)  |  approval: 通常承認モード (都度確認)
  User > (Esc+Enterで送信 / Esc+Backspaceでクリア):
  ```

### 2.2 カラーリング・装飾仕様（`prompt_toolkit` ANSIカラー）

| 項目 | 状態 | 表示テキスト | カラー・装飾 (`HTML` タグ) |
| :--- | :--- | :--- | :--- |
| **プロジェクト** | 常時 | `project: <パス>` | ラベル: `<ansiblue><b>project:</b></ansiblue>`<br>値: 標準色（`html.escape` 適用） |
| **動作モード** | 基本モード (`Project`) | `基本モード (Standard)` | ラベル: `<ansiblue><b>mode:</b></ansiblue>`<br>値: `<ansigreen><b>基本モード (Standard)</b></ansigreen>` |
| | 開発者モード (`in_container`) | `開発者モード (Dev Container内実行)` | 値: `<ansimagenta><b>開発者モード (Dev Container内実行)</b></ansimagenta>` |
| | 開発者モード (`host_docker`) | `開発者モード (Docker: <コンテナ名>)` | 値: `<ansimagenta><b>開発者モード (Docker: <コンテナ名>)</b></ansimagenta>` |
| **承認モード** | 通常承認 (`approval_mode="normal"`) | `通常承認モード (都度確認)` | ラベル: `<ansiblue><b>approval:</b></ansiblue>`<br>値: `<ansigreen><b>通常承認モード (都度確認)</b></ansigreen>` |
| | エディット (`approval_mode="edit"`) | `エディットモード (編集自動 / 削除・コマンド確認)` | 値: `<ansiyellow><b>エディットモード (編集自動 / 削除・コマンド確認)</b></ansiyellow>` |
| | トラスト (`approval_mode="trust"`) | `トラストモード (自動承認)` | 値: `<ansired><b>トラストモード (自動承認)</b></ansired>` |
| **入力行** | 常時 | `User > (Esc+Enterで送信 / Esc+Backspaceでクリア):` | `<ansigreen><b>User &gt;</b></ansigreen> (Esc+Enterで送信 / Esc+Backspaceでクリア):` |

---

## 3. 実装設計

### 3.1 プロンプトヘッダー生成関数 (`format_prompt_header`)
`src/gemio/cli/main.py` に `format_prompt_header(project: Project) -> str` を実装します。
引数として `Project`（または `DeveloperProject`）インスタンスを受け取り、`isinstance(project, DeveloperProject)`、`project.execution_mode`、`project.container_name`、`project.edit_mode`、および `project.trust_mode` を参照して動的にHTML文字列を組み立てます。

### 3.2 メインループでの動的評価
`main()` の対話ループ（`while True:`）内で、毎回の入力待ち時に `HTML(format_prompt_header(project))` を `session.prompt(...)` に渡します。これにより、`/edit` や `/trust` コマンドによる承認モードの変更が次ターンのプロンプト表示に即座に反映されます。

---

## 4. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-05 | AI Agent | 実装完了 | 初版作成および実装完了。2行ヘッダー構成による `project:` / `mode:` / `approval:` 表示機能を実装。 |
| 2026-11-06 | AI Agent | 実装完了 | エディットモード（`approval_mode="edit"`、黄色表示）の追加を反映。 |
| 2026-11-07 | AI Agent | 実装完了 | プロンプト入力欄クリア機能（`Esc+Backspace`）の導入に伴い、入力行ガイド表記を `User > (Esc+Enterで送信 / Esc+Backspaceでクリア):` に更新。 |
