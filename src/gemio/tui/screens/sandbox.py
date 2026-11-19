"""
サンドボックス環境・マウント保護状態診断モーダル（SandboxStatusModal）モジュール。
"""

from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from gemio.project import APPROVAL_MODE_EDIT, APPROVAL_MODE_TRUST


class SandboxStatusModal(ModalScreen[None]):
    """
    基本モードの保護仕様、または開発者モードのコンテナ設定・マウント状態一覧表・診断アラートを表示するモーダル画面。
    """

    def __init__(self, status: dict[str, Any]) -> None:
        super().__init__()
        self.status = dict(status)

    def compose(self) -> ComposeResult:
        is_dev = bool(self.status.get("is_dev_mode", False))
        if not is_dev:
            with Vertical(classes="modal-dialog"):
                yield Static(
                    "ワークスペース保護状態 (基本モード)",
                    markup=False,
                    classes="modal-title",
                )
                yield Static("", id="sandbox-std-body")
                with Horizontal(classes="modal-buttons"):
                    yield Button("閉じる (Esc)", id="btn-close", variant="primary", compact=True)
            return

        warnings = self.status.get("warnings") or []
        danger_cnt = sum(1 for w in warnings if w.get("level") == "danger")
        warn_cnt = sum(1 for w in warnings if w.get("level") == "warning")

        dialog_cls = "modal-dialog-large"
        if danger_cnt > 0:
            dialog_cls += " modal-dialog-danger"
        elif warn_cnt > 0:
            dialog_cls += " modal-dialog-warn"

        with Vertical(classes=dialog_cls):
            yield Static(
                "サンドボックス環境 & マウント状態診断 (/sandbox)",
                markup=False,
                classes="modal-title",
            )
            yield Static("", id="sandbox-dev-summary")
            yield DataTable(
                show_row_labels=False,
                zebra_stripes=True,
                cursor_type="row",
                id="sandbox-table",
                classes="modal-data-table",
            )
            with VerticalScroll(classes="modal-scroll-body"):
                yield Static("", id="sandbox-alerts")
            with Horizontal(classes="modal-buttons"):
                yield Button("閉じる (Esc)", id="btn-close", variant="primary", compact=True)

    def on_mount(self) -> None:
        if not bool(self.status.get("is_dev_mode", False)):
            self._populate_standard_mode()
        else:
            self._populate_developer_mode()
        self.query_one("#btn-close", Button).focus()

    def _populate_standard_mode(self) -> None:
        appr = self.status.get("approval_mode", "normal")
        if appr == APPROVAL_MODE_TRUST:
            appr_str = "トラストモード（自動承認）"
        elif appr == APPROVAL_MODE_EDIT:
            appr_str = "エディットモード（編集自動 / 削除確認）"
        else:
            appr_str = "通常承認モード（都度確認）"

        body = Text()
        body.append("現在は「基本モード (Standard Mode)」で動作しています。\n\n", style="bold green")
        body.append("  • 対象プロジェクト: ", style="bold cyan")
        body.append(f"{self.status.get('project_path', '')}\n")
        body.append("  • 承認モード      : ", style="bold cyan")
        body.append(f"{appr_str}\n")
        body.append("  • シェルコマンド  : ", style="bold cyan")
        body.append("無効（外部プロセス起動なし・ファイル操作のみ）\n", style="bold green")
        body.append("  • 常時保護領域    : ", style="bold cyan")
        body.append(
            ".env、秘密鍵（.ssh/, *.pem, *.key 等）、クラウド認証情報、.git/、既存 .devcontainer/、.gemio/ への改ざんはシステムレベルで常時ブロックされています。\n\n"
        )
        body.append(
            "※ Docker サンドボックス内でコマンド実行やマウント状態診断を行うには --dev オプションを付けて起動してください。",
            style="dim",
        )
        self.query_one("#sandbox-std-body", Static).update(body)

    def _populate_developer_mode(self) -> None:
        st = self.status
        exec_mode = st.get("execution_mode", "")
        mode_desc = (
            f"Host-Orchestrated (Docker: {st.get('container_name', '')})"
            if exec_mode == "host_docker"
            else "In-Container (Dev Container内直接実行)"
        )
        ws_dir = str(st.get("workspace_dir", ""))
        ws_rw = "rw" if st.get("workspace_rw", True) else "ro"

        user_info = st.get("user") or {}
        user_str = str(user_info.get("user_str", "不明"))
        is_root = bool(user_info.get("is_root"))

        net_info = st.get("network") or {}
        net_mode = str(net_info.get("mode", "unknown"))
        net_iso = bool(net_info.get("is_isolated"))

        res_info = st.get("resources") or {}
        ro_rootfs = bool(res_info.get("readonly_rootfs"))
        mem_str = str(res_info.get("memory_limit", "無制限"))
        cpu_str = str(res_info.get("cpus", "無制限"))
        pids_str = str(res_info.get("pids_limit", "無制限"))
        sens_keys = st.get("sensitive_env_keys") or []

        summary = Text()
        summary.append(f"接続方式: {mode_desc}  |  作業パス: {ws_dir} ({ws_rw})\n")
        summary.append("実行ユーザー: ")
        summary.append(
            f"{user_str} (root注意)  |  " if is_root else f"{user_str} (非特権)  |  ",
            style="bold yellow" if is_root else "bold green",
        )
        summary.append("ネットワーク: ")
        summary.append(
            f"完全遮断 ({net_mode})\n" if net_iso else f"通信可能 ({net_mode})\n",
            style="bold green" if net_iso else "bold yellow",
        )
        summary.append("環境変数検査: ")
        if sens_keys:
            joined = ", ".join(str(k) for k in sens_keys)
            summary.append(f"[危険] 機密キー検出 ({joined})  |  ", style="bold red")
        else:
            summary.append("機密キー検出なし  |  ", style="bold green")
        rootfs_label = "Read-Only" if ro_rootfs else "Read-Write"
        summary.append(
            f"RootFS: {rootfs_label} | Mem: {mem_str} | CPU: {cpu_str} | PIDs: {pids_str}",
            style="dim",
        )
        self.query_one("#sandbox-dev-summary", Static).update(summary)

        table = self.query_one("#sandbox-table", DataTable)
        table.add_columns("Path", "Mode", "UID:GID", "Size", "Mount", "Status")

        for entry in st.get("entries") or []:
            risk = entry.get("risk_level", "normal")
            m_mode = entry.get("mount_mode", "rw")
            if risk == "danger":
                cell_style = "bold red"
            elif risk == "warning":
                cell_style = "bold yellow"
            elif risk == "safe":
                cell_style = "bold green"
            elif m_mode == "rw":
                cell_style = "bold cyan"
            else:
                cell_style = "dim"

            table.add_row(
                Text(str(entry.get("name", "")), style=cell_style),
                Text(str(entry.get("perms", "")), style=cell_style),
                Text(f"{entry.get('uid', 0)}:{entry.get('gid', 0)}", style=cell_style),
                Text(f"{entry.get('size', 0):,} B", style=cell_style),
                Text(str(entry.get("mount_source", "")), style=cell_style),
                Text(str(entry.get("status_label", "")), style=cell_style),
            )

        warnings = st.get("warnings") or []
        alert_text = Text()
        if not warnings:
            alert_text.append(
                "セキュリティ診断結果: 問題なし — 機密ファイル・管理領域はすべて適切に保護されています。",
                style="bold green",
            )
        else:
            danger_cnt = sum(1 for w in warnings if w.get("level") == "danger")
            warn_cnt = sum(1 for w in warnings if w.get("level") == "warning")
            alert_text.append(
                f"診断アラート (危険: {danger_cnt}件 / 注意: {warn_cnt}件)\n",
                style="bold yellow",
            )
            for w in warnings:
                lvl = w.get("level", "warning")
                tag_style = "bold red" if lvl == "danger" else "bold yellow"
                prefix = "[危険] " if lvl == "danger" else "[注意] "
                alert_text.append(f"  {prefix}{w.get('path', '')}: ", style=tag_style)
                alert_text.append(f"{w.get('message', '')}\n")
                alert_text.append(
                    f"         対策: {w.get('recommendation', '')}\n",
                    style="dim",
                )
        self.query_one("#sandbox-alerts", Static).update(alert_text)

    def on_key(self, event: events.Key) -> None:
        if event.key in {"escape", "q"}:
            event.stop()
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)


__all__ = [
    "SandboxStatusModal",
]
