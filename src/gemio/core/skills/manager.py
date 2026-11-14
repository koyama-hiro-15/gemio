"""
Skills のカタログ生成・オンデマンド有効化/無効化・メタツール提供を担う SkillManager モジュール。
"""

from typing import Callable

from gemio.core.skills.loader import SkillSpec


class SkillManager:
    """
    登録された SkillSpec 群を管理し、段階的開示（Progressive Disclosure）方式で
    システムプロンプト用のカタログおよび有効化済みスキル手順を提供するクラス。
    """

    def __init__(
        self,
        skills: list[SkillSpec] | None = None,
        on_skill_activated: Callable[[SkillSpec], None] | None = None,
    ) -> None:
        self._skills: dict[str, SkillSpec] = {}
        self._active_bodies: dict[str, str] = {}
        self.on_skill_activated = on_skill_activated

        for spec in skills or []:
            self.register(spec)

    def register(self, skill: SkillSpec) -> None:
        """スキル定義を登録します（同名がある場合は上書きします）。"""
        self._skills[skill.name] = skill

    def list_skills(self) -> list[SkillSpec]:
        """登録されているすべての SkillSpec のリストを返します。"""
        return list(self._skills.values())

    @property
    def has_skills(self) -> bool:
        """登録されているスキルが1件以上存在するかどうかを返します。"""
        return bool(self._skills)

    @property
    def active_skill_names(self) -> list[str]:
        """現在有効化されているスキル名のリストを返します。"""
        return list(self._active_bodies.keys())

    def is_active(self, skill_name: str) -> bool:
        """指定されたスキルが現在有効化されているかどうかを返します。"""
        return skill_name.strip() in self._active_bodies

    def activate(self, skill_name: str) -> str:
        """
        指定されたスキルをオンデマンドで読み込み、有効化します。
        失敗時も物理パスを含まない安全なメッセージを返します。
        """
        clean_name = skill_name.strip()
        spec = self._skills.get(clean_name)
        if spec is None:
            available = ", ".join(self._skills.keys()) if self._skills else "なし"
            return (
                f"[ERROR] スキル '{clean_name}' は見つかりません。"
                f"利用可能なスキル: {available}"
            )

        if clean_name in self._active_bodies:
            return f"スキル '{clean_name}' はすでに有効化されています。"

        try:
            body = spec.load_body()
        except Exception:
            return f"[ERROR] スキル '{clean_name}' の読み込みに失敗しました。"

        self._active_bodies[clean_name] = body
        if self.on_skill_activated is not None:
            self.on_skill_activated(spec)

        rec_note = ""
        if spec.recommended_toolsets:
            joined_ts = ", ".join(spec.recommended_toolsets)
            rec_note = f"（推奨ツールセット: {joined_ts}）"

        return (
            f"成功: スキル '{clean_name}' を有効化しました{rec_note}。"
            "システムインストラクションの【有効化中のSkills】セクションに詳細手順が追加されました。"
        )

    def deactivate(self, skill_name: str) -> bool:
        """指定されたスキルの有効化を解除します。"""
        clean_name = skill_name.strip()
        if clean_name in self._active_bodies:
            del self._active_bodies[clean_name]
            return True
        return False

    def clear_active(self) -> None:
        """有効化されているすべてのスキルを解除して初期状態に戻します。"""
        self._active_bodies.clear()

    def build_catalog_prompt(self) -> str:
        """
        利用可能なスキル一覧（名称と1行説明のみ）のシステムプロンプトセクションを構築します。
        登録スキルが0件の場合は空文字列を返します。
        """
        if not self._skills:
            return ""

        lines = [
            "【利用可能なSkills（専門手順・ワークフロー）】",
            "- 以下の専門スキルが利用可能です。タスクに関連するスキルがある場合は、作業開始前に `activate_skill` ツールを呼び出して詳細手順を有効化してください。",
        ]
        for spec in self._skills.values():
            status_mark = " [有効化済み]" if spec.name in self._active_bodies else ""
            lines.append(f"  - `{spec.name}`{status_mark}: {spec.description}")
        return "\n".join(lines)

    def build_active_skills_prompt(self) -> str:
        """
        現在有効化されているスキルの詳細手順（Markdown本文）を結合したシステムプロンプトセクションを構築します。
        有効化中のスキルが0件の場合は空文字列を返します。
        """
        if not self._active_bodies:
            return ""

        blocks = ["【有効化中のSkills（適用手順）】"]
        for name, body in self._active_bodies.items():
            blocks.append(f"--- [Skill: {name}] ---\n{body.strip()}")
        return "\n\n".join(blocks)

    def get_meta_tools(self) -> list[Callable]:
        """
        AIが自律的にスキルを有効化するためのメタツールリストを返します。
        登録スキルが0件の場合は不要なスキーマトークン消費を防ぐため空リストを返します。
        """
        if not self._skills:
            return []

        def activate_skill(skill_name: str) -> str:
            """
            指定された専門スキル（Skill）を有効化し、詳細な作業手順や規約をシステムインストラクションへ読み込みます。

            Args:
                skill_name: 有効化するスキル名（【利用可能なSkills】に記載された名前）。

            Returns:
                有効化結果のステータスメッセージ。
            """
            return self.activate(skill_name)

        return [activate_skill]
