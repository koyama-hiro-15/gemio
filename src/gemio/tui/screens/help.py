"""
タブ切り替え式の操作ガイド & コマンド一覧モーダル（HelpModal）モジュール。
"""

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Markdown, Static, TabPane, TabbedContent

_KEYBINDINGS_MD = """\
### キーボードショートカット一覧

| 操作 | キーバインド | 説明 |
| :--- | :--- | :--- |
| **プロンプト送信** | `Ctrl+J` | 入力欄のプロンプトまたはコマンドを送信します |
| **改行** | `Enter` | 入力欄内で新しい行を挿入します |
| **入力欄全クリア** | `F7` ➔ `Backspace` | 全テキストを選択して消去します |
| **元に戻す / やり直し** | `Ctrl+Z` / `Ctrl+Y` | 入力欄のテキスト編集を Undo / Redo します |
| **入力欄へ移動** | `F4` | タイムライン閲覧中などにプロンプト入力欄へフォーカスを戻します |
| **ログ上下スクロール** | `PageUp` / `PageDown` | 入力欄にフォーカスしたまま過去ログをスクロールします |
| **フォーカス移動** | `Tab` / `Shift+Tab` | 入力欄・タイムライン・折りたたみ要素間を移動します |
| **実行中ターンの中断** | `Esc` *(AI処理中)* | **アプリを終了せず**、現在のターンと子プロセスのみを停止します |
| **テキストのコピー** | `[Copy]` ボタン / `/cp` | 各カードの `[Copy]` ボタンまたは `/copy` (`/cp`) でコピーします |
| **コマンドパレット** | `Ctrl+P` | 全コマンド・モデル切替・設定変更を検索して実行します |
| **クイック機能** | `F1`: ヘルプ / `F2`: モデル / `F3`: 承認切替 / `F4`: 入力欄 | ワンキーで各種画面・切替・フォーカス移動を行います |
| **終了** | `Ctrl+Q` | Gemio TUI を安全に終了します |
"""

_COMMANDS_MD = """\
### スラッシュコマンド一覧

* **`/help`** (`/h`, `help`, `/info`) — このヘルプ画面を表示します。
* **`/model [名前]`** — AIモデルを選択画面または直接指定（例: `/model neon`, `/model carbon`, `/model hydrogen`, `/model 3.8`）で切り替えます。
* **`/edit [on|off]`** — エディットモード（作成・編集は自動承認、削除とコマンド実行は都度確認）を切り替えます。
* **`/trust [on|off]`** (`/auto`) — トラストモード（事前同意の上で変更操作を自動承認）を切り替えます。
* **`/sandbox`** (`/status`, `/container`) — サンドボックス環境設定およびマウント保護状態（`ls -la` 相当）を診断します。
* **`/tokens`** (`/usage`, `/token`) — 現在のコンテキスト健全性と累積トークン消費量の内訳を表示します。
* **`/clear [--all|-a]`** (`/reset`, `/new`) — 会話履歴とトークン統計をリセットします（`--all` 指定時は作業前提資料も破棄）。
* **`/search <内容>`** (`/s`) — 対話履歴を汚さずに隔離環境で単発 Web 検索を実行します（検索カードの `[+ 資料に追加]` または `/search --save` で作業前提資料へ保持 / `/search --clear` で破棄）。
* **`/search on|off`** (`/s on|off`) — 作業エージェント（AI）による Web 検索ツール呼び出し（`+WEB` / `-WEB`）を切り替えます（ヘッダーの `+WEB` / `-WEB` バッジクリックでも切替可能）。
* **`/copy`** (`/cp`) — 直近の AI 回答をクリップボードへコピーします。
* **`/compact [残すターン数]`** — 古い対話ターン群を構造化サマリへ要約圧縮します（デフォルトは直近2ターン維持）。
* **`/rewind [ターン数]`** (`/undo`) — 直近の対話ターンを指定数だけ巻き戻します。
* **`/exit`** (`exit`, `quit`) — セッションを終了します。
"""

_SAFETY_MD = """\
### 動作モード・3段階承認・保護ガードレール

1. **2つの動作モード**:
   * **基本モード (`STD`)**: 外部プロセスを一切起動せず、プロジェクト配下のファイル操作と Python 静的構文チェックのみを行います。
   * **開発者モード (`DEV` / `--dev`)**: 安全性が検証された Docker / Dev Container サンドボックス内でのみシェルコマンドを実行します。
2. **3段階の承認モード**:
   * **`NORMAL` (通常承認)**: すべてのファイル変更・削除・コマンド実行で都度確認を行います。
   * **`EDIT` (エディット)**: 作成・編集・移動・コピーは自動承認し、**ファイル削除とコマンド実行のみ都度確認**します。
   * **`TRUST` (トラスト)**: 信頼済みプロジェクト向けに、致命的保護領域を除く変更操作を自動承認します。
3. **AIによるWeb検索（`+WEB` / `-WEB`）と常時確認**:
   * AIが外部検索エージェント（ステートレス隔離）へ質問を送る際は、**`NORMAL` / `EDIT` / `TRUST` を問わず常に送信プロンプト全文の承認確認**を行います。
   * `TRUST` 自律実行中など検索確認を止めたい場合や外部送信を完全に無効化したい場合は、上部ヘッダーの `+WEB` バッジをクリック（または `/search off`）して `-WEB` へ切り替えてください。
4. **承認確認時の4つの選択**:
   * `Y`: 許可 / `N`: 拒否 / `A` (`Esc`): 作業を強制中止(Abort) / `K`: プロセス即時終了(Kill)
   * ※ 拒否後に AI が相談なく変更を強行しようとした場合、**サーキットブレーカー**により自動遮断されます。
5. **常時ハードブロックされる保護領域**:
   * `.env` 等の機密ファイル、秘密鍵、クラウド認証情報、`.git/`、既存 `.devcontainer/`、`.gemio/`、`*.egg-info/`。
"""


class HelpModal(ModalScreen[None]):
    """キー操作・コマンド一覧・安全仕様をタブ切り替えで閲覧できるヘルプモーダル。"""

    def __init__(self, initial_tab: str = "tab-keys") -> None:
        super().__init__()
        self.initial_tab = initial_tab

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-large"):
            yield Static(
                "Gemio 操作ガイド & コマンドリファレンス (/help)",
                markup=False,
                classes="modal-title",
            )
            with TabbedContent(initial=self.initial_tab, id="help-tabs"):
                with TabPane("キー操作", id="tab-keys"):
                    with VerticalScroll(classes="modal-scroll-fill"):
                        yield Markdown(_KEYBINDINGS_MD)
                with TabPane("コマンド一覧", id="tab-commands"):
                    with VerticalScroll(classes="modal-scroll-fill"):
                        yield Markdown(_COMMANDS_MD)
                with TabPane("安全設計・承認", id="tab-safety"):
                    with VerticalScroll(classes="modal-scroll-fill"):
                        yield Markdown(_SAFETY_MD)
            with Horizontal(classes="modal-buttons"):
                yield Button("閉じる (Esc)", id="btn-close", variant="primary", compact=True)

    def on_key(self, event: events.Key) -> None:
        if event.key in {"escape", "f1", "q"}:
            event.stop()
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


__all__ = [
    "HelpModal",
]
