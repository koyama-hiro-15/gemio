"""
[Experimental] AIからのツール要求および動的ツールセット組み合わせ（DynamicToolsetProvider）モジュール。

初期状態では最小限の常駐ツールと `request_tools` メタツールのみをAPIスキーマに渡し、
システムプロンプト上の軽量カタログを通じてAIが必要なツールセット・個別ツールを動的に要求・有効化できるようにします。
"""

from typing import Callable

from gemio.core.tools.registry import ToolProvider, ToolRegistry


class DynamicToolsetProvider(ToolProvider):
    """
    ツールセットのオンデマンド有効化・動的組み合わせを行う実験的 ToolProvider 実装クラス。
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        initial_active_toolsets: list[str] | None = None,
        initial_active_tools: list[str] | None = None,
    ) -> None:
        super().__init__(registry=registry)
        self._initial_toolsets: set[str] = set(initial_active_toolsets or [])
        self._initial_tools: set[str] = set(initial_active_tools or [])
        self._active_toolsets: set[str] = set(self._initial_toolsets)
        self._active_tools: set[str] = set(self._initial_tools)
        self._request_tool_fn = self._create_request_tools_callable()

    def _has_on_demand_tools(self) -> bool:
        """常時有効（always_active=True）以外のオンデマンドツールが存在するかを返します。"""
        return any(not ts.always_active for ts in self.registry.list_toolsets())

    def _is_toolset_active(self, toolset_name: str) -> bool:
        ts = self.registry.get_toolset(toolset_name)
        if ts is None:
            return False
        return ts.always_active or (toolset_name in self._active_toolsets)

    def _is_spec_active(self, tool_name: str, toolset_name: str) -> bool:
        return self._is_toolset_active(toolset_name) or (
            tool_name in self._active_tools
        )

    def activate_toolsets(self, toolset_names: list[str]) -> list[str]:
        """指定されたツールセット名リストを有効化し、有効化されたツール名のリストを返します。"""
        activated_tools: list[str] = []
        for raw_name in toolset_names:
            clean_name = raw_name.strip()
            ts = self.registry.get_toolset(clean_name)
            if ts is not None:
                self._active_toolsets.add(ts.name)
                for spec in ts.tools:
                    if spec.name not in activated_tools:
                        activated_tools.append(spec.name)
        return activated_tools

    def reset_active_tools(self) -> None:
        """動的に有効化されたツール状態を初期状態へリセットします。"""
        self._active_toolsets = set(self._initial_toolsets)
        self._active_tools = set(self._initial_tools)

    def _create_request_tools_callable(self) -> Callable:
        def request_tools(
            toolsets: list[str] | None = None,
            tool_names: list[str] | None = None,
        ) -> str:
            """
            作業に必要なツールセット（toolsets）または個別ツール（tool_names）を動的に有効化します。
            【動的ツールセットカタログ】で [要リクエスト] となっているツールを使用する前に呼び出してください。

            Args:
                toolsets: 有効化したいツールセット名のリスト（例: ["project_explore", "project_read"]）。
                tool_names: 個別に有効化したいツール関数名のリスト（例: ["write_file"]）。

            Returns:
                有効化されたツール一覧またはエラー案内メッセージ。
            """
            req_ts = [s.strip() for s in (toolsets or []) if isinstance(s, str) and s.strip()]
            req_fn = [s.strip() for s in (tool_names or []) if isinstance(s, str) and s.strip()]

            if not req_ts and not req_fn:
                available_ts = [
                    ts.name for ts in self.registry.list_toolsets() if not ts.always_active
                ]
                return (
                    "[ERROR] 有効化する `toolsets` または `tool_names` を1つ以上指定してください。"
                    f"利用可能なツールセット: {', '.join(available_ts) if available_ts else 'なし'}"
                )

            newly_enabled: list[str] = []
            unknown_targets: list[str] = []

            for ts_name in req_ts:
                ts = self.registry.get_toolset(ts_name)
                if ts is None:
                    unknown_targets.append(f"toolset:{ts_name}")
                    continue
                self._active_toolsets.add(ts.name)
                for spec in ts.tools:
                    if spec.name not in newly_enabled:
                        newly_enabled.append(spec.name)

            for fname in req_fn:
                spec = self.registry.get_tool(fname)
                if spec is None:
                    unknown_targets.append(f"tool:{fname}")
                    continue
                self._active_tools.add(spec.name)
                if spec.name not in newly_enabled:
                    newly_enabled.append(spec.name)

            messages: list[str] = []
            if newly_enabled:
                messages.append(
                    f"成功: 以下のツールを有効化しました: {', '.join(newly_enabled)}。"
                    "次のステップから直接呼び出し可能です。"
                )
            if unknown_targets:
                available_ts = [ts.name for ts in self.registry.list_toolsets()]
                messages.append(
                    f"未登録の指定をスキップしました: {', '.join(unknown_targets)} "
                    f"（登録済みツールセット: {', '.join(available_ts)}）"
                )
            return "\n".join(messages)

        return request_tools

    def _build_unavailable_tool_message(self, name: str) -> str:
        spec = self.registry.get_tool(name)
        if spec is not None:
            return (
                f"[ERROR] ツール '{name}'（所属ツールセット: '{spec.toolset}'）はまだ有効化されていません。"
                f"先に request_tools(toolsets=['{spec.toolset}']) または "
                f"request_tools(tool_names=['{name}']) を呼び出して有効化してください。"
            )
        return super()._build_unavailable_tool_message(name)

    def get_active_tools(self) -> list[Callable]:
        seen: set[str] = set()
        active: list[Callable] = []

        if self._has_on_demand_tools():
            seen.add(self._request_tool_fn.__name__)
            active.append(self._request_tool_fn)

        for fname, fn in self._extra_tools.items():
            if fname not in seen:
                seen.add(fname)
                active.append(fn)

        for ts in self.registry.list_toolsets():
            ts_active = ts.always_active or (ts.name in self._active_toolsets)
            for spec in ts.tools:
                if ts_active or (spec.name in self._active_tools):
                    if spec.name not in seen:
                        seen.add(spec.name)
                        active.append(spec.func)

        return active

    def get_catalog_prompt(self) -> str:
        if not self._has_on_demand_tools():
            return ""

        lines = [
            "【動的ツールセットカタログ（Experimental）】",
            "- コンテキスト効率化のため、一部のツールセットは初期状態で未有効（`[要リクエスト]`）になっています。",
            "- `[要リクエスト]` のツールを使用する必要がある場合は、先に `request_tools(toolsets=[...], tool_names=[...])` を呼び出して有効化してください。有効化すると直後のステップから利用可能になります。",
            "- 登録ツールセット一覧:",
        ]

        for ts in self.registry.list_toolsets():
            if ts.always_active:
                status = "[常時有効]"
            elif ts.name in self._active_toolsets:
                status = "[有効化済み]"
            else:
                status = "[要リクエスト]"

            lines.append(f"  * `{ts.name}` {status}: {ts.description}")
            for spec in ts.tools:
                indiv_mark = (
                    " (個別有効化済み)"
                    if (not self._is_toolset_active(ts.name) and spec.name in self._active_tools)
                    else ""
                )
                lines.append(f"    - `{spec.name}`{indiv_mark}: {spec.summary}")

        return "\n".join(lines)
