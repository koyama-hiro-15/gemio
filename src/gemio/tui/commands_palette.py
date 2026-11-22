"""
Textual 標準コマンドパレット (Ctrl+P) 向けの GemioCommandProvider モジュール。

スラッシュコマンドを暗記していなくても、Ctrl+P から全機能・モデル切替・承認切替を
一覧表示（discover）およびインクリメンタル検索（search）して実行できます。
"""

from functools import partial
from typing import TYPE_CHECKING, Callable

from textual.command import DiscoveryHit, Hit, Hits, Provider



if TYPE_CHECKING:
    from gemio.tui.app import GemioApp


class GemioCommandProvider(Provider):
    """Gemio TUI の全アクション・設定変更をコマンドパレットへ公開するプロバイダ。"""

    def _collect_commands(self) -> list[tuple[str, str, Callable[[], None]]]:
        app: GemioApp = self.app  # type: ignore[assignment]
        items: list[tuple[str, str, Callable[[], None]]] = [
            (
                "Help: 操作ガイド・コマンド一覧を表示 (/help)",
                "キーバインド、スラッシュコマンド、安全仕様のヘルプモーダルを開きます",
                app.action_show_help,
            ),
            (
                "Model: AIモデルを選択して切り替え (/model)",
                "プリセット一覧またはカスタム指定でAIモデルを変更します",
                app.action_select_model,
            ),
            (
                "Sandbox: サンドボックス・マウント保護診断 (/sandbox)",
                "コンテナ設定、ファイル権限、機密マスク状態を検査・表示します",
                app.action_show_sandbox,
            ),
            (
                "Tokens: トークン使用量・コンテキスト健全性診断 (/tokens)",
                "直近コンテキスト量とセッション累積トークン消費量の内訳を表示します",
                app.action_show_tokens,
            ),
            (
                "Approval: エディットモード (EDIT) を切り替え (/edit)",
                "ファイル作成・編集の自動承認モードを ON/OFF します",
                lambda: app._execute_edit_mode(None),
            ),
            (
                "Approval: トラストモード (TRUST) を有効化 (/trust on)",
                "同意確認の上で変更操作の自動承認モードを有効化します",
                lambda: app._execute_trust_mode(True),
            ),
            (
                "Approval: 通常承認モード (NORMAL) に戻す",
                "すべての変更・コマンド実行で都度確認を行う標準モードに戻します",
                lambda: app._set_normal_approval_mode(),
            ),
            (
                "Copy: 直近のAI回答をクリップボードにコピー (/copy)",
                "直近ターンのAI回答テキストをコピーします",
                app.action_copy_last_response,
            ),
            (
                "Session: 直近1ターンを巻き戻す (/rewind)",
                "直前の対話ターンを取り消して履歴とタイムラインから除去します",
                lambda: app._execute_rewind(1),
            ),
            (
                "Session: 古い対話履歴を構造化要約へ圧縮 (/compact)",
                "直近2ターンを残し、それ以前の履歴を要約してコンテキストを圧縮します",
                lambda: app._start_compact_worker(2),
            ),
            (
                "Session: 対話履歴をリセット・新規セッション開始 (/clear)",
                "作業前提資料を維持したまま会話履歴とトークン統計をリセットします",
                lambda: app._execute_clear_session(clear_materials=False),
            ),
            (
                "Session: 作業前提資料も含めて完全リセット (/clear --all)",
                "会話履歴・トークン統計・作業前提資料をすべて初期状態に戻します",
                lambda: app._execute_clear_session(clear_materials=True),
            ),
            (
                "Search: AIによるWeb検索ツール (+WEB / -WEB) を切り替え (/search on|off)",
                "作業エージェントからのWeb検索ツール呼び出しの許可 (ON/OFF) をトグルします",
                lambda: app._execute_web_search_toggle(None),
            ),
            (
                "Search: 直近の単発検索結果を作業前提資料に追加 (/search --save)",
                "直近に /search で取得した検索結果を作業前提資料ストアに保持します",
                app._save_last_search_material_action,
            ),
            (
                "Search: 保持中のWeb検索前提資料を破棄 (/search --clear)",
                "セッション内のWeb検索結果資料をクリアします",
                app._clear_search_materials_action,
            ),
            (
                "Exit: Gemio を終了 (/exit)",
                "稼働中のプロセスを停止してアプリケーションを終了します",
                app.action_quit_app,
            ),
        ]

        for info in app.controller.get_cached_models():
            tags: list[str] = []
            if info.supports_thinking:
                tags.append("Thinking対応")
            ctx = info.format_context_limit()
            if ctx:
                tags.append(f"Ctx:{ctx}")
            tag_prefix = f"[{' / '.join(tags)}] " if tags else ""
            items.append(
                (
                    f"Model Switch: {info.model_id}",
                    f"AIモデルを {info.model_id} ({tag_prefix}{info.summary_label()}) に即時変更します",
                    partial(app._switch_model_directly, info.model_id),
                )
            )

        return items

    async def discover(self) -> Hits:
        for title, desc, callback in self._collect_commands():
            yield DiscoveryHit(title, callback, help=desc)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, desc, callback in self._collect_commands():
            score = matcher.match(f"{title} {desc}")
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(title),
                    callback,
                    help=desc,
                )


__all__ = [
    "GemioCommandProvider",
]
