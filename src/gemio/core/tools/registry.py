"""
ツール仕様（ToolSpec）・ツールセット（Toolset）・レジストリ（ToolRegistry）および
ツール提供プロバイダ（ToolProvider / StaticToolProvider）定義モジュール。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable


def extract_tool_summary(func: Callable) -> str:
    """関数の Docstring から先頭の1行概要を安全に抽出します。"""
    doc = inspect.getdoc(func) or ""
    for line in doc.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(("args:", "returns:", "raises:")):
            break
        return stripped
    return f"{getattr(func, '__name__', 'tool')} ツール"


@dataclass
class ToolSpec:
    """単一ツールの関数実体と論理メタデータを保持するデータクラス（物理パス非保持）。"""

    func: Callable
    name: str
    summary: str = ""
    toolset: str = "default"

    @classmethod
    def from_callable(
        cls,
        func: Callable,
        toolset: str = "default",
        summary: str | None = None,
    ) -> "ToolSpec":
        fname = getattr(func, "__name__", "").strip()
        if not fname:
            raise ValueError("ツール関数には有効な __name__ 属性が必要です。")
        resolved_summary = (
            summary.strip() if summary is not None else extract_tool_summary(func)
        )
        return cls(
            func=func,
            name=fname,
            summary=resolved_summary,
            toolset=toolset,
        )


@dataclass
class Toolset:
    """関連する複数ツールを論理グループ化したデータクラス。"""

    name: str
    description: str
    tools: list[ToolSpec] = field(default_factory=list)
    always_active: bool = False

    @classmethod
    def from_callables(
        cls,
        name: str,
        description: str,
        funcs: list[Callable],
        *,
        always_active: bool = False,
    ) -> "Toolset":
        specs = [ToolSpec.from_callable(f, toolset=name) for f in funcs]
        return cls(
            name=name,
            description=description.strip(),
            tools=specs,
            always_active=always_active,
        )


class ToolRegistry:
    """
    利用可能なすべての Toolset および ToolSpec を一元管理するレジストリクラス。
    """

    def __init__(self) -> None:
        self._toolsets: dict[str, Toolset] = {}
        self._tools_by_name: dict[str, ToolSpec] = {}

    def register_toolset(self, toolset: Toolset) -> None:
        """ツールセットを登録します（同名ツールセットがある場合は更新します）。"""
        if toolset.name in self._toolsets:
            old_ts = self._toolsets[toolset.name]
            for old_spec in old_ts.tools:
                if (
                    old_spec.name in self._tools_by_name
                    and self._tools_by_name[old_spec.name].toolset == toolset.name
                ):
                    del self._tools_by_name[old_spec.name]

        self._toolsets[toolset.name] = toolset
        for spec in toolset.tools:
            spec.toolset = toolset.name
            self._tools_by_name[spec.name] = spec

    def register_functions(
        self,
        funcs: list[Callable],
        toolset_name: str = "default",
        description: str = "標準ツールセット",
        *,
        always_active: bool = False,
    ) -> Toolset:
        """関数リストから Toolset を構築して登録します。"""
        ts = Toolset.from_callables(
            name=toolset_name,
            description=description,
            funcs=funcs,
            always_active=always_active,
        )
        self.register_toolset(ts)
        return ts

    def get_tool(self, name: str) -> ToolSpec | None:
        """ツール名から ToolSpec を取得します。"""
        return self._tools_by_name.get(name)

    def get_toolset(self, name: str) -> Toolset | None:
        """ツールセット名から Toolset を取得します。"""
        return self._toolsets.get(name)

    def list_toolsets(self) -> list[Toolset]:
        """登録されている全 Toolset のリストを返します。"""
        return list(self._toolsets.values())

    def list_tools(self) -> list[ToolSpec]:
        """登録されている全 ToolSpec のリストを返します。"""
        return list(self._tools_by_name.values())


class ToolProvider(ABC):
    """
    ChatAgent の各ループイテレーションに対して「現在有効なツール一覧」と「カタログ説明」を提供し、
    ツール呼び出しを安全に解決・実行する抽象基底クラス。
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry: ToolRegistry = registry or ToolRegistry()
        self._extra_tools: dict[str, Callable] = {}

    def register_extra_tools(self, funcs: list[Callable]) -> None:
        """
        SkillManager のメタツール（activate_skill 等）のように、プロバイダ種別を問わず
        常時公開するシステムメタツールを追加登録します。
        """
        for fn in funcs:
            fname = getattr(fn, "__name__", "").strip()
            if fname:
                self._extra_tools[fname] = fn

    @abstractmethod
    def get_active_tools(self) -> list[Callable]:
        """現在のイテレーションで Gemini API の tools に渡す有効な関数リストを返します。"""

    def get_catalog_prompt(self) -> str:
        """システムプロンプトへ付加するツールカタログ文字列を返します（標準では空文字列）。"""
        return ""

    def _build_unavailable_tool_message(self, name: str) -> str:
        """未知または未有効なツールが呼び出された際の案内メッセージを構築します。"""
        if self.registry.get_tool(name) is not None:
            return (
                f"[ERROR] ツール '{name}' は現在有効化されていません。"
                "使用する前にツール要求を行って有効化してください。"
            )
        return (
            f"[ERROR] ツール '{name}' は存在しないか、現在有効化されていません。"
            "利用可能なツール名を確認してください。"
        )

    def execute(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        executor: Callable | None = None,
    ) -> Any:
        """
        現在有効なツールの中から `name` を検索して実行します。
        未登録または未有効なツールが指定された場合はクラッシュさせずエラー案内文字列を返します。
        """
        active_map = {
            getattr(fn, "__name__", ""): fn for fn in self.get_active_tools()
        }
        target_fn = active_map.get(name)
        if target_fn is None:
            return self._build_unavailable_tool_message(name)

        call_args = args or {}
        if executor is not None:
            return executor(target_fn, **call_args)
        return target_fn(**call_args)


class StaticToolProvider(ToolProvider):
    """
    登録されているすべてのツールを常時有効状態で提供する標準プロバイダクラス。
    """

    @classmethod
    def from_functions(cls, funcs: list[Callable]) -> "StaticToolProvider":
        registry = ToolRegistry()
        if funcs:
            registry.register_functions(
                funcs,
                toolset_name="default",
                description="登録ツール群",
                always_active=True,
            )
        return cls(registry=registry)

    def get_active_tools(self) -> list[Callable]:
        seen: set[str] = set()
        active: list[Callable] = []

        for spec in self.registry.list_tools():
            if spec.name not in seen:
                seen.add(spec.name)
                active.append(spec.func)

        for fname, fn in self._extra_tools.items():
            if fname not in seen:
                seen.add(fname)
                active.append(fn)

        return active
