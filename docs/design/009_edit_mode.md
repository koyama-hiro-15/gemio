# #009 エディットモード（Edit Mode）機能設計書

**作成日:** 2026年11月06日  
**最終更新日:** 2026年11月17日  
**ステータス:** 実装完了（現行）  
**対象システム:** `src/gemio/project/base.py`, `src/gemio/project/developer.py`, `src/gemio/cli/main.py`, `src/gemio/cli/ui/`, `tests/`  

### 【要約】
従来の「通常承認モード」と「トラストモード」の2段階構成に加え、中間モードとして「エディットモード（`--edit` / `/edit`）」を導入します。エディットモードでは、ファイルの作成・編集・リネーム・コピーを自動承認して作業速度を高めつつ、取り返しのつきにくいファイルの削除（`delete_path`）とサンドボックス内シェルコマンド実行（`run_sandbox_command`）については人間の都度確認（Human-in-the-loop）を維持します。また、確認操作が拒否された際は、自動承認対象のファイル書き換えも含めてすべての変更操作をサーキットブレーカーで遮断します。

> **💡 現行仕様に関する補足:** `Project` / `DeveloperProject` の3段階承認モード制御およびサーキットブレーカー優先評価の仕様は現行コードと一致しています（CLI/UI側の描画・コマンド処理配置については [`012_ui_package_refactoring_for_textual.md`](./012_ui_package_refactoring_for_textual.md) も参照してください）。

---

## 1. 背景と目的

1. **開発テンポと安全性の両立**:
   Git管理下のプロジェクトにおいて、ファイルの作成や編集（`write_file`, `search_and_replace_file` 等）は差分の確認やロールバックが容易であるためAIにテンポよく進めさせたい一方、ファイル・ディレクトリの削除（`delete_path`）や任意のシェルコマンド実行（`run_sandbox_command`）は事前に人間が目視確認したいという実務上のニーズに応える。
2. **軽快なモード切り替え**:
   トラストモード（全自動承認）とは異なり、危険度の高い操作（削除・コマンド実行）にはブレーキが残っているため、エディットモード有効化時は同意確認プロンプト（Yes/No入力）を求めず、モード切替メッセージを表示して即座に有効化する。
3. **承認拒否後の迂回書き換え防止（サーキットブレーカーの徹底）**:
   エディットモード中に `delete_path` や `run_sandbox_command` がユーザーに拒否された直後、AIがユーザーへの報告・相談なしに無断で `write_file` や `search_and_replace_file` 等によるコード・設定ファイルの書き換えへ逃げようとした場合でも、自動承認を通さず確実に遮断（`AgentExecutionAborted`）する。

---

## 2. 承認モードの3段階モデルと権限マトリクス

承認モードを `"normal"`（通常承認モード）、`"edit"`（エディットモード）、`"trust"`（トラストモード）の3段階で管理します。

| 操作カテゴリ / 対象ツール | ① 通常承認モード<br>(`"normal"`) | ② エディットモード<br>(`"edit"`) | ③ トラストモード<br>(`"trust"`) |
| :--- | :---: | :---: | :---: |
| **読み取り・探索・構文検査・質問**<br>(`read_file`, `get_structure`, `find_files`, `search_text`, `syntax_check`, `ask_user` 等) | 自動実行 | 自動実行 | 自動実行 |
| **ディレクトリ作成**<br>(`create_directory`) | 自動実行<br>*(※拒否保留時は遮断)* | 自動実行<br>*(※拒否保留時は遮断)* | 自動実行<br>*(※拒否保留時は遮断)* |
| **ファイル作成・編集・移動・コピー**<br>(`write_file`, `search_and_replace_file`, `rename_path`, `copy_file`) | **都度確認** | **自動承認（確認なし）**<br>*(※拒否保留時は遮断)* | **自動承認（確認なし）** |
| **ファイル・ディレクトリの削除**<br>(`delete_path`) | **都度確認** | **都度確認** | **自動承認（確認なし）** |
| **サンドボックス内コマンド実行**<br>(`run_sandbox_command` ※`--dev`時) | **都度確認** | **都度確認** | **自動承認（確認なし）** |
| **致命的セキュリティ領域**<br>（機密ファイル・`.git/`・既存`.devcontainer/`・ルート外） | **常時ハードブロック** | **常時ハードブロック** | **常時ハードブロック** |

---

## 3. アーキテクチャ・クラス設計

### 3.1 定数および状態管理 (`src/gemio/project/base.py`)

```python
APPROVAL_MODE_NORMAL = "normal"
APPROVAL_MODE_EDIT = "edit"
APPROVAL_MODE_TRUST = "trust"
VALID_APPROVAL_MODES = {
    APPROVAL_MODE_NORMAL,
    APPROVAL_MODE_EDIT,
    APPROVAL_MODE_TRUST,
}
```

* **`Project.__init__`**:
  ```python
  def __init__(
      self,
      project_path: str | Path,
      require_approval: bool = True,
      approval_callback: Callable[[str], bool | str] | None = None,
      ask_user_callback: Callable[[str, list[str] | None, str], str] | None = None,
      approval_mode: str | None = None,
  ):
  ```
  * `approval_mode` が明示指定された場合は `"normal" | "edit" | "trust"` を検証の上で設定する。
  * `approval_mode` が `None` の場合は、既存の `require_approval` 引数（`True` なら `"normal"`、`False` なら `"trust"`）から初期モードを決定する（後方互換性の維持）。
  * `DeveloperProject.__init__` にも `approval_mode: str | None = None` を追加し、`super().__init__` に引き渡す。

