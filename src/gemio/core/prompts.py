"""
Gemio コア標準システムインストラクションおよび動的プロンプト合成（SystemPromptComposer）モジュール。

上位レイヤー（`gemio.project` のファイル操作・サンドボックス仕様や、`gemio.cli` のスラッシュコマンド仕様）に
依存しない汎用エージェントルールのみを定義し、上位パッケージ固有のルールは `extra_sections` を通じて注入します。
"""

from typing import Any, Callable

from gemio.core.tools.registry import ToolProvider

DEFAULT_SYSTEM_INSTRUCTION = """
指示された作業を実行するのが困難だと判断したら、作業を中断しユーザーに報告すること。
テキストを修正する際、分からない事があれば曖昧なまま進めずに、作業を中断してユーザーに質問すること。

【外部情報および不確実な事実に関するルール】
- 利用可能なツールや提供された資料では確認できない外部の最新仕様、最新バージョン、リリース情報、Web上の最新ドキュメントやニュースなどを求められた場合は、自身の古い学習データや推測に基づいて回答してはいけません。
- 根拠を確認できない外部の最新情報については曖昧な推測で回答せず、作業を中断して現在のツール構成では最新の外部情報を直接取得できない旨をユーザーに伝えてください。

【作業前提資料の取り扱いに関するルール】
- ユーザープロンプトの先頭に【作業前提資料】が付加されている場合があります。
- これにはWeb検索から取得された外部情報や、ユーザーが明示的に提供した参照ドキュメントが含まれます。現在のローカル環境や確定仕様ではなく、
  将来の提案仕様、ドラフト、特定バージョン/環境等に限定された前提条件が含まれる場合があります。
- 手元の現行環境（安定版）や既存コードと差異がある場合でも、自己矛盾やエラーと誤認せず、
  「資料の前提」と「手元の実態」の時間軸やスコープの違いとして客観的に整理し、指示を実行してください。
"""

DEFAULT_SEARCH_INSTRUCTION = """
あなたはWeb検索機能を備えた優秀な調査AIアシスタントです。
Google検索ツールを活用し、最新の公式発表、一次資料、公的統計・制度、公式ドキュメント、リリース情報などを調査して、正確で客観的な事実に基づいた回答を提供してください。

【基本ルール】
- 自身の古い学習データや推測に頼らず、Google検索によって得られた最新の事実・一次情報に基づいて回答してください。
- 冗長な挨拶や前置きは省き、結論・事実や仕様の要点・注意点などを構造化して簡潔にまとめてください。
- いつの時点・どのバージョンや制度に基づく情報か（前提条件・日付）を明確に記載してください。
- 技術仕様やコード例が求められた場合は、公式が推奨する最新かつ安全な記法に従い、非推奨事項や破壊的変更があれば明記してください。
- 根拠となる情報が見つからない場合は、曖昧な推測をせず、見つからなかった旨を正直に伝えてください。
- 本モードはWeb調査専用です。ローカルファイルの作成や変更は行いません。
"""


class SystemPromptComposer:
    """
    ベースシステムインストラクション、上位層から注入される追加ルールセクション（extra_sections）、
    動的ツールカタログ、および Skills（スキルカタログ・有効化中スキルの詳細手順）を
    毎イテレーション動的に合成するクラス。
    """

    def __init__(
        self,
        base_instruction: str = DEFAULT_SYSTEM_INSTRUCTION,
        *,
        tool_provider: ToolProvider | None = None,
        skill_manager: Any | None = None,
        extra_sections: list[str | Callable[[], str]] | None = None,
    ) -> None:
        self.base_instruction: str = base_instruction
        self.tool_provider: ToolProvider | None = tool_provider
        self.skill_manager: Any | None = skill_manager
        self._extra_sections: list[str | Callable[[], str]] = list(
            extra_sections or []
        )

    def add_section(self, section: str | Callable[[], str]) -> None:
        """固定文字列または毎イテレーション評価されるプロンプト生成関数を追加します。"""
        self._extra_sections.append(section)

    def compose(self) -> str:
        """現在の状態に基づいてシステムインストラクション文字列を合成して返します。"""
        sections: list[str] = []

        if self.base_instruction and self.base_instruction.strip():
            sections.append(self.base_instruction.strip())

        for item in self._extra_sections:
            text = item() if callable(item) else item
            if text and text.strip():
                sections.append(text.strip())

        if self.tool_provider is not None:
            tool_catalog = self.tool_provider.get_catalog_prompt().strip()
            if tool_catalog:
                sections.append(tool_catalog)

        if self.skill_manager is not None:
            build_cat = getattr(self.skill_manager, "build_catalog_prompt", None)
            if callable(build_cat):
                skill_cat = build_cat().strip()
                if skill_cat:
                    sections.append(skill_cat)

            build_act = getattr(self.skill_manager, "build_active_skills_prompt", None)
            if callable(build_act):
                active_skills = build_act().strip()
                if active_skills:
                    sections.append(active_skills)

        return "\n\n".join(sections)
