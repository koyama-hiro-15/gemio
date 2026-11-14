"""
プロジェクト内固定ディレクトリ（<project_root>/.gemio/tools/）からの拡張ツールローダーモジュール。

ローカル環境情報の露出と冗長な探索処理を排除するため、読み込み対象を `.gemio/tools/*.py` の
1階層のみに固定し、シンボリックリンクの追跡や物理ファイルパスの外部公開を一切行いません。
"""

import importlib.util
from pathlib import Path
from typing import Callable

from gemio.core.tools.registry import Toolset

MAX_TOOL_MODULE_BYTES = 512 * 1024  # 512 KB 上限


def load_project_toolsets(
    project_root: str | Path,
    on_warning: Callable[[str], None] | None = None,
) -> list[Toolset]:
    """
    プロジェクト直下の固定ディレクトリ `<project_root>/.gemio/tools/*.py` から
    公開されている拡張ツールセット（`TOOLSETS`, `TOOLSET`, または `agent_tools`）を読み込みます。

    セキュリティおよび環境情報秘匿のため:
    - シンボリックリンクやディレクトリ外を参照するエントリは無視します。
    - エラー通知や生成される Toolset 情報にホストの物理パスを一切含めません。
    """
    try:
        resolved_root = Path(project_root).resolve()
    except Exception:
        return []

    tools_dir = resolved_root / ".gemio" / "tools"
    if not tools_dir.exists() or tools_dir.is_symlink() or not tools_dir.is_dir():
        return []

    try:
        resolved_tools_dir = tools_dir.resolve()
        if not resolved_tools_dir.is_relative_to(resolved_root):
            return []
        entries = sorted(resolved_tools_dir.iterdir(), key=lambda p: p.name)
    except Exception:
        return []

    loaded_toolsets: list[Toolset] = []

    for entry in entries:
        name = entry.name
        if name.startswith("_") or not name.endswith(".py"):
            continue
        if entry.is_symlink() or not entry.is_file():
            continue

        try:
            resolved_entry = entry.resolve()
            if resolved_entry.parent != resolved_tools_dir:
                continue
            if resolved_entry.stat().st_size > MAX_TOOL_MODULE_BYTES:
                if on_warning:
                    on_warning(f"拡張ツール '{entry.stem}' はサイズ上限を超えているためスキップしました。")
                continue

            logical_mod_name = f"_gemio_project_tool_{entry.stem}"
            spec = importlib.util.spec_from_file_location(logical_mod_name, resolved_entry)
            if spec is None or spec.loader is None:
                continue

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "TOOLSETS") and isinstance(module.TOOLSETS, list):
                for item in module.TOOLSETS:
                    if isinstance(item, Toolset):
                        loaded_toolsets.append(item)
            elif hasattr(module, "TOOLSET") and isinstance(module.TOOLSET, Toolset):
                loaded_toolsets.append(module.TOOLSET)
            elif hasattr(module, "agent_tools") and isinstance(module.agent_tools, list):
                callables = [fn for fn in module.agent_tools if callable(fn)]
                if callables:
                    mod_doc = (getattr(module, "__doc__", "") or "").strip().splitlines()
                    desc = mod_doc[0].strip() if mod_doc else f"拡張ツールセット ({entry.stem})"
                    loaded_toolsets.append(
                        Toolset.from_callables(
                            name=f"ext_{entry.stem}",
                            description=desc,
                            funcs=callables,
                            always_active=False,
                        )
                    )
        except Exception:
            if on_warning:
                on_warning(f"拡張ツール '{entry.stem}' の読み込みに失敗したためスキップしました。")
            continue

    return loaded_toolsets