* **プロパティとメソッド**:
  * `approval_mode -> str`: 現在のモード文字列（`"normal"`, `"edit"`, `"trust"`）を返す。
  * `edit_mode -> bool`: `self._approval_mode == "edit"` のとき `True`。
  * `trust_mode -> bool`: `self._approval_mode == "trust"` のとき `True`。
  * `require_approval -> bool`: `self._approval_mode == "normal"` のとき `True`、`"edit"` または `"trust"` のとき `False`。セッター（`project.require_approval = ...`）も提供し、既存コードとの完全な互換性を保つ。
  * `set_approval_mode(mode: str) -> None`: 指定モードへ切り替え、`self._rejection_pending = False` にリセットする。
  * `set_edit_mode(enabled: bool) -> None`: `True` なら `"edit"`、`False` なら `"normal"` に設定する。
  * `set_trust_mode(enabled: bool) -> None`: `True` なら `"trust"`、`False` なら `"normal"` に設定する。

### 3.2 サーキットブレーカーと `_confirm` の評価順序

承認拒否後（`self._rejection_pending == True`）にAIがユーザーへ報告・質問（`ask_user`）せず連続して変更操作を試みた場合、自動承認モードであっても確実に遮断するため、**自動承認判定よりも前にサーキットブレーカー判定を実行**します。

```python
def _check_circuit_breaker(self) -> None:
    if self._rejection_pending:
        self._rejection_pending = False
        raise AgentExecutionAborted(
            "承認拒否後にユーザーへの説明・確認を行わず連続して変更操作が試行されたため、"
            "安全保護（サーキットブレーカー）により作業を強制中止しました。"
        )
```

1. `create_directory` を含むすべての変更系メソッドの実行時に `_check_circuit_breaker()`（または `_confirm()` 先頭での同検査）を通過させる。
2. `_confirm(self, action_description: str, *, force_confirm_in_edit: bool = False) -> bool`:
   * Step 1: `self._check_circuit_breaker()` を実行（拒否保留中ならモードを問わず即座に `AgentExecutionAborted` を送出）。
   * Step 2:
     * `self._approval_mode == "trust"` の場合、`True`（自動承認）を返す。
     * `self._approval_mode == "edit"` かつ `not force_confirm_in_edit` の場合、`True`（自動承認）を返す。
   * Step 3: 上記以外（`"normal"` モード、または `"edit"` モードでの `delete_path` / `run_sandbox_command`）は人間の承認プロンプトを表示する。
3. `delete_path` および `DeveloperProject.run_sandbox_command` では `self._confirm(action_desc, force_confirm_in_edit=True)` を呼び出す。

---

## 4. CLI・UI仕様 (`src/gemio/cli/main.py`)

### 4.1 起動オプション
* `--edit` オプションを追加:
  ```text
  gemio [project_dir] [--dev] [--edit] [--trust]
  ```
* `--edit` が指定された場合、同意確認の入力待ちは行わず、有効化メッセージを表示してエディットモード（`approval_mode="edit"`）で起動します。
* `--trust` と `--edit` が同時に指定された場合は、`--trust` の同意確認を行い、同意されれば `"trust"`、キャンセルされた場合は `"edit"` で起動します。

### 4.2 対話中コマンド (`/edit`)
* `/edit`（または `/edit on`, `/edit off`）:
  * `/edit` 単体入力時: 現在エディットモード以外であればエディットモードへ切り替え、すでにエディットモードであれば通常承認モードへ戻します。
  * `/edit on` (`true`, `enable`): エディットモードへ切り替えます。
  * `/edit off` (`false`, `disable`): 通常承認モードへ戻します。
* 有効化時は確認プロンプトを出さず、以下の通知パネル（黄色枠）を表示して即座に切り替えます:
  ```text
  ✏️ エディットモードを有効化しました。
    • ファイル作成・編集・移動・コピー : 自動承認
    • ファイル削除・シェルコマンド実行 : 都度確認
  （通常承認モードへ戻すには `/edit off`）
  ```

### 4.3 ステータス表示・カラーリング仕様

| モード | プロンプトヘッダー (`approval:`) | ウェルカムバナー | カラー |
| :--- | :--- | :--- | :--- |
| **通常承認モード** (`normal`) | `通常承認モード (都度確認)` | `通常承認モード（都度確認）` | 🟢 緑 (`ansigreen` / `bold green`) |
| **エディットモード** (`edit`) | `エディットモード (編集自動 / 削除・コマンド確認)` | `エディットモード（編集自動 / 削除・コマンド確認）` | 🟡 黄 (`ansiyellow` / `bold yellow`) |
| **トラストモード** (`trust`) | `トラストモード (自動承認)` | `トラストモード（自動承認）` | 🔴 赤 (`ansired` / `bold red`) |

---

## 5. 更新履歴

| 日付 | 更新者 | ステータス | 変更内容の要約 |
| :--- | :--- | :--- | :--- |
| 2026-11-06 | AI Agent | 実装完了 | 初版作成および実装。3段階承認モード（normal / edit / trust）、`delete_path`・コマンド確認維持、即時切替メッセージ、および拒否後の全変更操作遮断（サーキットブレーカー）を規定。 |
| 2026-11-17 | AI Agent | 実装完了（現行） | 設計書の3桁連番管理導入に伴い `009_edit_mode.md` へリネームし、ステータスを `実装完了（現行）` に更新。 |
